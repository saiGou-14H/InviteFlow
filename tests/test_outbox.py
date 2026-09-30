import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from test_auth_integration import harness as harness

from inviteflow.idempotency import CommandResult, IdempotencyExecutor
from inviteflow.persistence.models import AuditLog, Operation, OutboxMessage, utcnow
from inviteflow.persistence.outbox import (
    LeaseLostError,
    OperationBusyError,
    OperationConflictError,
    OperationStateError,
    create_operation,
    lease_outbox,
    mark_outbox_sent,
    mark_outbox_unknown,
    quarantine_expired_leases,
    renew_outbox_lease,
    resolve_unknown_operation,
    transition_operation,
)

pytestmark = pytest.mark.integration


def parameters(key="one", **overrides):
    return (
        dict(
            actor_kind="internal",
            actor_id=None,
            command="test.command",
            idempotency_scope="internal:test",
            idempotency_key_digest=hashlib.sha256(key.encode()).hexdigest(),
            request_digest=hashlib.sha256(b"safe-request").hexdigest(),
            outbox_topic="test.dispatch",
            outbox_payload={"reference_id": str(uuid4())},
        )
        | overrides
    )


async def seed(database, key="one", **overrides):
    async with database.sessions.begin() as db:
        operation = await create_operation(db, **parameters(key, **overrides))
        return operation.id


async def test_create_is_atomic_and_requires_explicit_transaction(harness):
    _, _, database = harness
    async with database.sessions() as db:
        with pytest.raises(ValueError, match="transaction"):
            await create_operation(db, **parameters())
    with pytest.raises(RuntimeError, match="rollback"):
        async with database.sessions.begin() as db:
            await create_operation(db, **parameters())
            raise RuntimeError("rollback")
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Operation)) == 0
        assert await db.scalar(select(func.count()).select_from(OutboxMessage)) == 0


async def test_durable_identity_deduplicates_and_conflicts(harness):
    _, _, database = harness
    args = parameters()
    async with database.sessions.begin() as db:
        first = await create_operation(db, **args)
        identifier = first.id
    async with database.sessions.begin() as db:
        same = await create_operation(db, **args)
        assert same.id == identifier
    with pytest.raises(OperationConflictError):
        async with database.sessions.begin() as db:
            await create_operation(db, **(args | {"request_digest": "a" * 64}))
    async with database.sessions.begin() as db:
        await db.execute(delete(OutboxMessage).where(OutboxMessage.operation_id == identifier))
    # A swept delivery/receipt does not cause another notification on replay.
    async with database.sessions.begin() as db:
        assert (await create_operation(db, **args)).id == identifier
        assert await db.scalar(select(func.count()).select_from(OutboxMessage)) == 0


async def test_uncommitted_identity_reports_in_progress(harness):
    _, _, database = harness
    args = parameters()
    async with database.sessions.begin() as first:
        await create_operation(first, **args)
        with pytest.raises(OperationBusyError):
            async with database.sessions.begin() as second:
                await create_operation(second, **args)


async def test_idempotency_receipt_and_operation_commit_together(harness):
    app, _, database = harness
    executor = IdempotencyExecutor(database, app.state.settings.session_secret.get_secret_value())
    calls = []

    async def command(db):
        calls.append(True)
        operation = await create_operation(db, **parameters())
        return CommandResult(202, {"operation_id": str(operation.id)})

    first = await executor.execute(
        scope="internal:test", key="receipt-key", payload={}, command=command
    )
    replay = await executor.execute(
        scope="internal:test", key="receipt-key", payload={}, command=command
    )
    assert first.body == replay.body and replay.replayed and len(calls) == 1
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Operation)) == 1
        assert await db.scalar(select(func.count()).select_from(OutboxMessage)) == 1


async def test_skip_locked_workers_lease_disjoint_rows(harness):
    _, _, database = harness
    for index in range(4):
        await seed(database, str(index))
    await seed(database, "other-topic", outbox_topic="other.dispatch")
    async with database.sessions.begin() as first:
        group_a = await lease_outbox(first, worker_id="a", topics=["test.dispatch"], limit=2)
        async with database.sessions.begin() as second:
            group_b = await lease_outbox(second, worker_id="b", topics=["test.dispatch"], limit=2)
        assert len(group_a) == len(group_b) == 2
        assert not ({x.id for x in group_a} & {x.id for x in group_b})
        assert {x.generation for x in group_a + group_b} == {1}
    async with database.sessions.begin() as db:
        assert await lease_outbox(db, worker_id="c", topics=["test.dispatch"]) == []


async def test_ack_checks_owner_generation_and_is_not_business_success(harness):
    _, _, database = harness
    operation_id = await seed(database)
    async with database.sessions.begin() as db:
        message = (await lease_outbox(db, worker_id="a", topics=["test.dispatch"]))[0]
    for owner, generation in [("wrong", message.generation), ("a", message.generation - 1)]:
        with pytest.raises(LeaseLostError):
            async with database.sessions.begin() as db:
                await mark_outbox_sent(db, message.id, worker_id=owner, generation=generation)
    async with database.sessions.begin() as db:
        await renew_outbox_lease(db, message.id, worker_id="a", generation=message.generation)
        await mark_outbox_sent(db, message.id, worker_id="a", generation=message.generation)
    async with database.sessions() as db:
        operation = await db.get(Operation, operation_id)
        row = await db.get(OutboxMessage, message.id)
        assert operation.status == "pending"
        assert row.status == "sent" and row.sent_at is not None
        assert row.worker_id is row.lease_until is None
    with pytest.raises(LeaseLostError):
        async with database.sessions.begin() as db:
            await mark_outbox_sent(db, message.id, worker_id="a", generation=message.generation)


