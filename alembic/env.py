"""Alembic entry point; the DSN comes only from INVITEFLOW_DATABASE_URL."""

import asyncio
import os

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context
from inviteflow.persistence.database import validate_database_url
from inviteflow.persistence.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = validate_database_url(os.environ.get("INVITEFLOW_DATABASE_URL", ""))
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def configure_connection(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = validate_database_url(os.environ.get("INVITEFLOW_DATABASE_URL", ""))
    engine = create_async_engine(url, poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(configure_connection)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
