"""Application configuration loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by the API and task runner."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    public_origin: str = "http://localhost:8080"
    database_url: str = "postgresql+asyncpg://pptmaster:pptmaster@postgres:5432/pptmaster"
    redis_url: str = "redis://redis:6379/0"
    workspace_root: Path = Path("/srv/projects")
    host_projects_root: Path = Path("/srv/projects")
    host_opencode_config_root: str | None = None
    worker_image: str = "ppt-master-worker:local"
    worker_network: str = "ppt-master-backend"
    worker_uid: int = Field(default=10003, ge=1)
    worker_gid: int = Field(default=10003, ge=1)
    default_model: str | None = None
    allowed_models: str = ""
    aihub_api_key: str | None = None
    deepseek_api_key: str | None = None
    config_encryption_key: str | None = Field(
        default=None,
        validation_alias="PPTMASTER_CONFIG_ENCRYPTION_KEY",
    )
    session_cookie_name: str = "pm_session"
    session_ttl_hours: int = Field(default=168, ge=1, le=24 * 31)
    invite_ttl_hours: int = Field(default=168, ge=1, le=24 * 31)
    admin_username: str | None = None
    admin_email: str | None = None
    admin_password: str | None = None
    opencode_idle_timeout_seconds: int = Field(default=600, ge=60, le=1800)
    celery_task_time_limit: int = Field(default=3600, ge=60)

    @property
    def sync_database_url(self) -> str:
        """Return the SQLAlchemy sync URL used only by Alembic."""

        return self.database_url.replace("+asyncpg", "+psycopg")

    @property
    def secure_cookies(self) -> bool:
        return self.app_env.lower() not in {"development", "test"}

    @property
    def configured_model(self) -> str | None:
        """Return the explicitly configured OpenCode model, if any."""

        model = (self.default_model or "").strip()
        return model or None

    @property
    def configured_models(self) -> list[str]:
        """Return the server-approved OpenCode models in display order."""

        models = [self.configured_model] if self.configured_model else []
        models.extend(item.strip() for item in self.allowed_models.split(",") if item.strip())
        credentials = {
            "aihub": (self.aihub_api_key or "").strip(),
            "deepseek": (self.deepseek_api_key or "").strip(),
        }
        return [
            model
            for model in dict.fromkeys(models)
            if credentials.get(model.partition("/")[0], "")
        ]


@lru_cache
def get_settings() -> Settings:
    """Return the immutable process settings instance."""

    return Settings()
