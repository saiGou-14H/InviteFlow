"""Persistence checks compile PostgreSQL SQL; no live database is used."""

import ast
import asyncio
import inspect
from datetime import timedelta, timezone
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import asyncpg
import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from alembic import command
from inviteflow.persistence import (
    EXPECTED_REVISION,
    AuditLog,
    Base,
    Database,
    IdempotencyRequest,
    LoginRateLimit,
    Operation,
    OutboxMessage,
    PublicSession,
    StaffAccount,
    StaffSession,
    utcnow,
)

ROOT = Path(__file__).resolve().parents[1]
TEST_URL = "postgresql+asyncpg://offline:password%25safe@invalid.example/inviteflow"
MODELS = (
    StaffAccount,
    PublicSession,
    StaffSession,
    IdempotencyRequest,
    Operation,
    OutboxMessage,
    AuditLog,
    LoginRateLimit,
)


def migration_config(output: StringIO | None = None) -> Config:
    return Config(str(ROOT / "alembic.ini"), output_buffer=output)


def test_public_model_contract() -> None:
    session_fields = {
        "id",
        "token_hash",
        "csrf_hash",
        "created_at",
        "expires_at",
        "absolute_expires_at",
        "revoked_at",
    }
    expected = {
        "staff_accounts": {
            "id",
            "username_normalized",
            "password_hash",
            "role",
            "status",
            "session_epoch",
            "created_at",
        },
        "public_sessions": session_fields,
        "staff_sessions": session_fields | {"account_id", "session_epoch"},
        "idempotency_requests": {
            "id",
            "scope",
            "key_digest",
            "request_digest",
            "response_status",
            "response_body",
            "created_at",
            "expires_at",
        },
        "operations": {
            "id",
            "actor_kind",
            "actor_id",
            "command",
            "idempotency_scope",
            "idempotency_key_digest",
            "request_digest",
            "status",
            "attempts",
            "generation",
            "result_body",
            "error_code",
            "available_at",
            "lease_until",
            "created_at",
            "updated_at",
        },
        "outbox_messages": {
            "id",
            "operation_id",
            "topic",
            "payload",
            "status",
            "attempts",
            "generation",
            "available_at",
            "lease_until",
            "worker_id",
            "last_error_code",
            "sent_at",
            "created_at",
            "updated_at",
        },
        "audit_logs": {
            "id",
            "actor_kind",
            "actor_id",
            "action",
            "target",
            "created_at",
            "safe_metadata",
        },
        "login_rate_limits": {"scope_digest", "window_started_at", "attempts"},
    }
    assert {name: set(table.c.keys()) for name, table in Base.metadata.tables.items()} == expected
    assert set(Base.metadata.naming_convention) >= {"ix", "uq", "ck", "fk", "pk"}
    assert {model.__table__.name for model in MODELS} == set(expected)
    lengths = {
        StaffAccount: {"username_normalized": 128, "password_hash": 512, "role": 16, "status": 16},
        PublicSession: {"token_hash": 64, "csrf_hash": 64},
        StaffSession: {"token_hash": 64, "csrf_hash": 64},
        IdempotencyRequest: {"scope": 256, "key_digest": 64, "request_digest": 64},
        Operation: {
            "actor_kind": 16,
            "actor_id": 128,
            "command": 64,
            "idempotency_scope": 256,
            "idempotency_key_digest": 64,
            "request_digest": 64,
            "status": 16,
            "error_code": 64,
        },
        OutboxMessage: {
            "topic": 128,
            "status": 16,
            "worker_id": 128,
            "last_error_code": 64,
        },
        AuditLog: {"actor_kind": 16, "actor_id": 128, "action": 64, "target": 128},
        LoginRateLimit: {"scope_digest": 64},
    }
    for model, fields in lengths.items():
        for field, length in fields.items():
            assert model.__table__.c[field].type.length == length


