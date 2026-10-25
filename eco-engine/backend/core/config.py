"""
Application settings (pattern from fastapi/full-stack-fastapi-template: one typed
Settings object read from the environment / .env, imported wherever needed).

Secrets and per-request switches (auth provider, LLM key, DATABASE_URL) are still read
where they are used, so tests can change them with monkeypatch at run time.
"""

from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_prefix="ECO_",
        env_ignore_empty=True,
        extra="ignore",
    )

    PROJECT_NAME: str = "Project Eco (EcoRetrofit AI Engine)"
    DESCRIPTION: str = "Green Home Score, fabric-first retrofit planner, dual-fuel carbon, value range, computer vision and a guarded AI advisor."
    VERSION: str = "1.1.0"
    ENGINE_VERSION: str = "1.0.0"

    # Comma-separated extra origins allowed to call the API from a browser. Empty (default)
    # means same-origin only: the web portal is served by this app, so it needs no CORS.
    # "*" is accepted in development only.
    CORS_ORIGINS: str = ""
    # Swagger UI / ReDoc / OpenAPI schema. Off in production unless explicitly enabled.
    ENABLE_DOCS: bool = True

    # Responses at least this large are gzip-compressed.
    GZIP_MIN_BYTES: int = 1024
    # Browser cache lifetimes for the static web portal.
    STATIC_MAX_AGE_S: int = 3600
    ASSET_MAX_AGE_S: int = 7 * 24 * 3600

    @property
    def is_production(self) -> bool:
        import os

        return os.getenv("ENVIRONMENT", "").lower() == "production"

    @property
    def cors_origins(self) -> List[str]:
        raw = self.CORS_ORIGINS.strip()
        if raw == "*":
            if self.is_production:
                raise RuntimeError("ECO_CORS_ORIGINS='*' is not allowed in production; list the exact origins.")
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def docs_enabled(self) -> bool:
        import os

        if self.is_production:
            return os.getenv("ECO_ENABLE_DOCS", "0") == "1"
        return self.ENABLE_DOCS

    @property
    def data_file(self) -> Path:
        return BASE_DIR / "data" / "properties.json"

    @property
    def web_dir(self) -> Path:
        return BASE_DIR / "frontend" / "web"


settings = Settings()
