import base64
import binascii
from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ASTERFORGE_",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Environment.DEVELOPMENT
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    database_url: PostgresDsn
    cors_origins: list[AnyHttpUrl] = Field(
        default_factory=lambda: [AnyHttpUrl("http://localhost:4173")]
    )
    session_ttl_minutes: int = Field(default=480, ge=5, le=10080)
    session_cookie_secure: bool = False
    login_attempt_limit: int = Field(default=5, ge=3, le=20)
    login_attempt_window_minutes: int = Field(default=15, ge=1, le=60)
    login_block_minutes: int = Field(default=15, ge=1, le=1440)
    bootstrap_admin_username: str | None = Field(
        default=None,
        min_length=3,
        max_length=100,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    bootstrap_admin_password: SecretStr | None = Field(
        default=None, min_length=12, max_length=256
    )
    integration_encryption_key: SecretStr | None = Field(default=None, min_length=44, max_length=44)
    ami_monitoring_enabled: bool = True
    ami_connect_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    ami_action_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    ami_poll_interval_seconds: float = Field(default=30.0, ge=1, le=3600)
    ami_inventory_refresh_seconds: float = Field(default=10.0, ge=1, le=300)
    ami_reconnect_min_seconds: float = Field(default=1.0, ge=0.1, le=60)
    ami_reconnect_max_seconds: float = Field(default=60.0, ge=1, le=3600)
    cdr_source: Literal["disabled", "postgres"] = "disabled"
    cel_source: Literal["disabled", "postgres"] = "disabled"
    host_telemetry_enabled: bool = False
    host_telemetry_port: int = Field(default=9100, ge=1, le=65535)
    host_telemetry_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60)
    host_telemetry_poll_interval_seconds: float = Field(default=60.0, ge=5, le=3600)
    host_telemetry_inventory_refresh_seconds: float = Field(default=10.0, ge=1, le=300)
    host_telemetry_recording_mountpoint: str = Field(
        default="/var/spool/asterisk/monitor", min_length=1, max_length=500
    )
    log_ingestion_enabled: bool = False
    log_source_root: str = Field(
        default="/var/lib/asterforge/log-sources", min_length=1, max_length=500
    )
    log_poll_interval_seconds: float = Field(default=5.0, ge=1, le=3600)
    log_inventory_refresh_seconds: float = Field(default=10.0, ge=1, le=300)
    log_read_max_bytes: int = Field(default=1_048_576, ge=4096, le=8_388_608)
    log_read_max_lines: int = Field(default=1000, ge=1, le=10_000)
    log_retention_days: int = Field(default=30, ge=1, le=3650)
    log_retention_batch_size: int = Field(default=5000, ge=1, le=10_000)
    log_retention_interval_seconds: float = Field(default=3600, ge=60, le=86400)

    @field_validator(
        "bootstrap_admin_username",
        "bootstrap_admin_password",
        "integration_encryption_key",
        mode="before",
    )
    @classmethod
    def empty_bootstrap_values_are_unset(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("integration_encryption_key")
    @classmethod
    def require_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        try:
            decoded = base64.urlsafe_b64decode(value.get_secret_value().encode())
        except (binascii.Error, ValueError) as exc:
            raise ValueError("integration_encryption_key must be a Fernet key") from exc
        if len(decoded) != 32:
            raise ValueError("integration_encryption_key must be a Fernet key")
        return value

    @model_validator(mode="after")
    def require_complete_bootstrap_credentials(self) -> Settings:
        if (self.bootstrap_admin_username is None) != (self.bootstrap_admin_password is None):
            raise ValueError("bootstrap administrator username and password must be set together")
        return self

    @model_validator(mode="after")
    def require_secure_production_session_cookie(self) -> Settings:
        if self.environment is Environment.PRODUCTION and not self.session_cookie_secure:
            raise ValueError("session continuation cookies must be secure in production")
        return self

    @model_validator(mode="after")
    def require_valid_ami_backoff(self) -> Settings:
        if self.ami_reconnect_max_seconds < self.ami_reconnect_min_seconds:
            raise ValueError("ami_reconnect_max_seconds must be at least the minimum")
        return self

    @field_validator("database_url")
    @classmethod
    def require_asyncpg(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("database_url must use the postgresql+asyncpg scheme")
        return value

    @field_validator("host_telemetry_recording_mountpoint")
    @classmethod
    def require_absolute_recording_mountpoint(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("/") or "\x00" in cleaned:
            raise ValueError("host telemetry recording mountpoint must be an absolute path")
        return cleaned

    @field_validator("log_source_root")
    @classmethod
    def require_absolute_log_source_root(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned.startswith("/") or "\x00" in cleaned:
            raise ValueError("log source root must be an absolute container path")
        return cleaned.rstrip("/") or "/"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # BaseSettings loads required values from env.