def test_nullability_timezone_and_python_defaults() -> None:
    nullable = {
        "public_sessions": {"revoked_at"},
        "staff_sessions": {"revoked_at"},
        "idempotency_requests": {"response_status", "response_body"},
        "operations": {"actor_id", "result_body", "error_code", "lease_until"},
        "outbox_messages": {"lease_until", "worker_id", "last_error_code", "sent_at"},
        "audit_logs": {"actor_id", "target"},
    }
    for model in MODELS:
        table = model.__table__
        assert {column.name for column in table.c if column.nullable} == nullable.get(
            table.name, set()
        )
        for column in table.c:
            if isinstance(column.type, sa.DateTime):
                assert column.type.timezone is True
        if "id" in table.c:
            assert table.c.id.primary_key
            first, second = table.c.id.default.arg(None), table.c.id.default.arg(None)
            assert isinstance(first, UUID) and first.version == 4 and first != second
    assert utcnow().tzinfo == timezone.utc
    assert utcnow().utcoffset() == timedelta(0)
    assert StaffAccount.__table__.c.created_at.default.arg(None).tzinfo == timezone.utc
    assert StaffAccount.__table__.c.role.default.arg == "admin"
    assert StaffAccount.__table__.c.status.default.arg == "active"
    assert StaffAccount.__table__.c.session_epoch.default.arg == 0
    assert StaffSession.__table__.c.session_epoch.default.arg == 0
    metadata_default = AuditLog.__table__.c.safe_metadata.default.arg
    first_metadata, second_metadata = metadata_default(None), metadata_default(None)
    assert first_metadata == second_metadata == {}
    assert first_metadata is not second_metadata
    assert isinstance(AuditLog.__table__.c.safe_metadata.type, postgresql.JSONB)
    assert isinstance(IdempotencyRequest.__table__.c.response_body.type, postgresql.JSONB)
    assert isinstance(Operation.__table__.c.result_body.type, postgresql.JSONB)
    assert isinstance(OutboxMessage.__table__.c.payload.type, postgresql.JSONB)


def test_postgresql_ddl_constraints_and_indexes() -> None:
    dialect = postgresql.dialect()
    ddl = "\n".join(
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    )
    assert "dealer" not in ddl.lower()
    for fragment in (
        "TIMESTAMP WITH TIME ZONE",
        "JSONB",
        "UUID",
        "CHECK (role = 'admin')",
        "CHECK (status IN ('active', 'disabled'))",
        "CHECK (attempts >= 0)",
        "UNIQUE (scope, key_digest)",
        "UNIQUE (username_normalized)",
        "ON DELETE RESTRICT",
        "CHECK (absolute_expires_at >= expires_at)",
        "CHECK (status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'cancelled'))",
        "CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'unknown'))",
        "UNIQUE (idempotency_scope, idempotency_key_digest)",
        "UNIQUE (operation_id, topic)",
        "ON DELETE RESTRICT",
    ):
        assert fragment in ddl
    assert ddl.count("UNIQUE (token_hash)") == 2
    for table in Base.metadata.tables.values():
        assert all(constraint.name for constraint in table.constraints)
        for index in table.indexes:
            assert index.name
            assert "CREATE INDEX" in str(CreateIndex(index).compile(dialect=dialect))
    fk = next(iter(StaffSession.__table__.c.account_id.foreign_keys))
    assert fk.target_fullname == "staff_accounts.id"
    assert fk.ondelete == "RESTRICT"
    indexes = {index.name for table in Base.metadata.tables.values() for index in table.indexes}
    assert indexes == {
        "ix_public_sessions_expires_at",
        "ix_staff_sessions_expires_at",
        "ix_staff_sessions_account_id",
        "ix_idempotency_requests_expires_at",
        "ix_operations_available_at",
        "ix_operations_created_at",
        "ix_outbox_messages_operation_id",
        "ix_outbox_messages_available_at",
        "ix_outbox_messages_created_at",
        "ix_audit_logs_created_at",
        "ix_login_rate_limits_window_started_at",
    }


