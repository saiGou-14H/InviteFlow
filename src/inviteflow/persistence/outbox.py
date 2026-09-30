"""Database-only operation/outbox primitives; no provider or broker is called.

Caller owns the transaction. Commit the lease BEFORE transport work, and ACK in
a new transaction. Expired/uncertain attempts are quarantined, never redelivered
automatically. Payloads/results must be safe projections or durable references.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inviteflow.persistence.models import AuditLog, Operation, OutboxMessage
from inviteflow.safe_json import bounded_json_object

_DIGEST = re.compile(r"[0-9a-f]{64}")
_TOPIC = re.compile(r"[a-z][a-z0-9_.:-]{0,127}")
_WORKER = re.compile(r"[A-Za-z0-9._:-]{1,128}")
_CODE = re.compile(r"[A-Z0-9_.:-]{1,64}")
_TRANSITIONS = {
    "pending": {"running", "cancelled"},
    "running": {"succeeded", "failed", "unknown"},
    "failed": {"running", "cancelled"},
    "unknown": set(),
    "succeeded": set(),
    "cancelled": set(),
}


class LeaseLostError(RuntimeError):
    pass


class OperationStateError(ValueError):
    pass


class OperationConflictError(ValueError):
    pass


class OperationBusyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LeasedMessage:
    id: UUID
    operation_id: UUID
    topic: str
    payload: dict[str, object]
    generation: int
    attempts: int


def _transaction(db: AsyncSession) -> None:
    if not db.in_transaction():
        raise ValueError("An explicit caller-owned transaction is required")


def _bounded_json(value: dict[str, object]) -> dict[str, object]:
    return bounded_json_object(value)


def _code(value: str) -> str:
    if not _CODE.fullmatch(value):
        raise ValueError("Use a safe uppercase error code of 1–64 characters")
    return value


def _limits(limit: int, lease_seconds: int = 60) -> None:
    if not 1 <= limit <= 100 or not 1 <= lease_seconds <= 3600:
        raise ValueError("limit must be 1–100; lease_seconds must be 1–3600")


async def _now(db: AsyncSession) -> datetime:
    # Read database wall-clock AFTER acquiring locks; transaction-start time can
    # predate a lock wait and must not revive an already expired lease.
    value = await db.scalar(select(func.clock_timestamp()))
    assert isinstance(value, datetime)
    return value


async def create_operation(
    db: AsyncSession,
    *,
    actor_kind: str,
    actor_id: str | None,
    command: str,
    idempotency_scope: str,
    idempotency_key_digest: str,
    request_digest: str,
    outbox_topic: str | None = None,
    outbox_payload: dict[str, object] | None = None,
) -> Operation:
    """Atomically create or reuse a durable operation and its initial notification.

    Scope/digests must be server-derived and include the actor and command.
    Include the full command/envelope in request_digest. Replays must be authorized
    before this call; an old operation is not re-enqueued after receipt cleanup.
    """
    _transaction(db)
    if actor_kind not in {"user", "admin", "internal"}:
        raise ValueError("Unsupported actor kind")
    if actor_kind != "internal":
        if actor_id is None:
            raise ValueError("User/admin actor requires a UUID")
        actor_id = str(UUID(actor_id))
    if actor_id is not None and not 1 <= len(actor_id) <= 128:
        raise ValueError("Actor reference must be 1–128 characters")
    if not _TOPIC.fullmatch(command) or len(command) > 64:
        raise ValueError("Invalid command name")
    if not 1 <= len(idempotency_scope) <= 256:
        raise ValueError("Invalid idempotency scope")
    if not _DIGEST.fullmatch(idempotency_key_digest) or not _DIGEST.fullmatch(request_digest):
        raise ValueError("Digests must be lowercase SHA-256 hex")
    payload = None
    if outbox_topic is not None:
        if not _TOPIC.fullmatch(outbox_topic) or outbox_payload is None:
            raise ValueError("A valid outbox topic and payload are required together")
        payload = _bounded_json(outbox_payload)
    elif outbox_payload is not None:
        raise ValueError("Payload requires a topic")
    lock_hash = hashlib.sha256(
        ("operation\0" + idempotency_scope + "\0" + idempotency_key_digest).encode()
    ).digest()
    lock_id = int.from_bytes(lock_hash[:8], byteorder="big", signed=True)
    if not await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id}):
        raise OperationBusyError("Operation is being created; retry the same key later")
    existing = await db.scalar(
        select(Operation).where(
            Operation.idempotency_scope == idempotency_scope,
            Operation.idempotency_key_digest == idempotency_key_digest,
        )
    )
    if existing is not None:
        if (existing.request_digest, existing.command, existing.actor_kind, existing.actor_id) != (
            request_digest,
            command,
            actor_kind,
            actor_id,
        ):
            raise OperationConflictError("Idempotency identity reused with different content")
        return existing
    operation = Operation(
        actor_kind=actor_kind,
        actor_id=actor_id,
        command=command,
        idempotency_scope=idempotency_scope,
        idempotency_key_digest=idempotency_key_digest,
        request_digest=request_digest,
    )
    db.add(operation)
    await db.flush()
    if outbox_topic is not None:
        db.add(OutboxMessage(operation_id=operation.id, topic=outbox_topic, payload=payload))
        await db.flush()
    return operation


async def transition_operation(
    db: AsyncSession,
    operation_id: UUID,
    *,
    expected_status: str,
    expected_generation: int,
    new_status: str,
    result_body: dict[str, object] | None = None,
    error_code: str | None = None,
    lease_seconds: int = 60,
) -> Operation:
    """Fence state changes. failed means confirmed no effect, not an exception."""
    _transaction(db)
    _limits(1, lease_seconds)
    if new_status not in _TRANSITIONS.get(expected_status, set()):
        raise OperationStateError(
            "Transition forbidden; unknown requires evidence-based resolution"
        )
    result = _bounded_json(result_body) if result_body is not None else None
    code = _code(error_code) if error_code is not None else None
    operation = await db.scalar(
        select(Operation)
        .where(Operation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await _now(db)
    if (
        operation is None
        or operation.status != expected_status
        or operation.generation != expected_generation
    ):
        raise OperationStateError("Operation changed or no longer exists")
    if expected_status == "running" and (
        operation.lease_until is None or operation.lease_until <= now
    ):
        raise LeaseLostError("Operation lease expired; reconcile instead of finalizing")
    operation.status = new_status
    operation.generation += 1
    operation.lease_until = (
        now + timedelta(seconds=lease_seconds) if new_status == "running" else None
    )
    if new_status == "running":
        operation.attempts += 1
    operation.result_body = result
    operation.error_code = code
    operation.updated_at = now
    await db.flush()
    return operation


async def lease_outbox(
    db: AsyncSession, *, worker_id: str, topics: list[str], limit: int = 20, lease_seconds: int = 60
) -> list[LeasedMessage]:
    """Lease only pending messages for explicit topics; NEVER reclaim processing."""
    _transaction(db)
    _limits(limit, lease_seconds)
    if not _WORKER.fullmatch(worker_id) or not topics or len(topics) > 100:
        raise ValueError("Valid worker and nonempty bounded topic list required")
    if any(not _TOPIC.fullmatch(topic) for topic in topics):
        raise ValueError("Invalid topic")
    rows = (
        await db.scalars(
            select(OutboxMessage)
            .where(
                OutboxMessage.status == "pending",
                OutboxMessage.topic.in_(topics),
                OutboxMessage.available_at <= func.clock_timestamp(),
            )
            .order_by(OutboxMessage.available_at, OutboxMessage.created_at, OutboxMessage.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).all()
    now = await _now(db)
    leased = []
    for row in rows:
        row.status = "processing"
        row.worker_id = worker_id
        row.lease_until = now + timedelta(seconds=lease_seconds)
        row.attempts += 1
        row.generation += 1
        row.updated_at = now
        leased.append(
            LeasedMessage(
                row.id,
                row.operation_id,
                row.topic,
                _bounded_json(row.payload),
                row.generation,
                row.attempts,
            )
        )
    await db.flush()
    return leased


async def _owned_message(
    db: AsyncSession, message_id: UUID, worker_id: str, generation: int
) -> OutboxMessage:
    _transaction(db)
    message = await db.scalar(
        select(OutboxMessage)
        .where(OutboxMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    now = await _now(db)
    if (
        message is None
        or message.status != "processing"
        or message.worker_id != worker_id
        or message.generation != generation
        or message.lease_until is None
        or message.lease_until <= now
    ):
        raise LeaseLostError("Outbox lease or fencing generation no longer valid")
    return message


async def renew_outbox_lease(
    db: AsyncSession, message_id: UUID, *, worker_id: str, generation: int, lease_seconds: int = 60
) -> None:
    _limits(1, lease_seconds)
    row = await _owned_message(db, message_id, worker_id, generation)
    now = await _now(db)
    row.lease_until = max(row.lease_until or now, now + timedelta(seconds=lease_seconds))
    row.updated_at = now
    await db.flush()


async def mark_outbox_sent(
    db: AsyncSession, message_id: UUID, *, worker_id: str, generation: int
) -> None:
    """Transport acceptance only; DOES NOT mark an operation/business successful."""
    row = await _owned_message(db, message_id, worker_id, generation)
    now = await _now(db)
    row.status, row.sent_at, row.updated_at = "sent", now, now
    row.lease_until, row.worker_id = None, None
    row.generation += 1
    await db.flush()


async def mark_outbox_unknown(
    db: AsyncSession, message_id: UUID, *, worker_id: str, generation: int, error_code: str
) -> None:
    _code(error_code)
    row = await _owned_message(db, message_id, worker_id, generation)
    row.status, row.last_error_code, row.updated_at = "unknown", error_code, await _now(db)
    row.lease_until, row.worker_id = None, None
    row.generation += 1
    await db.flush()


async def quarantine_expired_leases(db: AsyncSession, *, limit: int = 100) -> dict[str, int]:
    """Bounded recovery quarantines uncertain attempts, without resending anything."""
    _transaction(db)
    _limits(limit)
    counts = {}
    targets: list[tuple[type[Operation] | type[OutboxMessage], str, str]] = [
        (Operation, "running", "operations"),
        (OutboxMessage, "processing", "outbox_messages"),
    ]
    for model, status, label in targets:
        rows = (
            await db.scalars(
                select(model)
                .where(
                    model.status == status,
                    model.lease_until <= func.clock_timestamp(),
                )
                .order_by(model.lease_until, model.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        ).all()
        now = await _now(db)
        for row in rows:
            assert isinstance(row, (Operation, OutboxMessage))
            row.status, row.lease_until, row.updated_at = "unknown", None, now
            row.generation += 1
            if isinstance(row, OutboxMessage):
                row.worker_id = None
                row.last_error_code = "LEASE_EXPIRED"
            else:
                row.error_code = "LEASE_EXPIRED"
        counts[label] = len(rows)
    await db.flush()
    return counts


async def resolve_unknown_operation(
    db: AsyncSession,
    operation_id: UUID,
    *,
    expected_generation: int,
    confirmed_success: bool,
    evidence_id: UUID,
    admin_id: UUID,
) -> None:
    """Trusted reconciliation boundary; caller must verify admin and durable evidence.

    This function cannot verify external evidence authenticity. No HTTP endpoint
    or provider implementation exposes it in this foundation. No requeue occurs.
    """
    _transaction(db)
    row = await db.scalar(
        select(Operation)
        .where(Operation.id == operation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "unknown" or row.generation != expected_generation:
        raise OperationStateError("Unknown operation changed or missing")
    row.status = "succeeded" if confirmed_success else "failed"
    row.generation += 1
    row.result_body = {"evidence_id": str(evidence_id)}
    row.error_code = None if confirmed_success else "CONFIRMED_NO_EFFECT"
    row.updated_at = await _now(db)
    db.add(
        AuditLog(
            actor_kind="admin",
            actor_id=str(admin_id),
            action="operation.reconciled",
            target=str(operation_id),
            safe_metadata={"evidence_id": str(evidence_id), "status": row.status},
        )
    )
    await db.flush()
