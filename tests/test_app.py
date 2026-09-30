from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from inviteflow.app import create_app
from inviteflow.auth import Actor, require_admin, require_user
from inviteflow.config import Settings
from inviteflow.domain.roles import Role


@pytest.fixture
def client():
    app = create_app(Settings(_env_file=None, database_url=None, session_secret=None))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def authorized_client(client):
    # Unit-only boundary override; integration tests use real cookies and PostgreSQL.
    actor_id = UUID("00000000-0000-0000-0000-000000000001")
    client.app.dependency_overrides[require_user] = lambda: Actor(
        actor_id, actor_id, Role.USER, "test"
    )
    client.app.dependency_overrides[require_admin] = lambda: Actor(
        actor_id, actor_id, Role.ADMIN, "test"
    )
    return client


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["cache-control"] == "no-store"


def test_readyz_requires_database(client):
    assert client.get("/readyz").status_code == 503
    assert client.get("/readyz").json()["persistence"] == "not_configured"


def test_no_database_cannot_authenticate(client):
    response = client.post("/api/v1/claims/batches", json={"codes": ["TEST"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "PERSISTENCE_NOT_CONFIGURED"


def test_capabilities_report_hooks_as_reserved(client):
    body = client.get("/api/v1/capabilities").json()
    assert body["implemented"] == []
    assert "claim.confirm" in body["reserved_hooks"]
    assert "claim.get_snapshot" in body["reserved_hooks"]
    assert body["supported_roles"] == ["user", "admin"]
    assert not any(name.startswith("dealer.") for name in body["reserved_hooks"])


@pytest.mark.parametrize("value", ["dealer", "visitor", "worker"])
def test_other_product_roles_are_rejected(value):
    with pytest.raises(ValueError):
        Role(value)


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/v1/dealer/cdks/lookup", {"code": "EXAMPLE"}),
        ("/api/v1/dealer/claims/00000000-0000-0000-0000-000000000001/probe-resend", {}),
    ],
)
def test_removed_dealer_routes_return_not_found(client, path, payload):
    assert client.post(path, json=payload).status_code == 404


def test_openapi_has_no_dealer_contract(client):
    schema = client.get("/openapi.json").json()
    assert not any("/dealer/" in path for path in schema["paths"])
    assert "DealerLookupRequest" not in schema["components"]["schemas"]


@pytest.mark.parametrize(
    "method,path,payload,hook",
    [
        ("POST", "/claims/batches", {"codes": ["TEST"]}, "claim.claim_batch"),
        ("GET", "/claims/{id}", None, "claim.get_snapshot"),
        ("POST", "/claims/{id}/confirm", None, "claim.confirm"),
        ("POST", "/claims/{id}/retry", {"reason": "test"}, "claim.retry"),
        ("POST", "/claims/{id}/followup", None, "claim.follow_up"),
        ("POST", "/admin/cdk-batches", {"quantity": 1}, "admin.create_cdk_batch"),
        ("POST", "/admin/claims/{id}/reconcile", {"reason": "test"}, "admin.reconcile_claim"),
    ],
)
def test_authenticated_routes_still_use_placeholders(
    authorized_client, method, path, payload, hook
):
    url = "/api/v1" + path.format(id="00000000-0000-0000-0000-000000000001")
    response = authorized_client.request(method, url, json=payload)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "HOOK_NOT_IMPLEMENTED"
    assert response.json()["error"]["hook"] == hook


def test_disabled_hook_gate_prevents_injected_implementation(authorized_client):
    class UnsafeHook:
        async def claim_batch(self, *args, **kwargs):
            raise AssertionError("Disabled hooks must never execute")

    authorized_client.app.state.hooks.claims = UnsafeHook()
    response = authorized_client.post("/api/v1/claims/batches", json={"codes": ["TEST"]})
    assert response.status_code == 501


def test_sensitive_validation_input_is_not_echoed(client):
    password = "PRIVATE_PASSWORD" * 50
    response = client.post("/api/v1/staff/login", json={"username": "a", "password": password})
    assert response.status_code == 422
    assert "PRIVATE_PASSWORD" not in response.text
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_request_size_limit(client):
    response = client.post("/api/v1/staff/login", content=b"x" * 262145)
    assert response.status_code == 413


def test_origin_required_even_for_session_creation(client):
    response = client.post("/api/v1/public/sessions", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ORIGIN_REJECTED"
