import json

import pytest
from sqlalchemy import text
from test_auth_integration import harness as harness
from test_outbox import parameters, seed

from inviteflow.persistence.outbox import create_operation, lease_outbox
from inviteflow.safe_json import bounded_json_object


@pytest.mark.parametrize(
    "value",
    [
        {"values": [1e308] * 1000},
        {"n": 2**53},
        {"n": float("nan")},
        {"n": float("inf")},
        {"a": {1: "ambiguous"}},
        {"text": "\x00"},
        {"text": "\ud800"},
        {"n": (1, 2)},
        {"large": "界" * 22000},
    ],
)
def test_unportable_payloads_rejected(value):
    with pytest.raises(ValueError):
        bounded_json_object(value)


def test_cyclic_payload_is_rejected_and_valid_payload_copied():
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(ValueError, match="nesting"):
        bounded_json_object(cyclic)
    source = {"values": [None, True, 2**53 - 1, -0.5, 1e-300, "中文"]}
    copied = bounded_json_object(source)
    source["values"].append("changed")
    assert "changed" not in copied["values"]


@pytest.mark.integration
async def test_pg_expands_exponent_but_enqueue_rejects_poison_before_writing(harness):
    _, _, database = harness
    poison = {"values": [1e308] * 1000}
    encoded = json.dumps(poison)
    assert len(encoded) < 65536
    async with database.sessions() as db:
        expanded = await db.scalar(text("SELECT CAST(:payload AS JSONB)"), {"payload": encoded})
        assert len(json.dumps(expanded)) > 65536  # Reproduces actual PG serialization hazard.
    with pytest.raises(ValueError, match="safe range"):
        async with database.sessions.begin() as db:
            await create_operation(db, **parameters(outbox_payload=poison))
    valid = {"reference_id": "safe", "values": [2**53 - 1, -0.5, 1e-300, True]}
    await seed(database, outbox_payload=valid)
    async with database.sessions.begin() as db:
        messages = await lease_outbox(db, worker_id="test", topics=["test.dispatch"])
        assert len(messages) == 1 and messages[0].payload == valid
