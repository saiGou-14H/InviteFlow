import asyncio
import json
import sys
from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import event, null, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from test_auth_integration import harness as harness

from inviteflow import maintenance, maintenance_cli
from inviteflow.maintenance import PurgeCounts, purge_expired
from inviteflow.persistence.database import Database
from inviteflow.persistence.models import (
    AuditLog,
    IdempotencyRequest,
    LoginRateLimit,
    Operation,
    OutboxMessage,
    PublicSession,
    StaffAccount,
    StaffSession,
    utcnow,
)

COUNTS = {
    "public_sessions": 3,
    "staff_sessions": 3,
    "idempotency_requests": 2,
    "login_rate_limits": 2,
    "sent_outbox_messages": 2,
}
TABLES = {
    "public_sessions": (PublicSession, PublicSession.id),
    "staff_sessions": (StaffSession, StaffSession.id),
    "idempotency_requests": (IdempotencyRequest, IdempotencyRequest.id),
    "login_rate_limits": (LoginRateLimit, LoginRateLimit.scope_digest),
    "sent_outbox_messages": (OutboxMessage, OutboxMessage.id),
    "operations": (Operation, Operation.id),
    "audit_logs": (AuditLog, AuditLog.id),
    "accounts": (StaffAccount, StaffAccount.id),
}


@pytest.mark.parametrize(
    ("name", "value"),
    [("limit", value) for value in (0, -1, 1001, True, 1.5, "2")]
    + [
        (name, value)
        for name in ("session_retention_seconds", "sent_outbox_retention_seconds")
        for value in (0, 59, 31536001, True, 60.5)
    ]
    + [("login_window_seconds", value) for value in (0, -1, 86401, True, 1.5)]
    + [("dry_run", value) for value in (0, 1, "false")],
)
async def test_invalid_parameters_do_not_query(name: str, value: Any) -> None:
    db = MagicMock(spec=AsyncSession)
    kwargs: dict[str, Any] = {"login_window_seconds": 60, name: value}
    with pytest.raises(ValueError, match=name):
        await purge_expired(db, **kwargs)
    db.scalars.assert_not_called()
    db.execute.assert_not_called()


@pytest.mark.parametrize("limit", [1, 1000])
@pytest.mark.parametrize("dry_run", [True, False])
async def test_queries_are_bounded_and_lock_only_on_apply(limit: int, dry_run: bool) -> None:
    db = MagicMock(spec=AsyncSession)
    result = MagicMock()
    result.all.return_value = [uuid4()]
    db.scalars = AsyncMock(return_value=result)
    counts = await purge_expired(db, login_window_seconds=60, limit=limit, dry_run=dry_run)
    assert set(counts.as_dict().values()) == {1}
    statements = [call.args[0] for call in db.scalars.call_args_list]
    selections = [stmt for stmt in statements if stmt.is_select]
    assert len(selections) == 5
    for stmt in selections:
        compiled = stmt.compile(dialect=cast(Any, postgresql.dialect)())
        assert limit in compiled.params.values()
        assert " LIMIT " in str(compiled)
        assert ("FOR UPDATE SKIP LOCKED" in str(compiled)) is (not dry_run)
    deletions = [stmt for stmt in statements if stmt.is_delete]
    assert len(deletions) == (0 if dry_run else 5)
    for stmt in deletions:
        sql = str(stmt.compile(dialect=cast(Any, postgresql.dialect)()))
        assert " IN (" in sql and " RETURNING " in sql
    db.flush.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.parametrize("argv", [[], ["--dry-run"], ["--apply"]])
def test_cli_defaults_and_apply(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    run_purge = AsyncMock(return_value=COUNTS)
    monkeypatch.setattr(maintenance_cli, "run_purge", run_purge)
    monkeypatch.setattr(sys, "argv", ["maintenance", "--purge-expired", *argv, "--limit", "7"])
    maintenance_cli.run()
    run_purge.assert_awaited_once_with(apply="--apply" in argv, limit=7)
    output = capsys.readouterr()
    assert json.loads(output.out) == COUNTS
    assert not output.err


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--apply"],
        ["--purge-expired", "--app"],
        ["--purge-expired", "--apply", "--dry-run"],
        *[["--purge-expired", "--limit", value] for value in ("0", "1001", "x", "1.5")],
    ],
)
def test_cli_rejects_invalid_arguments(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    run_purge = AsyncMock()
    monkeypatch.setattr(maintenance_cli, "run_purge", run_purge)
    monkeypatch.setattr(sys, "argv", ["maintenance", *argv])
    with pytest.raises(SystemExit) as exc:
        maintenance_cli.run()
    assert exc.value.code == 2
    run_purge.assert_not_called()


@pytest.mark.parametrize("error_type", [SQLAlchemyError, OSError, ValueError, RuntimeError])
def test_cli_errors_never_expose_secrets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error_type: type[Exception]
) -> None:
    error = error_type("postgresql+asyncpg://user:SECRET@localhost/db private SQL payload")
    monkeypatch.setattr(maintenance_cli, "run_purge", AsyncMock(side_effect=error))
    monkeypatch.setattr(sys, "argv", ["maintenance", "--purge-expired"])
    with pytest.raises(SystemExit) as exc:
        maintenance_cli.run()
    assert exc.value.code == 2
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "Maintenance failed; check configuration, database and migrations.\n"


