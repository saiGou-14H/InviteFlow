import asyncio

import pytest
from sqlalchemy import func, select
from test_auth_integration import harness as harness

from inviteflow.api.errors import ApiError
from inviteflow.idempotency import CommandResult, IdempotencyExecutor
from inviteflow.persistence.models import AuditLog, IdempotencyRequest

pytestmark = pytest.mark.integration


async def test_replay_and_payload_conflict(harness):
    app, _, database = harness
    executor = IdempotencyExecutor(database, app.state.settings.session_secret.get_secret_value())
    calls = []

    async def command(db):
        calls.append(True)
        db.add(AuditLog(actor_kind="user", action="test.command"))
        return CommandResult(202, {"operation_id": "safe-reference"})

    first = await executor.execute(
        scope="user:one:test",
        key="request-key-1",
        payload={"codes": ["PRIVATE_TEST_CDK"]},
        command=command,
    )
    second = await executor.execute(
        scope="user:one:test",
        key="request-key-1",
        payload={"codes": ["PRIVATE_TEST_CDK"]},
        command=command,
    )
    assert first.status == second.status == 202
    assert not first.replayed and second.replayed and len(calls) == 1
    with pytest.raises(ApiError) as error:
        await executor.execute(
            scope="user:one:test",
            key="request-key-1",
            payload={"codes": ["other"]},
            command=command,
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
    async with database.sessions() as db:
        row = await db.scalar(select(IdempotencyRequest))
        assert row.key_digest != "request-key-1"
        assert "PRIVATE_TEST_CDK" not in str(row.response_body)
        assert "PRIVATE_TEST_CDK" not in row.request_digest


async def test_concurrent_execution_is_once_and_other_call_in_progress(harness):
    app, _, database = harness
    executor = IdempotencyExecutor(database, app.state.settings.session_secret.get_secret_value())
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def command(db):
        calls.append(True)
        db.add(AuditLog(actor_kind="user", action="test.concurrent"))
        entered.set()
        await release.wait()
        return CommandResult(200, {"ok": True})

    task = asyncio.create_task(
        executor.execute(scope="user:one:test", key="concurrent-key", payload={}, command=command)
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(ApiError) as error:
            await executor.execute(
                scope="user:one:test", key="concurrent-key", payload={}, command=command
            )
        assert error.value.code == "IDEMPOTENCY_IN_PROGRESS"
    finally:
        release.set()
        await task
    assert len(calls) == 1
    replay = await executor.execute(
        scope="user:one:test", key="concurrent-key", payload={}, command=command
    )
    assert replay.replayed
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuditLog)) == 1


async def test_failure_rolls_back_receipt_and_business_write(harness):
    app, _, database = harness
    executor = IdempotencyExecutor(database, app.state.settings.session_secret.get_secret_value())

    async def command(db):
        db.add(AuditLog(actor_kind="user", action="test.rollback"))
        await db.flush()
        raise ValueError("simulated failure")

    with pytest.raises(ValueError, match="simulated failure"):
        await executor.execute(
            scope="user:one:test", key="rollback-key", payload={}, command=command
        )
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(AuditLog)) == 0
        assert await db.scalar(select(func.count()).select_from(IdempotencyRequest)) == 0


async def test_actor_scopes_are_independent_and_bad_keys_rejected(harness):
    app, _, database = harness
    executor = IdempotencyExecutor(database, app.state.settings.session_secret.get_secret_value())
    calls = []

    async def command(db):
        calls.append(True)
        return CommandResult(200, {"ok": True})

    for actor in ("user:one:test", "user:two:test"):
        await executor.execute(scope=actor, key="same-key-1", payload={}, command=command)
    assert len(calls) == 2
    with pytest.raises(ApiError) as error:
        await executor.execute(scope="user:one:test", key="bad key", payload={}, command=command)
    assert error.value.code == "INVALID_IDEMPOTENCY_KEY"
