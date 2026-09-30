"""Regression: locking a row must refresh an existing SQLAlchemy identity-map object."""

from uuid import uuid4

import pytest
from test_auth_integration import harness as harness
from test_outbox import seed

from inviteflow.persistence.models import Operation, OutboxMessage
from inviteflow.persistence.outbox import (
    LeaseLostError,
    OperationStateError,
    lease_outbox,
    mark_outbox_sent,
    mark_outbox_unknown,
    resolve_unknown_operation,
    transition_operation,
)

pytestmark = pytest.mark.integration


async def test_stale_operation_cannot_acquire_same_generation(harness):
    _, _, database = harness
    operation_id = await seed(database)
    async with database.sessions() as stale:
        cached = await stale.get(Operation, operation_id)
        await stale.commit()  # expire_on_commit=False deliberately retains the reference.
        async with database.sessions.begin() as other:
            await transition_operation(
                other,
                operation_id,
                expected_status="pending",
                expected_generation=0,
                new_status="running",
            )
        assert cached.status == "pending" and cached.generation == 0
        with pytest.raises(OperationStateError):
            async with stale.begin():
                await transition_operation(
                    stale,
                    operation_id,
                    expected_status="pending",
                    expected_generation=0,
                    new_status="running",
                )
    async with database.sessions() as db:
        row = await db.get(Operation, operation_id)
        assert row.status == "running" and row.generation == row.attempts == 1


async def test_stale_ack_cannot_overwrite_unknown(harness):
    _, _, database = harness
    await seed(database)
    async with database.sessions.begin() as db:
        message = (await lease_outbox(db, worker_id="worker", topics=["test.dispatch"]))[0]
    async with database.sessions.begin() as stale:
        cached = await stale.get(OutboxMessage, message.id)
        async with database.sessions.begin() as other:
            await mark_outbox_unknown(
                other, message.id, worker_id="worker", generation=1, error_code="UNCERTAIN_RESULT"
            )
        assert cached.status == "processing" and cached.generation == 1
        with pytest.raises(LeaseLostError):
            await mark_outbox_sent(stale, message.id, worker_id="worker", generation=1)
    async with database.sessions() as db:
        row = await db.get(OutboxMessage, message.id)
        assert row.status == "unknown" and row.generation == 2 and row.sent_at is None


async def test_stale_resolution_cannot_replace_confirmed_result(harness):
    _, _, database = harness
    operation_id = await seed(database)
    async with database.sessions.begin() as db:
        await transition_operation(
            db, operation_id, expected_status="pending", expected_generation=0, new_status="running"
        )
        await transition_operation(
            db, operation_id, expected_status="running", expected_generation=1, new_status="unknown"
        )
    first_evidence = uuid4()
    async with database.sessions.begin() as stale:
        cached = await stale.get(Operation, operation_id)
        async with database.sessions.begin() as other:
            await resolve_unknown_operation(
                other,
                operation_id,
                expected_generation=2,
                confirmed_success=True,
                evidence_id=first_evidence,
                admin_id=uuid4(),
            )
        assert cached.status == "unknown"
        with pytest.raises(OperationStateError):
            await resolve_unknown_operation(
                stale,
                operation_id,
                expected_generation=2,
                confirmed_success=False,
                evidence_id=uuid4(),
                admin_id=uuid4(),
            )
    async with database.sessions() as db:
        row = await db.get(Operation, operation_id)
        assert row.status == "succeeded"
        assert row.result_body == {"evidence_id": str(first_evidence)}
