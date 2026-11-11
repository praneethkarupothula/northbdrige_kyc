"""Application configuration.

All settings come from environment variables (or a local ``.env`` file). Nothing here
silently switches a deployment into demo mode: test-only behaviour (the scripted LLM
provider, SQLite) must be selected explicitly and is refused in production.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- environment -------------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    public_base_url: str = "http://localhost:5173"
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- database ----------------------------------------------------------------
    database_url: str = ""
    migration_database_url: str = ""  # optional direct (non-pooled) URL for Alembic
    allow_sqlite: bool = False  # SQLite must be opted into explicitly
    db_pool_size: int = 5

    # --- secrets -----------------------------------------------------------------
    session_secret: SecretStr = SecretStr("")
    encryption_key: SecretStr = SecretStr("")  # base64 32 bytes (AES-256-GCM)
    identity_hmac_secret: SecretStr = SecretStr("")

    # --- sessions / auth ---------------------------------------------------------
    session_ttl_minutes: int = 60 * 8
    session_idle_minutes: int = 60
    cookie_secure: bool | None = None  # defaults to True in production
    login_max_failures: int = 5
    login_window_minutes: int = 15
    email_token_ttl_minutes: int = 60 * 24
    reset_token_ttl_minutes: int = 30

    # --- AI provider -------------------------------------------------------------
    llm_provider: Literal["gemini", "scripted"] = "gemini"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout_seconds: float = 30.0
    gemini_max_retries: int = 2
    llm_max_input_chars: int = 12000

    # --- OCR ---------------------------------------------------------------------
    tesseract_cmd: str = ""  # e.g. C:\\Program Files\\Tesseract-OCR\\tesseract.exe
    tesseract_lang: str = "eng"
    ocr_timeout_seconds: int = 30
    ocr_min_alnum_chars: int = 40
    ocr_min_mean_confidence: float = 60.0
    pdf_render_dpi: int = 200

    # --- upload limits -----------------------------------------------------------
    max_file_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 12 * 1024 * 1024
    max_files_per_version: int = 10
    max_pdf_pages: int = 10
    max_image_pixels: int = 40_000_000

    # --- policy defaults (admin can override per organization) ---------------------
    pan_policy: Literal["optional", "required", "not_requested"] = "required"
    max_salary_slips: int = 6
    required_salary_slips: int = 1
    retention_days_after_decision: int = 7

    # --- worker ------------------------------------------------------------------
    worker_poll_seconds: float = 1.0
    worker_lease_seconds: int = 120
    worker_heartbeat_seconds: int = 15
    job_max_attempts: int = 3
    retention_sweep_minutes: int = 60
    sse_max_seconds: int = 600  # stream closes after this; the browser reconnects with Last-Event-ID

    # --- storage -----------------------------------------------------------------
    storage_backend: Literal["local", "s3"] = "local"
    storage_dir: Path = BACKEND_DIR / "var" / "storage"
    s3_bucket: str = ""
    s3_endpoint_url: str = ""

    # --- email -------------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "no-reply@northbridge.example"
    smtp_starttls: bool = True

    # --- telemetry ---------------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "northbridge-kyc"

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):  # allow comma-separated env var
        if isinstance(v, str) and not v.strip().startswith("["):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _guard_production(self):
        if self.app_env == "production":
            if self.llm_provider != "gemini":
                raise ValueError("LLM_PROVIDER=scripted is test-only and refused in production")
            if self.database_url.startswith("sqlite"):
                raise ValueError("SQLite is not allowed in production")
        if self.llm_provider == "scripted" and self.app_env != "test":
            raise ValueError("LLM_PROVIDER=scripted is a clearly-marked test double; set APP_ENV=test to use it")
        return self

    # --- derived -----------------------------------------------------------------
    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def secure_cookies(self) -> bool:
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.app_env == "production"

    @property
    def dev_email_capture(self) -> bool:
        return not self.smtp_host and self.app_env != "production"

    def encryption_key_bytes(self) -> bytes:
        raw = self.encryption_key.get_secret_value()
        if not raw:
            raise RuntimeError("ENCRYPTION_KEY is not set (generate with: python -m app.cli gen-secrets)")
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(key) != 32:
            raise RuntimeError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        return key

    def hmac_key_bytes(self) -> bytes:
        raw = self.identity_hmac_secret.get_secret_value()
        if len(raw) < 32:
            raise RuntimeError("IDENTITY_HMAC_SECRET must be at least 32 characters")
        return raw.encode()

    def validate_database_url(self) -> None:
        if not self.database_url:
            raise RuntimeError("DATABASE_URL is not set. See .env.example.")
        if self.is_sqlite and not self.allow_sqlite:
            raise RuntimeError(
                "DATABASE_URL points at SQLite but ALLOW_SQLITE is not true. SQLite is only for explicit "
                "local development; the app never falls back to it automatically."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
