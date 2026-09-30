"""Bounded maintenance for records that are safe to remove after retention."""

from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar
from uuid import UUID

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from inviteflow.persistence.models import (
    Base,
    IdempotencyRequest,
    LoginRateLimit,
    OutboxMessage,
    PublicSession,
    StaffSession,
    utcnow,
)

_Key = TypeVar("_Key", UUID, str)


@dataclass(frozen=True, slots=True)
class PurgeCounts:
    public_sessions: int
    staff_sessions: int
    idempotency_requests: int
    login_rate_limits: int
    sent_outbox_messages: int

    def as_dict(self) -> dict[str, int]:
        return {
            "public_sessions": self.public_sessions,
            "staff_sessions": self.staff_sessions,
            "idempotency_requests": self.idempotency_requests,
            "login_rate_limits": self.login_rate_limits,
            "sent_outbox_messages": self.sent_outbox_messages,
        }


async def _purge_batch(
    db: AsyncSession,
    model: type[Base],
    key: InstrumentedAttribute[_Key],
    eligible: ColumnElement[bool],
    *,
    limit: int,
    dry_run: bool,
) -> int:
    candidates = select(key).where(eligible).order_by(key).limit(limit)
    if not dry_run:
        candidates = candidates.with_for_update(skip_locked=True)
    keys = (await db.scalars(candidates)).all()
    if dry_run or not keys:
        return len(keys)
    # The selected rows remain locked until the caller closes this transaction.
    deleted = await db.scalars(
        delete(model)
        .where(key.in_(keys))
        .returning(key)
        .execution_options(synchronize_session=False)
    )
    return len(deleted.all())


async def purge_expired(
    db: AsyncSession,
    *,
    login_window_seconds: int,
    session_retention_seconds: int = 86400,
    sent_outbox_retention_seconds: int = 604800,
    limit: int = 100,
    dry_run: bool = True,
) -> PurgeCounts:
    """Count or delete at most ``limit`` eligible rows per table.

    Use a dedicated session/transaction with no application writes. The caller
    owns commit/rollback. Dry-run executes only SELECTs, without row locks or
    autoflush; apply selects primary keys FOR UPDATE SKIP LOCKED before deleting.
    Ordinary MVCC visibility and row locks exclude other uncommitted receipts.
    """
    for name, value, lower, upper in (
        ("limit", limit, 1, 1000),
        ("session_retention_seconds", session_retention_seconds, 60, 31536000),
        ("sent_outbox_retention_seconds", sent_outbox_retention_seconds, 60, 31536000),
        ("login_window_seconds", login_window_seconds, 1, 86400),
    ):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError(f"{name} must be {lower}..{upper}")
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    now = utcnow()
    session_cutoff = now - timedelta(seconds=session_retention_seconds)
    outbox_cutoff = now - timedelta(seconds=sent_outbox_retention_seconds)
    rate_cutoff = now - timedelta(seconds=max(login_window_seconds, 3600))
    with db.no_autoflush:
        public_count = await _purge_batch(
            db,
            PublicSession,
            PublicSession.id,
            or_(
                PublicSession.expires_at <= session_cutoff,
                PublicSession.revoked_at <= session_cutoff,
            ),
            limit=limit,
            dry_run=dry_run,
        )
        staff_count = await _purge_batch(
            db,
            StaffSession,
            StaffSession.id,
            or_(
                StaffSession.expires_at <= session_cutoff, StaffSession.revoked_at <= session_cutoff
            ),
            limit=limit,
            dry_run=dry_run,
        )
        receipt_count = await _purge_batch(
            db,
            IdempotencyRequest,
            IdempotencyRequest.id,
            (IdempotencyRequest.expires_at <= now)
            & IdempotencyRequest.response_status.is_not(None)
            # JSONB None can be stored as JSON null, not SQL NULL. Both are incomplete.
            & (func.jsonb_typeof(IdempotencyRequest.response_body) == "object"),
            limit=limit,
            dry_run=dry_run,
        )
        rate_count = await _purge_batch(
            db,
            LoginRateLimit,
            LoginRateLimit.scope_digest,
            LoginRateLimit.window_started_at <= rate_cutoff,
            limit=limit,
            dry_run=dry_run,
        )
        outbox_count = await _purge_batch(
            db,
            OutboxMessage,
            OutboxMessage.id,
            (OutboxMessage.status == "sent") & (OutboxMessage.sent_at <= outbox_cutoff),
            limit=limit,
            dry_run=dry_run,
        )
    return PurgeCounts(public_count, staff_count, receipt_count, rate_count, outbox_count)