def test_revision_chain_is_single_explicit_head() -> None:
    scripts = ScriptDirectory.from_config(migration_config())
    assert EXPECTED_REVISION == "0002_operations_outbox"
    assert scripts.get_heads() == [EXPECTED_REVISION]
    revisions = list(scripts.walk_revisions())
    assert len(revisions) == 2
    assert revisions[0].revision == EXPECTED_REVISION
    assert revisions[0].down_revision == "0001_foundation"
    assert revisions[1].down_revision is None
    for revision in revisions:
        source = inspect.getsource(revision.module)
        assert "create_all" not in source and "drop_all" not in source
        assert "inviteflow.persistence" not in source and "Base.metadata" not in source
        assert "op.create_table(" in source


def test_migration_matches_model_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = ScriptDirectory.from_config(migration_config())
    metadata = sa.MetaData()

    def create_table(name: str, *columns: Any, **kwargs: Any) -> sa.Table:
        return sa.Table(name, metadata, *columns, **kwargs)

    def create_index(name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        sa.Index(name, *(metadata.tables[table_name].c[column] for column in columns), **kwargs)

    for revision_id in ("0001_foundation", EXPECTED_REVISION):
        revision = scripts.get_revision(revision_id)
        assert revision is not None
        monkeypatch.setattr(revision.module.op, "f", lambda name: name)
        monkeypatch.setattr(revision.module.op, "create_table", create_table)
        monkeypatch.setattr(revision.module.op, "create_index", create_index)
        revision.module.upgrade()
    assert set(metadata.tables) == set(Base.metadata.tables)
    dialect = postgresql.dialect()

    def signature(table: sa.Table) -> dict[str, Any]:
        return {
            "columns": {
                column.name: (
                    str(column.type.compile(dialect=dialect)),
                    column.nullable,
                    str(column.server_default.arg) if column.server_default else None,
                )
                for column in table.c
            },
            "constraints": {
                str(constraint.name): (
                    type(constraint).__name__,
                    tuple(column.name for column in constraint.columns),
                    str(constraint.sqltext) if isinstance(constraint, sa.CheckConstraint) else None,
                )
                for constraint in table.constraints
            },
            "foreign_keys": {
                (fk.parent.name, fk.target_fullname, fk.ondelete) for fk in table.foreign_keys
            },
            "indexes": {
                (index.name, tuple(column.name for column in index.columns), index.unique)
                for index in table.indexes
            },
        }

    for name, table in Base.metadata.tables.items():
        assert signature(metadata.tables[name]) == signature(table), name


def test_offline_upgrade_and_downgrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INVITEFLOW_DATABASE_URL", TEST_URL)
    connect = AsyncMock(side_effect=AssertionError("Offline migration must not connect"))
    monkeypatch.setattr(asyncpg, "connect", connect)
    output = StringIO()
    command.upgrade(migration_config(output), "head", sql=True)
    sql = output.getvalue()
    assert "password%25safe" not in sql and "password%safe" not in sql
    assert "invalid.example" not in sql
    assert "dealer" not in sql.lower()
    assert "INSERT INTO alembic_version" in sql
    assert EXPECTED_REVISION in sql
    for table in Base.metadata.tables.values():
        assert f"CREATE TABLE {table.name} (" in sql
        for constraint in table.constraints:
            assert f"CONSTRAINT {constraint.name} " in sql
        for index in table.indexes:
            assert f"CREATE INDEX {index.name} ON {table.name}" in sql
    assert sql.index("CREATE TABLE staff_accounts") < sql.index("CREATE TABLE staff_sessions")
    output = StringIO()
    command.downgrade(migration_config(output), f"{EXPECTED_REVISION}:base", sql=True)
    sql = output.getvalue()
    for table in Base.metadata.tables.values():
        assert f"DROP TABLE {table.name};" in sql
        for index in table.indexes:
            assert f"DROP INDEX {index.name};" in sql
    assert sql.index("DROP TABLE staff_sessions") < sql.index("DROP TABLE staff_accounts")
    connect.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "",
        "secret-bad-url",
        "sqlite+aiosqlite:///tmp/test.db",
        "postgresql://user:secret@localhost/db",
        "postgresql+psycopg://user:secret@localhost/db",
        "mysql+asyncpg://user:secret@localhost/db",
        "postgresql+asyncpg://user:secret@localhost:notaport/db",
    ],
)
def test_database_rejects_invalid_urls_without_secrets(url: str) -> None:
    with pytest.raises(ValueError, match=r"must use postgresql\+asyncpg") as error:
        Database(url)
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("url", [None, "sqlite:///tmp/test.db", "secret-bad-url"])
def test_migration_rejects_missing_or_unsupported_url(
    monkeypatch: pytest.MonkeyPatch, url: str | None
) -> None:
    if url is None:
        monkeypatch.delenv("INVITEFLOW_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("INVITEFLOW_DATABASE_URL", url)
    with pytest.raises(ValueError, match=r"must use postgresql\+asyncpg") as error:
        command.upgrade(migration_config(StringIO()), "head", sql=True)
    assert "secret" not in str(error.value)


def test_database_construction_is_lazy(monkeypatch: pytest.MonkeyPatch) -> None:
    connect = AsyncMock(side_effect=AssertionError("Construction must not connect"))
    monkeypatch.setattr(asyncpg, "connect", connect)
    database = Database(TEST_URL)
    assert database.engine.url.drivername == "postgresql+asyncpg"
    assert database.engine.sync_engine.hide_parameters is True
    assert database.engine.pool._pre_ping is True
    assert database.sessions.kw["expire_on_commit"] is False
    assert database.sessions.kw["bind"] is database.engine
    asyncio.run(database.dispose())
    connect.assert_not_called()


@pytest.mark.parametrize(
    "revisions,expected",
    [
        ([EXPECTED_REVISION], True),
        ([], False),
        (["old_revision"], False),
        (["0002_future"], False),
        ([EXPECTED_REVISION, "branch"], False),
        ([EXPECTED_REVISION, EXPECTED_REVISION], False),
    ],
)
def test_readiness_requires_exact_single_revision(revisions: list[str], expected: bool) -> None:
    database = Database(TEST_URL)
    engine = MagicMock()
    connection = AsyncMock()
    probe, versions = MagicMock(), MagicMock()
    probe.scalar_one.return_value = 1
    versions.scalars.return_value.all.return_value = revisions
    connection.execute.side_effect = [probe, versions]
    engine.connect.return_value.__aenter__.return_value = connection
    original_engine = database.engine
    database.engine = engine
    assert asyncio.run(database.check_ready()) is expected
    assert [str(call.args[0]) for call in connection.execute.await_args_list] == [
        "SELECT 1",
        "SELECT version_num FROM alembic_version",
    ]
    engine.connect.return_value.__aexit__.assert_awaited_once()
    asyncio.run(original_engine.dispose())


@pytest.mark.parametrize("stage", ["connect", "probe", "version", "bad_probe"])
def test_readiness_fails_closed(stage: str) -> None:
    database = Database(TEST_URL)
    engine = MagicMock()
    connection = AsyncMock()
    probe = MagicMock()
    probe.scalar_one.return_value = 0 if stage == "bad_probe" else 1
    connection.execute.side_effect = (
        RuntimeError("unavailable")
        if stage == "probe"
        else [probe, RuntimeError("missing alembic_version")]
    )
    engine.connect.return_value.__aenter__.return_value = connection
    if stage == "connect":
        engine.connect.return_value.__aenter__.side_effect = RuntimeError("unavailable")
    original_engine = database.engine
    database.engine = engine
    assert asyncio.run(database.check_ready()) is False
    asyncio.run(original_engine.dispose())


def test_dispose_delegates_to_engine() -> None:
    database = Database(TEST_URL)
    original_engine = database.engine
    engine = MagicMock()
    engine.dispose = AsyncMock()
    database.engine = engine
    asyncio.run(database.dispose())
    engine.dispose.assert_awaited_once_with()
    asyncio.run(original_engine.dispose())


def test_owned_python_sources_support_python310() -> None:
    sources = [
        *ROOT.joinpath("src/inviteflow/persistence").glob("*.py"),
        *ROOT.joinpath("alembic").rglob("*.py"),
    ]
    for source in sources:
        ast.parse(source.read_text(), filename=str(source), feature_version=(3, 10))
