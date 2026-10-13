import argparse
import asyncio
import getpass

from app.db.session import SessionFactory, engine
from app.services.bootstrap import ensure_initial_administrator


async def create_admin(username: str, display_name: str) -> None:
    password = getpass.getpass("New administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")
    async with SessionFactory() as session:
        created = await ensure_initial_administrator(
            session,
            username=username,
            temporary_password=password,
            display_name=display_name,
        )
    if not created:
        raise SystemExit("User already exists")
    print("Administrator created")


def main() -> None:
    parser = argparse.ArgumentParser(description="AsterForge administrative commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create-admin", help="Create the initial administrator")
    create.add_argument("--username", required=True)
    create.add_argument("--display-name", required=True)
    args = parser.parse_args()
    if args.command == "create-admin":
        asyncio.run(_run_create_admin(args.username, args.display_name))


async def _run_create_admin(username: str, display_name: str) -> None:
    try:
        await create_admin(username, display_name)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
