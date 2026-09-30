"""Explicit, lazy async PostgreSQL engine lifecycle."""

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

EXPECTED_REVISION = "0001_foundation"

__all__ = ["Database", "EXPECTED_REVISION"]


def validate_database_url(url: str) -> URL:
    """Validate without including credentials in error messages."""
    try:
        parsed = make_url(url)
    except (ArgumentError, TypeError, ValueError):
        raise ValueError("Database URL must use postgresql+asyncpg.") from None
    if parsed.drivername != "postgresql+asyncpg":
        raise ValueError("Database URL must use postgresql+asyncpg.")
    return parsed


class Database:
    """Own an engine and session factory without connecting until first use."""

    def __init__(self, url: str) -> None:
        self.engine = create_async_engine(
            validate_database_url(url), pool_pre_ping=True, hide_parameters=True
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def check_ready(self) -> bool:
        """Require connectivity and precisely the supported schema revision."""
        try:
            async with self.engine.connect() as connection:
                probe = await connection.execute(text("SELECT 1"))
                if probe.scalar_one() != 1:
                    return False
                versions = await connection.execute(text("SELECT version_num FROM alembic_version"))
                return versions.scalars().all() == [EXPECTED_REVISION]
        except Exception:
            # A missing version table, extra heads, or an unavailable database is not ready.
            # Never log a DSN or exception containing database credentials here.
            return False
