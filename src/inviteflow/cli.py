import argparse
import asyncio
import getpass

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from inviteflow.config import get_settings
from inviteflow.persistence.database import Database
from inviteflow.persistence.models import AuditLog, StaffAccount
from inviteflow.security import hash_password, normalize_username


async def manage_admin(action: str, username: str, password: str | None = None) -> None:
    settings = get_settings()
    if not settings.database_url:
        raise ValueError("INVITEFLOW_DATABASE_URL is required")
    username = normalize_username(username)
    encoded = hash_password(password) if password is not None else None
    database = Database(settings.database_url.get_secret_value())
    try:
        if not await database.check_ready():
            raise ValueError("Database unavailable or migration required; run alembic upgrade head")
        async with database.sessions.begin() as db:
            account = await db.scalar(
                select(StaffAccount)
                .where(StaffAccount.username_normalized == username)
                .with_for_update()
            )
            if action == "create":
                if account is not None:
                    raise ValueError("Account already exists; use reset-password if intended")
                if encoded is None:
                    raise ValueError("Password required")
                account = StaffAccount(username_normalized=username, password_hash=encoded)
                db.add(account)
                await db.flush()
            else:
                if account is None:
                    raise ValueError("Account not found")
                account.session_epoch += 1
                if action == "reset-password":
                    if encoded is None:
                        raise ValueError("Password required")
                    account.password_hash = encoded
                elif action == "disable":
                    account.status = "disabled"
                elif action != "revoke-sessions":
                    raise ValueError("Unsupported operation")
            db.add(
                AuditLog(actor_kind="internal", action="admin." + action, target=str(account.id))
            )
    finally:
        await database.dispose()


def run() -> None:
    parser = argparse.ArgumentParser(description="InviteFlow administrator maintenance")
    parser.add_argument(
        "action", choices=["create", "reset-password", "revoke-sessions", "disable"]
    )
    parser.add_argument("username")
    args = parser.parse_args()
    password = None
    try:
        if args.action in {"create", "reset-password"}:
            password = getpass.getpass("Administrator password (12–256 characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
        asyncio.run(manage_admin(args.action, args.username, password))
    except ValueError as exc:
        parser.exit(2, str(exc) + "\n")
    except SQLAlchemyError:
        parser.exit(2, "Database operation failed; no credentials are displayed.\n")
    print("Administrator operation completed.")


if __name__ == "__main__":
    run()
