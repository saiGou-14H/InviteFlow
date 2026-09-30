"""Exercise the actual Alembic chain against the disposable integration database."""

import asyncio
import os

import pytest
from sqlalchemy import select
from test_auth_integration import harness as harness

from inviteflow.persistence.models import StaffAccount

pytestmark = pytest.mark.integration


async def migrate(database, *args):
    import sys

    env = os.environ | {
        "INVITEFLOW_DATABASE_URL": database.engine.url.render_as_string(hide_password=False)
    }
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        await asyncio.wait_for(child.communicate(), 30)
    except asyncio.TimeoutError:
        child.kill()
        await child.communicate()
        raise
    assert child.returncode == 0, "Alembic failed in disposable migration test"


async def test_existing_auth_data_survives_upgrade_and_old_revision_is_not_ready(harness):
    _, _, database = harness
    async with database.sessions() as db:
        account = await db.scalar(select(StaffAccount))
        original = (account.id, account.password_hash, account.session_epoch)
    try:
        await migrate(database, "downgrade", "0001_foundation")
        assert not await database.check_ready()
        async with database.sessions() as db:
            account = await db.scalar(select(StaffAccount))
            assert (account.id, account.password_hash, account.session_epoch) == original
    finally:
        await migrate(database, "upgrade", "head")
    assert await database.check_ready()
    await migrate(database, "check")
    async with database.sessions() as db:
        account = await db.scalar(select(StaffAccount))
        assert (account.id, account.password_hash, account.session_epoch) == original
