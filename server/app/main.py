from __future__ import annotations

import asyncio
import io
import json
import os
import zipfile
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .auth import current_user, login_response, logout_response, require_user, user_from_session_token, verify_credentials
from .config import Settings, get_settings
from .db import Database
from .markitdowner import markdown_filename, run_markitdown, stored_markitdown_name
from .transcriber import worker_loop
from .uploads import (
    assemble_upload,
    chunk_path,
    clean_filename,
    ensure_completed_size,
    save_chunk,
    save_upload_with_limit,
    uploaded_indices,
    validate_markitdown_file,
    validate_video,
)


settings = get_settings()
db = Database(settings.database_path)
templates = Jinja2Templates(directory="app/templates")
app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"chrome-extension://.*",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Transcript-Session", "X-Transcript-Recorder"],
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
stop_event: asyncio.Event | None = None
worker_tasks: list[asyncio.Task] = []


def login_required(request: Request, settings: Settings = Depends(get_settings)) -> str:
    try:
        return require_user(request, settings)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Necesitas iniciar sesion")


def require_extension_session(
    x_transcript_session: str | None = Header(default=None, alias="X-Transcript-Session"),
) -> str:
    if not x_transcript_session:
        raise HTTPException(status_code=401, detail="Sin sesion activa")
    user = user_from_session_token(settings, x_transcript_session)
    if not user:
        raise HTTPException(status_code=401, detail="Sesion invalida o vencida")
    return user


def render(request: Request, template: str, context: dict | None = None) -> HTMLResponse:
    payload = {
        "request": request,
        "settings": settings,
        "user": current_user(request, settings),
    }
    if context:
        payload.update(context)
    return templates.TemplateResponse(template, payload)


def remove_inside_storage(path_value: str | None) -> None:
    if not path_value:
        return
    path = Path(path_value)
    try:
        resolved_path = path.resolve()
        storage_root = settings.storage_dir.resolve()
        if resolved_path == storage_root or storage_root not in resolved_path.parents:
            return
        if resolved_path.is_dir():
            shutil.rmtree(resolved_path, ignore_errors=True)
        else:
            resolved_path.unlink(missing_ok=True)
    except OSError:
        return


def cleanup_job_files(row: object) -> None:
    for key in ("video_path", "txt_path", "srt_path"):
        remove_inside_storage(row[key])
    if row["upload_id"]:
        remove_inside_storage(str(settings.chunks_dir / row["upload_id"]))


_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.file",
]


def _google_service_creds():
    # Import diferido: la integracion es opcional y viene apagada por defecto.
    from google.oauth2 import service_account

    key_path = Path(settings.google_service_account_key)
    if not key_path.exists():
        return None
    try:
        return service_account.Credentials.from_service_account_file(str(key_path), scopes=_GOOGLE_SCOPES)
    except Exception:
        return None


def cleanup_markitdown_files(row: object) -> None:
    for key in ("input_path", "md_path"):
        remove_inside_storage(row[key])


def process_markitdown_job(job_id: str) -> None:
    try:
        run_markitdown(settings, db, job_id)
    except Exception as exc:
        db.update_markitdown_job(
            job_id,
            status="failed",
            progress=0,
            message="Error",
            error=str(exc),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )


def recording_original_name(filename: str | None, title: str | None) -> str:
    raw_filename = filename or "grabacion-chrome.webm"
    ext = Path(raw_filename).suffix.lower()
    base = clean_filename(title or Path(raw_filename).stem)
    return clean_filename(f"{base}{ext or '.webm'}")


@app.on_event("startup")
async def startup() -> None:
    global stop_event, worker_tasks
    for folder in (
        settings.uploads_dir,
        settings.chunks_dir,
        settings.outputs_dir,
        settings.tmp_dir,
        settings.markitdown_uploads_dir,
        settings.markitdown_outputs_dir,
    ):
        folder.mkdir(parents=True, exist_ok=True)
    db.init()
    stop_event = asyncio.Event()
    worker_tasks = [
        asyncio.create_task(worker_loop(settings, db, stop_event))
        for _ in range(max(1, settings.job_concurrency))
    ]
    worker_tasks.append(asyncio.create_task(retention_loop()))


@app.on_event("shutdown")
async def shutdown() -> None:
    if stop_event is not None:
        stop_event.set()
    for task in worker_tasks:
        task.cancel()


async def retention_loop() -> None:
    while stop_event is not None and not stop_event.is_set():
        if settings.retention_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
            for row in db.list_jobs(limit=1000):
                if row["status"] not in {"done", "failed"} or not row["finished_at"]:
                    continue
                try:
                    finished = datetime.fromisoformat(row["finished_at"])
                except ValueError:
                    continue
                if finished <= cutoff and row["video_path"]:
                    video_path = Path(row["video_path"])
                    if video_path.exists():
                        video_path.unlink(missing_ok=True)
                        db.update_job(row["id"], message="Video original eliminado por retencion")
        await asyncio.sleep(60 * 60)


EXTENSION_DIR = Path(os.environ.get("EXTENSION_DIR", "/extension"))


