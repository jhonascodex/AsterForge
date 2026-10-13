import os

from cryptography.fernet import Fernet

TEST_DATABASE_URL = os.environ.get(
    "ASTERFORGE_TEST_DATABASE_URL",
    "postgresql+asyncpg://asterforge_test:test-only@localhost:5432/asterforge_test",
)

# Tests never inherit runtime credentials or bootstrap identities from a developer's shell/.env.
os.environ["ASTERFORGE_ENVIRONMENT"] = "test"
os.environ["ASTERFORGE_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["ASTERFORGE_INTEGRATION_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["ASTERFORGE_AMI_MONITORING_ENABLED"] = "false"
os.environ.pop("ASTERFORGE_BOOTSTRAP_ADMIN_USERNAME", None)
os.environ.pop("ASTERFORGE_BOOTSTRAP_ADMIN_PASSWORD", None)
