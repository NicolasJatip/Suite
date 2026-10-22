from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Suite"
    public_url: str = "http://localhost:8000"

    admin_user: str = "admin"
    admin_password: str = "cambiar-esta-clave"
    session_secret: str = "cambiar-este-secreto-largo"

    storage_dir: Path = Path("/data")
    max_upload_gb: int = 10
    markitdown_max_upload_mb: int = 512
    chunk_size_mb: int = 16
    job_concurrency: int = 1
    retention_days: Optional[int] = None

    # Experimental y apagado por defecto: "Abrir en Google Doc" con service account.
    google_docs_enabled: bool = False
    google_service_account_key: str = "/data/google-service-account.json"

    transcrip_model: str = "small"
    transcrip_language: str = "es"
    transcrip_compute_type: str = "int8"
    transcrip_device: str = "cpu"

    allowed_extensions: set[str] = Field(
        default_factory=lambda: {
            "mp4",
            "avi",
            "mov",
            "mkv",
            "webm",
            "m4v",
            "opus",
            "ogg",
            "mp3",
            "wav",
            "m4a",
            "aac",
            "flac",
        }
    )
    markitdown_allowed_extensions: set[str] = Field(
        default_factory=lambda: {
            "aac",
            "bmp",
            "csv",
            "doc",
            "docx",
            "epub",
            "flac",
            "gif",
            "htm",
            "html",
            "jpeg",
            "jpg",
            "json",
            "m4a",
            "md",
            "mp3",
            "odp",
            "ods",
            "odt",
            "ogg",
            "opus",
            "pdf",
            "png",
            "ppt",
            "pptx",
            "rtf",
            "tif",
            "tiff",
            "tsv",
            "txt",
            "wav",
            "webp",
            "xls",
            "xlsx",
            "xml",
            "zip",
        }
    )

    @field_validator("retention_days", mode="before")
    @classmethod
    def empty_retention_is_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def public_scheme(self) -> str:
        return urlsplit(self.public_url).scheme or "http"

    @property
    def public_host(self) -> str:
        return urlsplit(self.public_url).hostname or "localhost"

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url.rstrip("/"))
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_gb * 1024 * 1024 * 1024

    @property
    def markitdown_max_upload_bytes(self) -> int:
        return self.markitdown_max_upload_mb * 1024 * 1024

    @property
    def uploads_dir(self) -> Path:
        return self.storage_dir / "uploads"

    @property
    def chunks_dir(self) -> Path:
        return self.storage_dir / "chunks"

    @property
    def outputs_dir(self) -> Path:
        return self.storage_dir / "outputs"

    @property
    def tmp_dir(self) -> Path:
        return self.storage_dir / "tmp"

    @property
    def markitdown_dir(self) -> Path:
        return self.storage_dir / "markitdown"

    @property
    def markitdown_uploads_dir(self) -> Path:
        return self.markitdown_dir / "uploads"

    @property
    def markitdown_outputs_dir(self) -> Path:
        return self.markitdown_dir / "outputs"

    @property
    def database_path(self) -> Path:
        return self.storage_dir / "transcrip.sqlite3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
