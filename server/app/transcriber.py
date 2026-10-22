from __future__ import annotations

import asyncio
import math
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import Settings
from .db import Database, utcnow


def format_timestamp(seconds: float) -> str:
    milliseconds = int(round(seconds * 1000))
    hours = milliseconds // 3_600_000
    milliseconds %= 3_600_000
    minutes = milliseconds // 60_000
    milliseconds %= 60_000
    secs = milliseconds // 1000
    millis = milliseconds % 1000
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def write_txt(path: Path, segments: Iterable[object]) -> None:
    with path.open("w", encoding="utf-8") as out:
        for segment in segments:
            text = getattr(segment, "text", "").strip()
            if text:
                out.write(text + "\n")


def write_srt(path: Path, segments: Iterable[object]) -> None:
    with path.open("w", encoding="utf-8") as out:
        for index, segment in enumerate(segments, start=1):
            text = getattr(segment, "text", "").strip()
            start = format_timestamp(float(getattr(segment, "start", 0)))
            end = format_timestamp(float(getattr(segment, "end", 0)))
            out.write(f"{index}\n{start} --> {end}\n{text}\n\n")


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_audio(video_path: Path, audio_path: Path) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(audio_path),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def run_transcription(settings: Settings, db: Database, job_id: str) -> None:
    row = db.get_job(job_id)
    if row is None:
        return

    if not ffmpeg_available():
        raise RuntimeError("FFmpeg no esta disponible en el contenedor")

    from faster_whisper import WhisperModel

    video_path = Path(row["video_path"])
    base_name = Path(row["stored_name"]).stem
    audio_path = settings.tmp_dir / f"{base_name}.wav"
    txt_path = settings.outputs_dir / f"{base_name}.txt"
    srt_path = settings.outputs_dir / f"{base_name}.srt"
    settings.outputs_dir.mkdir(parents=True, exist_ok=True)

    db.update_job(job_id, progress=5, message="Extrayendo audio")
    extract_audio(video_path, audio_path)

    db.update_job(job_id, progress=15, message=f"Transcribiendo con modelo {settings.transcrip_model}")
    model = WhisperModel(
        settings.transcrip_model,
        device=settings.transcrip_device,
        compute_type=settings.transcrip_compute_type,
    )
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=settings.transcrip_language,
        vad_filter=True,
        beam_size=5,
    )

    duration = float(getattr(info, "duration", 0) or 0)
    collected = []
    for segment in segments_iter:
        collected.append(segment)
        if duration > 0:
            progress = 15 + min(80, math.floor((float(segment.end) / duration) * 80))
            db.update_job(job_id, progress=progress, message="Transcribiendo")

    db.update_job(job_id, progress=95, message="Generando archivos")
    write_txt(txt_path, collected)
    write_srt(srt_path, collected)
    audio_path.unlink(missing_ok=True)

    db.update_job(
        job_id,
        status="done",
        progress=100,
        message="Listo",
        txt_path=str(txt_path),
        srt_path=str(srt_path),
        finished_at=utcnow(),
        error=None,
    )


async def worker_loop(settings: Settings, db: Database, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        row = db.claim_next_job()
        if row is None:
            await asyncio.sleep(2)
            continue

        job_id = row["id"]
        try:
            await asyncio.to_thread(run_transcription, settings, db, job_id)
        except Exception as exc:
            db.update_job(
                job_id,
                status="failed",
                progress=0,
                message="Error",
                error=str(exc),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
