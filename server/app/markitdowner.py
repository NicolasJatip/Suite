from __future__ import annotations

import uuid
from pathlib import Path

from .config import Settings
from .db import Database, utcnow
from .uploads import clean_filename, extension_for


def markdown_filename(original_name: str) -> str:
    stem = Path(original_name).stem or "archivo"
    return clean_filename(f"{stem}.md")


def run_markitdown(settings: Settings, db: Database, job_id: str) -> None:
    row = db.get_markitdown_job(job_id)
    if row is None:
        return

    input_path = Path(row["input_path"])
    base_name = Path(row["stored_name"]).stem
    md_path = settings.markitdown_outputs_dir / f"{base_name}.md"
    settings.markitdown_outputs_dir.mkdir(parents=True, exist_ok=True)

    db.update_markitdown_job(job_id, status="processing", progress=15, message="Interpretando archivo")

    from markitdown import MarkItDown

    result = MarkItDown().convert(str(input_path))
    text = getattr(result, "text_content", "") or ""
    md_path.write_text(text, encoding="utf-8")

    db.update_markitdown_job(
        job_id,
        status="done",
        progress=100,
        message="Listo para descargar",
        md_path=str(md_path),
        finished_at=utcnow(),
        error=None,
    )


def stored_markitdown_name(original_name: str) -> str:
    ext = extension_for(original_name)
    return f"{uuid.uuid4().hex}.{ext}"
