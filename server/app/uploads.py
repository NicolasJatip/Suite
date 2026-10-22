from __future__ import annotations

import os
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .config import Settings


SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def clean_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "video"
    name = SAFE_NAME_RE.sub("_", name)
    return name[:180]


def extension_for(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def validate_video(settings: Settings, filename: str, total_size: int) -> str:
    ext = extension_for(filename)
    if ext not in settings.allowed_extensions:
        allowed = ", ".join(sorted(settings.allowed_extensions))
        raise HTTPException(status_code=400, detail=f"Formato no permitido. Usar: {allowed}")
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="Tamano total invalido")
    if total_size > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail=f"El archivo supera {settings.max_upload_gb} GB")
    return ext


def validate_markitdown_file(settings: Settings, filename: str, total_size: int) -> str:
    ext = extension_for(filename)
    if ext not in settings.markitdown_allowed_extensions:
        allowed = ", ".join(sorted(settings.markitdown_allowed_extensions))
        raise HTTPException(status_code=400, detail=f"Formato no permitido. Usar: {allowed}")
    if total_size <= 0:
        raise HTTPException(status_code=400, detail="Tamano total invalido")
    if total_size > settings.markitdown_max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"El archivo supera {settings.markitdown_max_upload_mb} MB",
        )
    return ext


async def save_upload_with_limit(upload_file: UploadFile, target: Path, max_bytes: int) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = await upload_file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="El archivo supera el tamano maximo permitido")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return written


async def save_chunk(upload_file: UploadFile, target: Path) -> int:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("wb") as out:
        while True:
            chunk = await upload_file.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
            written += len(chunk)
    return written


def chunk_path(settings: Settings, upload_id: str, index: int) -> Path:
    return settings.chunks_dir / upload_id / f"{index:08d}.part"


def uploaded_indices(settings: Settings, upload_id: str) -> set[int]:
    folder = settings.chunks_dir / upload_id
    if not folder.exists():
        return set()
    return {int(path.stem) for path in folder.glob("*.part") if path.stem.isdigit()}


def assemble_upload(settings: Settings, upload_id: str, filename: str, chunk_count: int) -> tuple[Path, str]:
    ext = extension_for(filename)
    stored_name = f"{uuid.uuid4().hex}.{ext}"
    final_path = settings.uploads_dir / stored_name
    final_path.parent.mkdir(parents=True, exist_ok=True)
    with final_path.open("wb") as out:
        for index in range(chunk_count):
            part = chunk_path(settings, upload_id, index)
            if not part.exists():
                raise HTTPException(status_code=409, detail=f"Falta el bloque {index}")
            with part.open("rb") as src:
                shutil.copyfileobj(src, out)
    shutil.rmtree(settings.chunks_dir / upload_id, ignore_errors=True)
    return final_path, stored_name


def ensure_completed_size(path: Path, expected_size: int) -> None:
    actual = os.path.getsize(path)
    if actual != expected_size:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=f"Tamano final invalido: {actual} != {expected_size}")