@app.get("/extension/transcrip-recorder.zip", include_in_schema=False)
async def extension_zip() -> Response:
    # Chrome bloquea los .crx fuera de la Web Store (CRX_REQUIRED_PROOF_MISSING):
    # se distribuye como zip para "Cargar descomprimida". Se arma desde el codigo
    # del repo (montado read-only), nunca desde un binario versionado.
    if not (EXTENSION_DIR / "manifest.json").is_file():
        raise HTTPException(status_code=404, detail="Extension no disponible")
    # La URL publica (SUITE_PUBLIC_URL) se inyecta en el manifest y en background.js,
    # asi la extension apunta a este servidor sin tocar codigo.
    origin = settings.public_origin
    match = f"{settings.public_scheme}://{settings.public_host}/*"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(EXTENSION_DIR.rglob("*")):
            if not f.is_file() or f.suffix == ".pem":
                continue
            arcname = Path("transcrip-recorder") / f.relative_to(EXTENSION_DIR)
            if f.name == "manifest.json":
                manifest = json.loads(f.read_text(encoding="utf-8"))
                manifest["host_permissions"] = [match]
                for script in manifest.get("content_scripts", []):
                    script["matches"] = [match]
                zf.writestr(str(arcname), json.dumps(manifest, indent=2, ensure_ascii=False))
            elif f.name == "background.js":
                code = f.read_text(encoding="utf-8").replace(
                    'const SERVER_URL = "http://localhost:8000";', f"const SERVER_URL = {json.dumps(origin)};"
                )
                zf.writestr(str(arcname), code)
            else:
                zf.write(f, arcname)
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="transcrip-recorder.zip"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/home", status_code=303)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if current_user(request, settings):
        return RedirectResponse(url="/home", status_code=303)
    return render(request, "login.html")


@app.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if verify_credentials(settings, username, password):
        return login_response(settings, username)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "settings": settings, "user": None, "error": "Usuario o clave incorrectos"},
        status_code=401,
    )


@app.post("/logout")
async def logout() -> RedirectResponse:
    return logout_response()


@app.get("/home", response_class=HTMLResponse)
async def home(request: Request):
    if not current_user(request, settings):
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "home.html")


@app.get("/transcrip", response_class=HTMLResponse)
async def transcrip(request: Request):
    if not current_user(request, settings):
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "transcrip.html", {"jobs": db.list_jobs()})


@app.get("/markitdown", response_class=HTMLResponse)
async def markitdown(request: Request):
    if not current_user(request, settings):
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "markitdown.html")


