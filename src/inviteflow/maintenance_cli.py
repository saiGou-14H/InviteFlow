"""Manual, bounded maintenance; deletion requires an explicit --apply."""

import argparse
import asyncio
import json

from sqlalchemy import text

from inviteflow.config import get_settings
from inviteflow.maintenance import purge_expired
from inviteflow.persistence.database import Database


async def run_purge(*, apply: bool = False, limit: int = 100) -> dict[str, int]:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be 1..1000")
    if type(apply) is not bool:
        raise ValueError("apply must be a boolean")
    settings = get_settings()
    if settings.database_url is None:
        raise ValueError("INVITEFLOW_DATABASE_URL is required")
    database = Database(settings.database_url.get_secret_value())
    try:
        if not await database.check_ready():
            raise ValueError("Database unavailable or migration required")
        async with database.sessions() as db:
            if not apply:
                # Enforce read-only at PostgreSQL as well as avoiding ORM autoflush.
                await db.execute(text("SET TRANSACTION READ ONLY"))
            counts = await purge_expired(
                db,
                login_window_seconds=settings.login_window_seconds,
                session_retention_seconds=settings.session_cleanup_retention_seconds,
                sent_outbox_retention_seconds=settings.outbox_cleanup_retention_seconds,
                limit=limit,
                dry_run=not apply,
            )
            if apply:
                await db.commit()
            else:
                await db.rollback()
        return counts.as_dict()
    finally:
        await database.dispose()


def _batch_limit(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer in 1..1000") from None
    if not 1 <= value <= 1000:
        raise argparse.ArgumentTypeError("limit must be an integer in 1..1000")
    return value


def run() -> None:
    parser = argparse.ArgumentParser(
        description="InviteFlow bounded maintenance (dry-run unless --apply)",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--purge-expired", action="store_true", help="inspect eligible transient records"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete the selected eligible records")
    mode.add_argument("--dry-run", action="store_true", help="count only (the default)")
    parser.add_argument("--limit", type=_batch_limit, default=100, help="per-table limit: 1..1000")
    args = parser.parse_args()
    if not args.purge_expired:
        parser.error("choose --purge-expired")
    try:
        counts = asyncio.run(run_purge(apply=args.apply, limit=args.limit))
    except Exception:
        # Driver, SQLAlchemy and configuration errors may embed DSNs, SQL parameters
        # or secrets. The CLI boundary deliberately never formats the exception.
        parser.exit(2, "Maintenance failed; check configuration, database and migrations.\n")
    else:
        print(json.dumps(counts, sort_keys=True))


if __name__ == "__main__":
    run()
