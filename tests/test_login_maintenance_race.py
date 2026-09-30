"""Real PostgreSQL interleavings; run only with the shared isolated harness."""

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from test_auth_integration import ORIGIN, PASSWORD
from test_auth_integration import harness as harness

from inviteflow.maintenance import purge_expired
from inviteflow.persistence.models import LoginRateLimit, utcnow
from inviteflow.security import digest

pytestmark = pytest.mark.integration


def _scopes(secret: str) -> list[str]:
    # The harness ASGITransport uses the default client address 127.0.0.1.
    return sorted(
        [digest(secret, "login-account", "admin"), digest(secret, "login-peer", "127.0.0.1")]
    )


@pytest.mark.parametrize("password,expected_status", [(PASSWORD, 200), ("wrong-secret", 401)])
async def test_login_window_remains_locked_during_maintenance(
    harness: Any, monkeypatch: pytest.MonkeyPatch, password: str, expected_status: int
) -> None:
    app, client, database = harness
    settings = app.state.settings
    secret = settings.session_secret.get_secret_value()
    scopes = _scopes(secret)
    unrelated = digest(secret, "login-account", "unrelated-expired-window")
    old = utcnow() - timedelta(hours=2)
    async with database.sessions.begin() as db:
        db.add_all(
            [
                LoginRateLimit(
                    scope_digest=scopes[0],
                    window_started_at=old,
                    attempts=settings.login_attempt_limit,
                ),
                LoginRateLimit(scope_digest=scopes[1], window_started_at=utcnow(), attempts=2),
                LoginRateLimit(scope_digest=unrelated, window_started_at=old, attempts=1),
            ]
        )

    original_execute = AsyncSession.execute
    original_scalar = AsyncSession.scalar
    insert_scopes: list[str] = []
    cleanup_counts: list[int] = []
    cleaned = False

    async def after_insert(statement: Any) -> None:
        nonlocal cleaned
        if not getattr(statement, "is_insert", False):
            return
        if statement.table.name != LoginRateLimit.__tablename__:
            return
        insert_scopes.append(statement.compile().params["scope_digest"])
        if cleaned:
            return
        cleaned = True
        # Suspend login after its INSERT/UPSERT completed, before it resets or
        # increments the window. This is the old INSERT/SELECT deletion gap.
        # Intercept both methods so reverting to execute(DO NOTHING) reproduces
        # deletion followed by the old SELECT's "row is not None" assertion.
        async with database.sessions.begin() as cleaner:
            await cleaner.execute(text("SET LOCAL lock_timeout = '300ms'"))
            counts = await asyncio.wait_for(
                purge_expired(
                    cleaner,
                    login_window_seconds=settings.login_window_seconds,
                    dry_run=False,
                ),
                timeout=3,
            )
            cleanup_counts.append(counts.login_rate_limits)

    async def execute_wrapper(
        self: AsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        result = await original_execute(self, statement, *args, **kwargs)
        await after_insert(statement)
        return result

    async def scalar_wrapper(
        self: AsyncSession, statement: Any, *args: Any, **kwargs: Any
    ) -> Any:
        result = await original_scalar(self, statement, *args, **kwargs)
        await after_insert(statement)
        return result

    monkeypatch.setattr(AsyncSession, "execute", execute_wrapper)
    monkeypatch.setattr(AsyncSession, "scalar", scalar_wrapper)
    response = await client.post(
        "/api/v1/staff/login",
        json={"username": "admin", "password": password},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == expected_status, response.text
    assert insert_scopes == scopes
    # Cleanup really ran: it removed the unrelated eligible row, while SKIP
    # LOCKED skipped the still-expired window held by the login transaction.
    assert cleanup_counts == [1]
    async with database.sessions() as db:
        rows = {row.scope_digest: row for row in await db.scalars(select(LoginRateLimit))}
    assert set(rows) == set(scopes)
    assert rows[scopes[0]].window_started_at > old
    assert rows[scopes[0]].attempts == 1
    assert rows[scopes[1]].attempts == 3


@pytest.mark.parametrize("exhausted_index", [0, 1])
async def test_exhausted_scope_still_consumes_other_scope(
    harness: Any, exhausted_index: int
) -> None:
    app, client, database = harness
    settings = app.state.settings
    scopes = _scopes(settings.session_secret.get_secret_value())
    async with database.sessions.begin() as db:
        db.add_all(
            [
                LoginRateLimit(
                    scope_digest=scope,
                    window_started_at=utcnow(),
                    attempts=settings.login_attempt_limit if index == exhausted_index else 2,
                )
                for index, scope in enumerate(scopes)
            ]
        )
    response = await client.post(
        "/api/v1/staff/login",
        json={"username": "admin", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 429
    async with database.sessions() as db:
        rows = {row.scope_digest: row for row in await db.scalars(select(LoginRateLimit))}
    assert rows[scopes[exhausted_index]].attempts == settings.login_attempt_limit
    assert rows[scopes[1 - exhausted_index]].attempts == 3
