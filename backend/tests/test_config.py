import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings

VALID_DB_URL = "postgresql+asyncpg://asterforge:test-only@localhost/asterforge"


def test_rejects_non_postgresql_database_url() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="sqlite:///local.db")  # type: ignore[arg-type]


def test_requires_async_postgresql_driver() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url="postgresql://asterforge:secret@localhost/asterforge")


def test_rejects_unknown_environment() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="staging", database_url=VALID_DB_URL)  # type: ignore[arg-type]


def test_production_requires_secure_session_continuation_cookie() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production", database_url=VALID_DB_URL)

    settings = Settings(
        environment="production",
        database_url=VALID_DB_URL,
        session_cookie_secure=True,
    )
    assert settings.session_cookie_secure is True


def test_bootstrap_credentials_must_be_configured_together() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, bootstrap_admin_username="admin")

    secret = "must-not-appear-in-validation-errors"
    with pytest.raises(ValidationError) as error:
        Settings(database_url=VALID_DB_URL, bootstrap_admin_password=secret)
    assert secret not in str(error.value)


def test_bootstrap_password_is_redacted_from_settings_representation() -> None:
    secret = "temporary-password-not-for-production"
    settings = Settings(
        database_url=VALID_DB_URL,
        bootstrap_admin_username="admin",
        bootstrap_admin_password=secret,
    )
    assert secret not in repr(settings)


def test_integration_encryption_key_must_be_valid_and_is_redacted() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, integration_encryption_key="x" * 44)

    key = Fernet.generate_key().decode()
    settings = Settings(database_url=VALID_DB_URL, integration_encryption_key=key)
    assert key not in repr(settings)


def test_history_sources_are_explicitly_allowlisted() -> None:
    settings = Settings(database_url=VALID_DB_URL)
    assert settings.cdr_source == "disabled"
    assert settings.cel_source == "disabled"
    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, cdr_source="arbitrary")  # type: ignore[arg-type]


def test_host_telemetry_defaults_disabled_and_validates_safe_bounds() -> None:
    settings = Settings(database_url=VALID_DB_URL)
    assert settings.host_telemetry_enabled is False
    assert settings.host_telemetry_port == 9100
    assert settings.host_telemetry_recording_mountpoint == "/var/spool/asterisk/monitor"

    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, host_telemetry_port=0)
    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, host_telemetry_poll_interval_seconds=1)
    with pytest.raises(ValidationError):
        Settings(database_url=VALID_DB_URL, host_telemetry_recording_mountpoint="relative")
