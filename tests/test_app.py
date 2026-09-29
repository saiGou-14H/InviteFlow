from fastapi.testclient import TestClient

from inviteflow.app import create_app


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