@pytest.mark.parametrize("kwargs", [{"limit": 0}, {"limit": True}, {"apply": "yes"}])
async def test_run_purge_validates_before_loading_settings(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    settings = MagicMock()
    monkeypatch.setattr(maintenance_cli, "get_settings", settings)
    with pytest.raises(ValueError):
        await maintenance_cli.run_purge(**kwargs)
    settings.assert_not_called()


async def _snapshot(database: Database) -> dict[str, set[str]]:
    async with database.sessions() as db:
        return {
            name: {str(key) for key in await db.scalars(select(column))}
            for name, (_, column) in TABLES.items()
        }


async def _seed(database: Database, now: datetime) -> dict[str, set[str]]:
    eligible: dict[str, set[str]] = {name: set() for name in COUNTS}
    cutoff = now - timedelta(hours=1)
    async with database.sessions.begin() as db:
        account = (await db.scalars(select(StaffAccount))).one()
        for model in (PublicSession, StaffSession):
            name = model.__tablename__
            for expires_at, revoked_at, removable in (
                (cutoff - timedelta(seconds=1), None, True),
                (cutoff, None, True),
                (now - timedelta(seconds=1), None, False),
                (now + timedelta(days=1), cutoff, True),
                (now + timedelta(days=1), now - timedelta(seconds=1), False),
                (now + timedelta(days=1), None, False),
            ):
                key = uuid4()
                values: dict[str, Any] = {
                    "id": key,
                    "token_hash": uuid4().hex,
                    "csrf_hash": uuid4().hex,
                    "created_at": now - timedelta(days=30),
                    "expires_at": expires_at,
                    "absolute_expires_at": now + timedelta(days=2),
                    "revoked_at": revoked_at,
                }
                if model is StaffSession:
                    values["account_id"] = account.id
                db.add(model(**values))
                if removable:
                    eligible[name].add(str(key))
        for response_status, body, expiry, removable in (
            (200, {}, cutoff, True),
            (202, {"accepted": True}, now, True),
            (200, {}, now + timedelta(days=1), False),
            (None, {}, cutoff, False),
            (None, None, cutoff, False),
            (200, None, cutoff, False),
            (200, null(), cutoff, False),
        ):
            key = uuid4()
            db.add(
                IdempotencyRequest(
                    id=key,
                    scope="maintenance-test",
                    key_digest=uuid4().hex,
                    request_digest=uuid4().hex,
                    response_status=response_status,
                    response_body=body,
                    created_at=now - timedelta(days=30),
                    expires_at=expiry,
                )
            )
            if removable:
                eligible["idempotency_requests"].add(str(key))
        for age in (3601, 3600, 3599, 61, 0):
            rate_key = uuid4().hex
            db.add(
                LoginRateLimit(
                    scope_digest=rate_key, window_started_at=now - timedelta(seconds=age)
                )
            )
            if age >= 3600:
                eligible["login_rate_limits"].add(rate_key)
        operation = Operation(
            id=uuid4(),
            actor_kind="internal",
            command="maintenance-test",
            idempotency_scope="maintenance-test",
            idempotency_key_digest=uuid4().hex,
            request_digest=uuid4().hex,
        )
        db.add(operation)
        await db.flush()
        for index, (status, sent_at, removable) in enumerate(
            (
                ("sent", cutoff - timedelta(seconds=1), True),
                ("sent", cutoff, True),
                ("sent", now - timedelta(seconds=1), False),
                ("pending", None, False),
                ("processing", None, False),
                ("unknown", None, False),
                ("failed", None, False),
            )
        ):
            key = uuid4()
            db.add(
                OutboxMessage(
                    id=key,
                    operation_id=operation.id,
                    topic=f"test-{index}",
                    payload={},
                    status=status,
                    sent_at=sent_at,
                    created_at=now - timedelta(days=30),
                    available_at=now - timedelta(days=30),
                    lease_until=now + timedelta(hours=1) if status == "processing" else None,
                    worker_id="test-worker" if status == "processing" else None,
                )
            )
            if removable:
                eligible["sent_outbox_messages"].add(str(key))
        db.add(AuditLog(actor_kind="internal", action="maintenance-test"))
    return eligible


async def _purge(db: AsyncSession, *, dry_run: bool = True, limit: int = 100) -> PurgeCounts:
    return await purge_expired(
        db,
        login_window_seconds=1,
        session_retention_seconds=3600,
        sent_outbox_retention_seconds=3600,
        dry_run=dry_run,
        limit=limit,
    )


@pytest.mark.integration
async def test_pg_retention_and_protected_records(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, database = harness
    now = utcnow()
    monkeypatch.setattr(maintenance, "utcnow", lambda: now)
    eligible = await _seed(database, now)
    before = await _snapshot(database)
    async with database.sessions.begin() as db:
        counts = await _purge(db, dry_run=False)
    assert counts.as_dict() == COUNTS
    after = await _snapshot(database)
    assert after == {name: keys - eligible.get(name, set()) for name, keys in before.items()}
    async with database.sessions.begin() as db:
        assert set((await _purge(db, dry_run=False)).as_dict().values()) == {0}


@pytest.mark.integration
async def test_pg_dry_run_read_only_no_autoflush_and_cli(
    harness: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, database = harness
    now = utcnow()
    monkeypatch.setattr(maintenance, "utcnow", lambda: now)
    await _seed(database, now)
    before = await _snapshot(database)
    statements: list[str] = []

    def capture(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(database.engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with database.sessions() as db:
            await db.execute(text("SET TRANSACTION READ ONLY"))
            db.add(AuditLog(actor_kind="internal", action="must-not-flush"))
            assert (await _purge(db)).as_dict() == COUNTS
            await db.rollback()
    finally:
        event.remove(database.engine.sync_engine, "before_cursor_execute", capture)
    assert all(stmt.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for stmt in statements)
    assert not any("FOR UPDATE" in stmt for stmt in statements)
    assert await _snapshot(database) == before
    settings = app.state.settings.model_copy(
        update={
            "session_cleanup_retention_seconds": 3600,
            "outbox_cleanup_retention_seconds": 3600,
        }
    )
    monkeypatch.setattr(maintenance_cli, "get_settings", lambda: settings)
    assert await maintenance_cli.run_purge() == COUNTS
    assert await _snapshot(database) == before
    assert await maintenance_cli.run_purge(apply=True, limit=1) == dict.fromkeys(COUNTS, 1)


@pytest.mark.integration
async def test_pg_each_table_bounded_and_rollback(harness: Any) -> None:
    _, _, database = harness
    await _seed(database, utcnow() - timedelta(seconds=1))
    before = await _snapshot(database)
    async with database.sessions() as db:
        assert (await _purge(db, dry_run=False, limit=1)).as_dict() == dict.fromkeys(COUNTS, 1)
        await db.rollback()
    assert await _snapshot(database) == before
    async with database.sessions.begin() as db:
        assert (await _purge(db, dry_run=False, limit=1)).as_dict() == dict.fromkeys(COUNTS, 1)
    after = await _snapshot(database)
    assert all(len(before[name]) - len(after[name]) == 1 for name in COUNTS)
    assert all(after[name] == before[name] for name in ("accounts", "operations", "audit_logs"))


@pytest.mark.integration
async def test_pg_skip_locked_and_uncommitted_receipt(harness: Any) -> None:
    _, _, database = harness
    now = utcnow() - timedelta(seconds=1)
    eligible = await _seed(database, now)
    async with database.sessions() as locker:
        locked: dict[str, str] = {}
        for name in COUNTS:
            _, column = TABLES[name]
            keys = (await locker.scalars(select(column).order_by(column))).all()
            key = next(key for key in keys if str(key) in eligible[name])
            await locker.execute(select(column).where(column == key).with_for_update())
            locked[name] = str(key)
        uncommitted = IdempotencyRequest(
            id=uuid4(),
            scope="uncommitted",
            key_digest=uuid4().hex,
            request_digest=uuid4().hex,
            response_status=200,
            response_body={},
            created_at=now - timedelta(days=2),
            expires_at=now - timedelta(days=1),
        )
        locker.add(uncommitted)
        await locker.flush()
        async with database.sessions.begin() as cleaner:
            await cleaner.execute(text("SET LOCAL lock_timeout = '300ms'"))
            counts = await asyncio.wait_for(_purge(cleaner, dry_run=False, limit=1), timeout=3)
        assert counts.as_dict() == dict.fromkeys(COUNTS, 1)
        after = await _snapshot(database)
        assert all(key in after[name] for name, key in locked.items())
        assert await locker.get(IdempotencyRequest, uncommitted.id) is uncommitted
        await locker.rollback()


@pytest.mark.integration
async def test_pg_login_window_above_floor(harness: Any) -> None:
    _, _, database = harness
    now = utcnow()
    async with database.sessions.begin() as db:
        db.add_all(
            [
                LoginRateLimit(
                    scope_digest=str(age), window_started_at=now - timedelta(seconds=age)
                )
                for age in (3601, 7199, 7201)
            ]
        )
    async with database.sessions.begin() as db:
        counts = await purge_expired(db, login_window_seconds=7200, dry_run=False)
    assert counts.login_rate_limits == 1
    assert (await _snapshot(database))["login_rate_limits"] == {"3601", "7199"}
