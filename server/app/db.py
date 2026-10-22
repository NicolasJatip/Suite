from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    def init(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    upload_id TEXT,
                    video_path TEXT NOT NULL,
                    txt_path TEXT,
                    srt_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS markitdown_jobs (
                    id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    stored_name TEXT NOT NULL,
                    extension TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    input_path TEXT NOT NULL,
                    md_path TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    finished_at TEXT,
                    error TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_markitdown_jobs_created ON markitdown_jobs(created_at)"
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_job(self, **values: object) -> None:
        now = utcnow()
        values.setdefault("created_at", now)
        values.setdefault("updated_at", now)
        columns = ", ".join(values.keys())
        placeholders = ", ".join([f":{key}" for key in values.keys()])
        with self.connect() as conn:
            conn.execute(f"INSERT INTO jobs ({columns}) VALUES ({placeholders})", values)

    def update_job(self, job_id: str, **values: object) -> None:
        values["updated_at"] = utcnow()
        values["id"] = job_id
        assignments = ", ".join([f"{key} = :{key}" for key in values.keys() if key != "id"])
        with self.connect() as conn:
            conn.execute(f"UPDATE jobs SET {assignments} WHERE id = :id", values)

    def get_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def delete_job(self, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def create_markitdown_job(self, **values: object) -> None:
        now = utcnow()
        values.setdefault("created_at", now)
        values.setdefault("updated_at", now)
        columns = ", ".join(values.keys())
        placeholders = ", ".join([f":{key}" for key in values.keys()])
        with self.connect() as conn:
            conn.execute(f"INSERT INTO markitdown_jobs ({columns}) VALUES ({placeholders})", values)

    def update_markitdown_job(self, job_id: str, **values: object) -> None:
        values["updated_at"] = utcnow()
        values["id"] = job_id
        assignments = ", ".join([f"{key} = :{key}" for key in values.keys() if key != "id"])
        with self.connect() as conn:
            conn.execute(f"UPDATE markitdown_jobs SET {assignments} WHERE id = :id", values)

    def get_markitdown_job(self, job_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM markitdown_jobs WHERE id = ?", (job_id,)).fetchone()

    def list_markitdown_jobs(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM markitdown_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def delete_markitdown_job(self, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM markitdown_jobs WHERE id = ?", (job_id,))

    def list_jobs(self, limit: int = 100) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()

    def claim_next_job(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            now = utcnow()
            conn.execute(
                """
                UPDATE jobs
                SET status = 'processing',
                    progress = 0,
                    message = 'Preparando transcripcion',
                    started_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, now, row["id"]),
            )
            return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
