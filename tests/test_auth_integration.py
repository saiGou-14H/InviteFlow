import asyncio
import os
from datetime import timedelta
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select, text

from inviteflow.app import create_app
from inviteflow.cli import manage_admin
from inviteflow.config import Settings, get_settings
from inviteflow.persistence.models import (
    AuditLog,
    PublicSession,
    StaffAccount,
    utcnow,
)
from inviteflow.security import hash_password

pytestmark = pytest.mark.integration
ORIGIN = "https://testserver"
PASSWORD = "test-only-password-9274"


@pytest.fixture
async def harness():
    url = os.environ.get("INVITEFLOW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set INVITEFLOW_TEST_DATABASE_URL to an isolated migrated PostgreSQL database")
    parsed = urlsplit(url)
    if parsed.path != "/inviteflow_test" or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "postgres",
    }:
        pytest.fail(
            "Refusing to reset a database not explicitly named inviteflow_test on a test host"
        )
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url=url,
        session_secret="test-only-secret-" * 4,
        public_origin=ORIGIN,
        cookie_secure=True,
        business_hooks_enabled=False,
    )
    app = create_app(settings)
    database = app.state.database
    assert await database.check_ready(), "Run alembic upgrade head before integration tests"
    async with database.sessions.begin() as db:
        await db.execute(
            text(
                "TRUNCATE outbox_messages, operations, staff_sessions, public_sessions, staff_accounts, "
                "audit_logs, login_rate_limits, idempotency_requests CASCADE"
            )
        )
        db.add(StaffAccount(username_normalized="admin", password_hash=hash_password(PASSWORD)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
        yield app, client, database
    await database.dispose()


async def user_session(client):
    response = await client.post("/api/v1/public/sessions", json={}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data, {"Origin": ORIGIN, "X-CSRF-Token": data["csrf_token"]}


async def admin_session(client):
    response = await client.post(
        "/api/v1/staff/login",
        json={"username": " ADMIN ", "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    return data, {"Origin": ORIGIN, "X-CSRF-Token": data["csrf_token"]}


async def test_real_session_hashes_reuse_and_readiness(harness):
    app, client, database = harness
    assert (await client.get("/readyz")).status_code == 200
    data, headers = await user_session(client)
    again, _ = await user_session(client)
    assert again == data
    assert data["role"] == "user"
    token = client.cookies.get(app.state.settings.user_cookie_name)
    async with database.sessions() as db:
        session = await db.get(PublicSession, UUID(data["actor_id"]))
        assert session.token_hash != token and len(session.token_hash) == 64
        assert session.csrf_hash != data["csrf_token"]
    response = await client.post(
        "/api/v1/claims/batches", json={"codes": ["TEST"]}, headers=headers
    )
    assert response.status_code == 501
    assert response.json()["error"]["hook"] == "claim.claim_batch"
    assert (await client.get("/api/v1/public/session")).json()["data"] == data


async def test_unauthenticated_and_user_admin_boundary(harness):
    _, client, _ = harness
    assert (await client.post("/api/v1/admin/cdk-batches", json={"quantity": 1})).status_code == 401
    assert (
        await client.get("/api/v1/claims/00000000-0000-0000-0000-000000000001")
    ).status_code == 401
    _, headers = await user_session(client)
    response = await client.post("/api/v1/admin/cdk-batches", json={"quantity": 1}, headers=headers)
    assert response.status_code == 403


async def test_origin_csrf_and_cross_session_csrf(harness):
    app, client, _ = harness
    _, headers = await user_session(client)
    path = "/api/v1/claims/batches"
    payload = {"codes": ["TEST"]}
    for invalid in (
        {},
        {"Origin": ORIGIN},
        {**headers, "Origin": "https://evil.example"},
        {**headers, "Sec-Fetch-Site": "cross-site"},
    ):
        assert (await client.post(path, json=payload, headers=invalid)).status_code == 403
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as other:
        _, other_headers = await user_session(other)
        assert (await client.post(path, json=payload, headers=other_headers)).status_code == 403


async def test_admin_cookie_flags_session_rotation_and_logout(harness):
    app, client, database = harness
    data, headers = await admin_session(client)
    cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "__Host-if-admin")
    assert cookie.secure and cookie.path == "/"
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert data["role"] == "admin"
    response = await client.post("/api/v1/admin/cdk-batches", json={"quantity": 1}, headers=headers)
    assert response.status_code == 501
    assert (await client.get("/api/v1/public/session")).status_code == 401
    old_token = client.cookies.get(app.state.settings.admin_cookie_name)
    _, headers = await admin_session(client)
    assert client.cookies.get(app.state.settings.admin_cookie_name) != old_token
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=ORIGIN,
        cookies={app.state.settings.admin_cookie_name: old_token},
    ) as old:
        assert (await old.get("/api/v1/staff/session")).status_code == 401
    token = client.cookies.get(app.state.settings.admin_cookie_name)
    assert (await client.post("/api/v1/staff/logout", headers=headers)).status_code == 200
    client.cookies.set(app.state.settings.admin_cookie_name, token)
    assert (await client.get("/api/v1/staff/session")).status_code == 401
    async with database.sessions() as db:
        logs = (await db.scalars(select(AuditLog))).all()
        assert {item.action for item in logs} >= {"auth.login", "auth.logout"}
        assert PASSWORD not in repr([item.safe_metadata for item in logs])


async def test_expired_user_session_cannot_be_revived(harness):
    _, client, database = harness
    data, headers = await user_session(client)
    async with database.sessions.begin() as db:
        session = await db.get(PublicSession, UUID(data["actor_id"]))
        session.created_at = utcnow() - timedelta(days=2)
        session.expires_at = utcnow() - timedelta(seconds=1)
    assert (await client.get("/api/v1/public/session")).status_code == 401
    assert (await client.post("/api/v1/public/logout", headers=headers)).status_code == 401


async def test_account_epoch_and_disable_invalidate_sessions(harness):
    _, client, database = harness
    data, _ = await admin_session(client)
    async with database.sessions.begin() as db:
        account = await db.get(StaffAccount, UUID(data["actor_id"]))
        account.session_epoch += 1
    assert (await client.get("/api/v1/staff/session")).status_code == 401
    data, _ = await admin_session(client)
    async with database.sessions.begin() as db:
        account = await db.get(StaffAccount, UUID(data["actor_id"]))
        account.status = "disabled"
    assert (await client.get("/api/v1/staff/session")).status_code == 401


async def test_password_errors_rate_limit_and_forged_role(harness):
    _, client, _ = harness
    path = "/api/v1/staff/login"
    for _ in range(5):
        response = await client.post(
            path,
            json={"username": "missing", "password": "wrong-secret"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert "wrong-secret" not in response.text
    assert (
        await client.post(
            path, json={"username": "missing", "password": "wrong"}, headers={"Origin": ORIGIN}
        )
    ).status_code == 429
    response = await client.post(
        path,
        json={"username": "admin", "password": PASSWORD, "role": "admin"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text


async def test_parallel_login_limits_are_database_atomic(harness):
    app, client, _ = harness

    async def attempt():
        return await client.post(
            "/api/v1/staff/login",
            json={"username": "unknown", "password": "wrong"},
            headers={"Origin": ORIGIN},
        )

    results = await asyncio.gather(*(attempt() for _ in range(8)))
    assert sum(result.status_code == 401 for result in results) == 5
    assert sum(result.status_code == 429 for result in results) == 3


async def test_real_actor_passed_to_hook(harness):
    app, client, _ = harness
    data, headers = await user_session(client)
    called = []

    class TestHook:
        async def claim_batch(self, codes, *, actor_id):
            called.append(actor_id)
            return {"test_only": True}

    app.state.settings.business_hooks_enabled = True
    app.state.hooks.claims = TestHook()
    response = await client.post(
        "/api/v1/claims/batches", json={"codes": ["TEST"]}, headers=headers
    )
    assert response.status_code == 202
    assert called == [data["actor_id"]]


async def test_admin_cli_create_reset_revoke(harness, monkeypatch):
    app, client, database = harness
    monkeypatch.setenv(
        "INVITEFLOW_DATABASE_URL", app.state.settings.database_url.get_secret_value()
    )
    monkeypatch.setenv(
        "INVITEFLOW_SESSION_SECRET", app.state.settings.session_secret.get_secret_value()
    )
    get_settings.cache_clear()
    try:
        await manage_admin("create", "SecondAdmin", PASSWORD)
        with pytest.raises(ValueError, match="already exists"):
            await manage_admin("create", "secondadmin", PASSWORD)
        await manage_admin("reset-password", "secondadmin", PASSWORD + "new")
        await manage_admin("revoke-sessions", "secondadmin")
        async with database.sessions() as db:
            account = await db.scalar(
                select(StaffAccount).where(StaffAccount.username_normalized == "secondadmin")
            )
            assert account.session_epoch == 2
    finally:
        get_settings.cache_clear()