async def test_expiry_quarantines_and_never_auto_redelivers(harness):
    _, _, database = harness
    operation_id = await seed(database)
    async with database.sessions.begin() as db:
        operation = await transition_operation(
            db, operation_id, expected_status="pending", expected_generation=0, new_status="running"
        )
        assert operation.attempts == operation.generation == 1
        message = (await lease_outbox(db, worker_id="a", topics=["test.dispatch"]))[0]
    async with database.sessions.begin() as db:
        await db.execute(
            update(Operation)
            .where(Operation.id == operation_id)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
        await db.execute(
            update(OutboxMessage)
            .where(OutboxMessage.id == message.id)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
    with pytest.raises(LeaseLostError):
        async with database.sessions.begin() as db:
            await mark_outbox_sent(db, message.id, worker_id="a", generation=1)
    with pytest.raises(LeaseLostError):
        async with database.sessions.begin() as db:
            await transition_operation(
                db,
                operation_id,
                expected_status="running",
                expected_generation=1,
                new_status="succeeded",
            )
    async with database.sessions.begin() as db:
        assert await lease_outbox(db, worker_id="b", topics=["test.dispatch"]) == []
        assert await quarantine_expired_leases(db) == {"operations": 1, "outbox_messages": 1}
    async with database.sessions.begin() as db:
        assert await lease_outbox(db, worker_id="b", topics=["test.dispatch"]) == []
        assert (await db.get(Operation, operation_id)).status == "unknown"
        assert (await db.get(OutboxMessage, message.id)).status == "unknown"
    with pytest.raises(OperationStateError):
        async with database.sessions.begin() as db:
            await transition_operation(
                db,
                operation_id,
                expected_status="unknown",
                expected_generation=2,
                new_status="running",
            )


async def test_uncertain_ack_quarantines_and_is_not_replayed(harness):
    _, _, database = harness
    await seed(database)
    async with database.sessions.begin() as db:
        message = (await lease_outbox(db, worker_id="a", topics=["test.dispatch"]))[0]
    async with database.sessions.begin() as db:
        await mark_outbox_unknown(
            db, message.id, worker_id="a", generation=1, error_code="TRANSPORT_TIMEOUT"
        )
    async with database.sessions.begin() as db:
        assert await lease_outbox(db, worker_id="b", topics=["test.dispatch"]) == []
        row = await db.get(OutboxMessage, message.id)
        assert row.status == "unknown" and row.last_error_code == "TRANSPORT_TIMEOUT"


async def test_operation_generation_and_evidence_based_resolution(harness):
    _, _, database = harness
    operation_id = await seed(database)
    async with database.sessions.begin() as db:
        await transition_operation(
            db, operation_id, expected_status="pending", expected_generation=0, new_status="running"
        )
    with pytest.raises(OperationStateError):
        async with database.sessions.begin() as db:
            await transition_operation(
                db,
                operation_id,
                expected_status="running",
                expected_generation=0,
                new_status="succeeded",
            )
    async with database.sessions.begin() as db:
        await transition_operation(
            db, operation_id, expected_status="running", expected_generation=1, new_status="unknown"
        )
    evidence, admin = uuid4(), uuid4()
    async with database.sessions.begin() as db:
        await resolve_unknown_operation(
            db,
            operation_id,
            expected_generation=2,
            confirmed_success=True,
            evidence_id=evidence,
            admin_id=admin,
        )
    async with database.sessions() as db:
        operation = await db.get(Operation, operation_id)
        assert operation.status == "succeeded" and operation.result_body == {
            "evidence_id": str(evidence)
        }
        audit = await db.scalar(select(AuditLog).where(AuditLog.action == "operation.reconciled"))
        assert audit.actor_id == str(admin) and audit.target == str(operation_id)
    with pytest.raises(OperationStateError):
        async with database.sessions.begin() as db:
            await transition_operation(
                db,
                operation_id,
                expected_status="succeeded",
                expected_generation=3,
                new_status="running",
            )


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_kind": "dealer"},
        {"actor_kind": "user", "actor_id": None},
        {"idempotency_key_digest": "raw-code"},
        {"outbox_payload": {"bad": float("nan")}},
        {"outbox_payload": {"oversized": "x" * 65537}},
        {"outbox_topic": "../invalid"},
    ],
)
async def test_invalid_envelopes_rejected_before_write(harness, overrides):
    _, _, database = harness
    with pytest.raises(ValueError):
        async with database.sessions.begin() as db:
            await create_operation(db, **parameters(**overrides))
    async with database.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Operation)) == 0


async def test_database_constraints_reject_malformed_leases_and_foreign_key(harness):
    _, _, database = harness
    operation_id = await seed(database)
    with pytest.raises(IntegrityError):
        async with database.sessions.begin() as db:
            await db.execute(
                update(Operation).where(Operation.id == operation_id).values(status="running")
            )
    with pytest.raises(IntegrityError):
        async with database.sessions.begin() as db:
            await db.execute(
                update(OutboxMessage)
                .where(OutboxMessage.operation_id == operation_id)
                .values(status="sent")
            )
    with pytest.raises(IntegrityError):
        async with database.sessions.begin() as db:
            await db.execute(delete(Operation).where(Operation.id == operation_id))
