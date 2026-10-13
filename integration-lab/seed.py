"""Seed the single fixed, test-owned Asterisk target in a fresh release-lab database."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select

from app.core.credentials import encrypt_credential
from app.db.models import AsteriskServer, InventoryEnvironment
from app.db.session import SessionFactory, engine

SERVER_ID = UUID("00000000-0000-4000-8000-000000000016")


async def seed() -> None:
    async with SessionFactory() as session:
        if await session.get(AsteriskServer, SERVER_ID) is not None:
            raise RuntimeError("release lab requires a fresh disposable database")
        environment = await session.scalar(
            select(InventoryEnvironment).where(InventoryEnvironment.code == "DEV")
        )
        if environment is None:
            raise RuntimeError("DEV inventory environment seed is missing")
        now = datetime.now(UTC)
        session.add(
            AsteriskServer(
                id=SERVER_ID,
                name="AsterForge release lab",
                name_key="asterforge release lab",
                hostname="lab-asterisk",
                ami_port=5038,
                ami_tls=False,
                environment=environment,
                location="disposable-release-lab",
                monitoring_enabled=True,
                ami_username_encrypted=encrypt_credential("asterforge-readonly"),
                ami_secret_encrypted=encrypt_credential("test-only-read-password"),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
    await engine.dispose()
    print(f"seeded disposable release-lab server {SERVER_ID}")


if __name__ == "__main__":
    asyncio.run(seed())
