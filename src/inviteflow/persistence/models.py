"""PostgreSQL authentication foundation; business integrations remain Hooks."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "Base",
    "utcnow",
    "StaffAccount",
    "PublicSession",
    "StaffSession",
    "IdempotencyRequest",
    "Operation",
    "OutboxMessage",
    "AuditLog",
    "LoginRateLimit",
]


def utcnow() -> datetime:
    """Return an aware UTC timestamp (also supported on Python 3.10)."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_name)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class StaffAccount(Base):
    __tablename__ = "staff_accounts"
    __table_args__ = (
        CheckConstraint("role = 'admin'", name="role_admin"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_valid"),
        CheckConstraint("session_epoch >= 0", name="session_epoch_nonnegative"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username_normalized: Mapped[str] = mapped_column(String(128), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default="admin", server_default="admin")
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )


class _SessionColumns:
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    absolute_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PublicSession(_SessionColumns, Base):
    __tablename__ = "public_sessions"
    __table_args__ = (
        CheckConstraint("expires_at >= created_at", name="expiry_after_creation"),
        CheckConstraint("absolute_expires_at >= expires_at", name="absolute_expiry_bound"),
    )


class StaffSession(_SessionColumns, Base):
    __tablename__ = "staff_sessions"
    __table_args__ = (
        CheckConstraint("expires_at >= created_at", name="expiry_after_creation"),
        CheckConstraint("absolute_expires_at >= expires_at", name="absolute_expiry_bound"),
        CheckConstraint("session_epoch >= 0", name="session_epoch_nonnegative"),
    )

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_accounts.id", ondelete="RESTRICT"), index=True
    )
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class IdempotencyRequest(Base):
    __tablename__ = "idempotency_requests"
    __table_args__ = (
        UniqueConstraint("scope", "key_digest"),
        CheckConstraint("expires_at >= created_at", name="expiry_after_creation"),
        CheckConstraint("response_status BETWEEN 100 AND 599", name="response_status_valid"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scope: Mapped[str] = mapped_column(String(256))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Operation(Base):
    """A durable command envelope; it contains no raw credentials or codes."""

    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("idempotency_scope", "idempotency_key_digest"),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'cancelled')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint("actor_kind IN ('user', 'admin', 'internal')", name="actor_kind_valid"),
        CheckConstraint("actor_kind = 'internal' OR actor_id IS NOT NULL", name="actor_required"),
        CheckConstraint("(status = 'running') = (lease_until IS NOT NULL)", name="lease_shape"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(128))
    command: Mapped[str] = mapped_column(String(64))
    idempotency_scope: Mapped[str] = mapped_column(String(256))
    idempotency_key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    result_body: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class OutboxMessage(Base):
    """A transactionally-created message for a future internal worker."""

    __tablename__ = "outbox_messages"
    __table_args__ = (
        UniqueConstraint("operation_id", "topic", name="uq_outbox_messages_operation_topic"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'unknown')",
            name="status_valid",
        ),
        CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint("generation >= 0", name="generation_nonnegative"),
        CheckConstraint(
            "(status = 'processing' AND lease_until IS NOT NULL AND worker_id IS NOT NULL) "
            "OR (status <> 'processing' AND lease_until IS NULL AND worker_id IS NULL)",
            name="lease_shape",
        ),
        CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name="sent_timestamp"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    operation_id: Mapped[UUID] = mapped_column(
        ForeignKey("operations.id", ondelete="RESTRICT"), index=True
    )
    topic: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending", server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(128))
    last_error_code: Mapped[str | None] = mapped_column(String(64))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    actor_kind: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str | None] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    safe_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )


class LoginRateLimit(Base):
    __tablename__ = "login_rate_limits"
    __table_args__ = (CheckConstraint("attempts >= 0", name="attempts_nonnegative"),)

    scope_digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
