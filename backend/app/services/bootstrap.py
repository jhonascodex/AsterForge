from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, normalize_username
from app.db.models import AuditEvent, Permission, Profile, User


async def ensure_initial_administrator(
    session: AsyncSession,
    *,
    username: str,
    temporary_password: str,
    display_name: str = "AsterForge Administrator",
) -> bool:
    """Create the configured bootstrap administrator once without updating existing users."""
    normalized_username = normalize_username(username)
    async with session.begin():
        permissions = list(
            (
                await session.scalars(
                    select(Permission).order_by(Permission.code).with_for_update()
                )
            ).all()
        )
        if not permissions:
            raise RuntimeError("permission catalog is empty; run migrations before the backend")

        existing_user = await session.scalar(
            select(User.id).where(User.username == normalized_username)
        )
        if existing_user is not None:
            return False

        profile = await session.scalar(
            select(Profile).where(Profile.name == "Super Administrator")
        )
        if profile is None:
            profile = Profile(
                name="Super Administrator",
                description="System bootstrap profile with all permissions",
                enabled=True,
                is_system=True,
                permissions=permissions,
            )
            session.add(profile)
        elif not profile.is_system:
            raise RuntimeError("Super Administrator profile exists but is not a system profile")
        else:
            profile.enabled = True
            profile.permissions = permissions

        now = datetime.now(UTC)
        user = User(
            username=normalized_username,
            display_name=display_name.strip(),
            password_hash=hash_password(temporary_password),
            enabled=True,
            must_change_password=True,
            profiles=[profile],
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            AuditEvent(
                occurred_at=now,
                action="user.bootstrap_created",
                target_type="user",
                target_id=str(user.id),
            )
        )
    return True
