from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialConfigurationError(RuntimeError):
    pass


def _fernet() -> Fernet:
    configured_key = get_settings().integration_encryption_key
    if configured_key is None:
        raise CredentialConfigurationError("integration encryption key is not configured")
    try:
        return Fernet(configured_key.get_secret_value().encode())
    except ValueError as exc:
        raise CredentialConfigurationError("integration encryption key is invalid") from exc


def encrypt_credential(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_credential(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise CredentialConfigurationError(
            "stored integration credential cannot be decrypted"
        ) from exc
