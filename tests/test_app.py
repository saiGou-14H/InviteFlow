import pytest
from fastapi.testclient import TestClient

from inviteflow.app import create_app
from inviteflow.domain.roles import Role


client = TestClient(create_app())


def test_healthz() -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_capabilities_report_hooks_as_reserved() -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["implemented"] == []
    assert "claim.confirm" in body["reserved_hooks"]


def test_unimplemented_business_hook_is_explicit() -> None:
    response = client.post("/api/v1/claims/batches", json={"codes": ["EXAMPLE"]})
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "HOOK_NOT_IMPLEMENTED"
    assert response.json()["error"]["hook"] == "claim.claim_batch"


def test_capabilities_only_advertise_user_and_admin_roles() -> None:
    body = client.get("/api/v1/capabilities").json()
    assert body["supported_roles"] == ["user", "admin"]
    assert not any(name.startswith("dealer.") for name in body["reserved_hooks"])


@pytest.mark.parametrize("value", ["dealer", "visitor", "worker"])
def test_other_product_roles_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        Role(value)


@pytest.mark.parametrize(
    "path,payload",
    [
        ("/api/v1/dealer/cdks/lookup", {"code": "EXAMPLE"}),
        (
            "/api/v1/dealer/claims/00000000-0000-0000-0000-000000000001/probe-resend",
            {},
        ),
    ],
)
def test_removed_dealer_routes_return_not_found(path: str, payload: dict[str, str]) -> None:
    assert client.post(path, json=payload).status_code == 404


def test_openapi_has_no_dealer_contract() -> None:
    schema = client.get("/openapi.json").json()
    assert not any("/dealer/" in path for path in schema["paths"])
    assert "DealerLookupRequest" not in schema["components"]["schemas"]


@pytest.mark.parametrize(
    "method,path,payload,hook",
    [
        ("GET", "/claims/{id}", None, "claim.get_snapshot"),
        ("POST", "/claims/{id}/confirm", None, "claim.confirm"),
        ("POST", "/claims/{id}/retry", {"reason": "test"}, "claim.retry"),
        ("POST", "/claims/{id}/followup", None, "claim.follow_up"),
        ("POST", "/admin/cdk-batches", {"quantity": 1}, "admin.create_cdk_batch"),
        (
            "POST",
            "/admin/claims/{id}/reconcile",
            {"reason": "test"},
            "admin.reconcile_claim",
        ),
    ],
)
def test_remaining_user_and_admin_routes_still_use_placeholders(
    method: str, path: str, payload: dict[str, object] | None, hook: str
) -> None:
    url = "/api/v1" + path.format(id="00000000-0000-0000-0000-000000000001")
    response = client.request(method, url, json=payload)
    assert response.status_code == 501
    assert response.json()["error"]["code"] == "HOOK_NOT_IMPLEMENTED"
    assert response.json()["error"]["hook"] == hook