@app.get("/api/jobs/{job_id}/gdoc")
async def create_gdoc(job_id: str, _: str = Depends(login_required)) -> JSONResponse:
    if not settings.google_docs_enabled:
        raise HTTPException(status_code=404, detail="Integracion con Google Docs deshabilitada")
    from googleapiclient.discovery import build as google_build

    row = db.get_job(job_id)
    if row is None or row["status"] != "done":
        raise HTTPException(status_code=404, detail="Trabajo no disponible")
    srt_path_value = row["srt_path"]
    if not srt_path_value or not Path(srt_path_value).exists():
        raise HTTPException(status_code=404, detail="Archivo SRT no encontrado")

    creds = _google_service_creds()
    if creds is None:
        raise HTTPException(status_code=503, detail="Google API no configurado en el servidor")

    srt_content = Path(srt_path_value).read_text(encoding="utf-8")
    title = f"{Path(row['original_name']).stem} — Transcripción"

    try:
        docs = google_build("docs", "v1", credentials=creds)
        doc = docs.documents().create(body={"title": title}).execute()
        doc_id = doc["documentId"]
        docs.documents().batchUpdate(
            documentId=doc_id,
            body={"requests": [{"insertText": {"location": {"index": 1}, "text": srt_content}}]},
        ).execute()
        drive = google_build("drive", "v3", credentials=creds)
        drive.permissions().create(
            fileId=doc_id,
            body={"type": "anyone", "role": "writer"},
            fields="id",
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al crear Google Doc: {exc}") from exc

    return JSONResponse({"url": f"https://docs.google.com/document/d/{doc_id}/edit"})


@app.get("/api/uploads/{upload_id}")
async def upload_status(upload_id: str, _: str = Depends(login_required)) -> dict:
    return {"upload_id": upload_id, "chunks": sorted(uploaded_indices(settings, upload_id))}


@app.post("/api/uploads/chunk")
async def upload_chunk(
    _: str = Depends(login_required),
    file: UploadFile = File(...),
    upload_id: str = Form(...),
    filename: str = Form(...),
    chunk_index: int = Form(...),
    chunk_count: int = Form(...),
    total_size: int = Form(...),
) -> dict:
    validate_video(settings, filename, total_size)
    if chunk_index < 0 or chunk_index >= chunk_count:
        raise HTTPException(status_code=400, detail="Bloque invalido")
    target = chunk_path(settings, upload_id, chunk_index)
    written = await save_chunk(file, target)
    return {"ok": True, "chunk_index": chunk_index, "bytes": written}


@app.post("/api/uploads/complete")
async def complete_upload(
    _: str = Depends(login_required),
    upload_id: str = Form(...),
    filename: str = Form(...),
    chunk_count: int = Form(...),
    total_size: int = Form(...),
) -> dict:
    ext = validate_video(settings, filename, total_size)
    have = uploaded_indices(settings, upload_id)
    expected = set(range(chunk_count))
    missing = sorted(expected - have)
    if missing:
        raise HTTPException(status_code=409, detail={"missing": missing})

    original_name = clean_filename(filename)
    video_path, stored_name = assemble_upload(settings, upload_id, original_name, chunk_count)
    ensure_completed_size(video_path, total_size)
    job_id = uuid.uuid4().hex
    db.create_job(
        id=job_id,
        original_name=original_name,
        stored_name=stored_name,
        extension=ext,
        size_bytes=total_size,
        status="queued",
        progress=0,
        message="En cola",
        upload_id=upload_id,
        video_path=str(video_path),
    )
    return {"ok": True, "job_id": job_id}


@app.get("/api/extension/session")
async def extension_session(_: str = Depends(require_extension_session)) -> dict:
    return {"ok": True}


@app.post("/api/extension/recordings")
async def extension_recording(
    _: str = Depends(require_extension_session),
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    recorded_at: str | None = Form(default=None),
) -> dict:
    original_name = recording_original_name(file.filename, title)
    ext = validate_video(settings, original_name, 1)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    target = settings.uploads_dir / stored_name
    written = await save_upload_with_limit(file, target, settings.max_upload_bytes)
    validate_video(settings, original_name, written)

    job_id = uuid.uuid4().hex
    message_parts = ["En cola desde Chrome"]
    if recorded_at:
        message_parts.append(recorded_at)
    if source_url:
        message_parts.append("pestana capturada")
    db.create_job(
        id=job_id,
        original_name=original_name,
        stored_name=stored_name,
        extension=ext,
        size_bytes=written,
        status="queued",
        progress=0,
        message=" - ".join(message_parts),
        upload_id=None,
        video_path=str(target),
    )
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.get("/api/jobs")
async def jobs(_: str = Depends(login_required)) -> list[dict]:
    return [dict(row) for row in db.list_jobs()]


@app.get("/api/markitdown/jobs")
async def markitdown_jobs(_: str = Depends(login_required)) -> list[dict]:
    return [dict(row) for row in db.list_markitdown_jobs()]


@app.post("/api/markitdown/jobs")
async def create_markitdown_job(
    background_tasks: BackgroundTasks,
    _: str = Depends(login_required),
    file: UploadFile = File(...),
) -> dict:
    original_name = clean_filename(file.filename or "archivo")
    ext = validate_markitdown_file(settings, original_name, 1)
    stored_name = stored_markitdown_name(original_name)
    target = settings.markitdown_uploads_dir / stored_name
    written = await save_upload_with_limit(file, target, settings.markitdown_max_upload_bytes)
    validate_markitdown_file(settings, original_name, written)

    job_id = uuid.uuid4().hex
    db.create_markitdown_job(
        id=job_id,
        original_name=original_name,
        stored_name=stored_name,
        extension=ext,
        size_bytes=written,
        status="queued",
        progress=0,
        message="En cola",
        input_path=str(target),
    )
    background_tasks.add_task(process_markitdown_job, job_id)
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str, _: str = Depends(login_required)) -> dict:
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    cleanup_job_files(row)
    db.delete_job(job_id)
    return {"ok": True, "job_id": job_id}


@app.delete("/api/markitdown/jobs/{job_id}")
async def delete_markitdown_job(job_id: str, _: str = Depends(login_required)) -> dict:
    row = db.get_markitdown_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    cleanup_markitdown_files(row)
    db.delete_markitdown_job(job_id)
    return {"ok": True, "job_id": job_id}


@app.get("/download/markitdown/{job_id}")
async def download_markitdown(job_id: str, _: str = Depends(login_required)) -> FileResponse:
    row = db.get_markitdown_job(job_id)
    if row is None or row["status"] != "done":
        raise HTTPException(status_code=404, detail="Trabajo no disponible")
    path_value = row["md_path"]
    if not path_value:
        raise HTTPException(status_code=404, detail="Archivo no generado")
    path = Path(path_value)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    return FileResponse(path, filename=markdown_filename(row["original_name"]), media_type="text/markdown")


@app.get("/download/{job_id}/{kind}")
async def download(job_id: str, kind: str, _: str = Depends(login_required)) -> FileResponse:
    if kind not in {"txt", "srt"}:
        raise HTTPException(status_code=404, detail="Archivo no encontrado")
    row = db.get_job(job_id)
    if row is None or row["status"] != "done":
        raise HTTPException(status_code=404, detail="Trabajo no disponible")
    path_value = row[f"{kind}_path"]
    if not path_value:
        raise HTTPException(status_code=404, detail="Archivo no generado")
    path = Path(path_value)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Archivo no encontrado en disco")
    stem = Path(row["original_name"]).stem
    return FileResponse(path, filename=f"{stem}.{kind}", media_type="text/plain")


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True})
