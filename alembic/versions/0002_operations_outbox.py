"""Add durable operation envelopes and transactional outbox messages.

Revision ID: 0002_operations_outbox
Revises: 0001_foundation

This migration stores only safe digests and worker metadata. It does not execute
external providers and cannot claim exactly-once delivery outside PostgreSQL.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_operations_outbox"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("command", sa.String(64), nullable=False),
        sa.Column("idempotency_scope", sa.String(256), nullable=False),
        sa.Column("idempotency_key_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("result_body", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'unknown', 'cancelled')",
            name=op.f("ck_operations_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_operations_attempts_nonnegative")),
        sa.CheckConstraint("generation >= 0", name=op.f("ck_operations_generation_nonnegative")),
        sa.CheckConstraint(
            "actor_kind IN ('user', 'admin', 'internal')",
            name=op.f("ck_operations_actor_kind_valid"),
        ),
        sa.CheckConstraint(
            "actor_kind = 'internal' OR actor_id IS NOT NULL",
            name=op.f("ck_operations_actor_required"),
        ),
        sa.CheckConstraint(
            "(status = 'running') = (lease_until IS NOT NULL)",
            name=op.f("ck_operations_lease_shape"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_operations")),
        sa.UniqueConstraint(
            "idempotency_scope",
            "idempotency_key_digest",
            name=op.f("uq_operations_idempotency_scope"),
        ),
    )
    op.create_index(op.f("ix_operations_available_at"), "operations", ["available_at"])
    op.create_index(op.f("ix_operations_created_at"), "operations", ["created_at"])
    op.create_table(
        "outbox_messages",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("operation_id", sa.UUID(), nullable=False),
        sa.Column("topic", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_id", sa.String(128), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'failed', 'unknown')",
            name=op.f("ck_outbox_messages_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_outbox_messages_attempts_nonnegative")),
        sa.CheckConstraint(
            "generation >= 0", name=op.f("ck_outbox_messages_generation_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["operations.id"],
            ondelete="RESTRICT",
            name=op.f("fk_outbox_messages_operation_id_operations"),
        ),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_until IS NOT NULL AND worker_id IS NOT NULL) "
            "OR (status <> 'processing' AND lease_until IS NULL AND worker_id IS NULL)",
            name=op.f("ck_outbox_messages_lease_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'sent') = (sent_at IS NOT NULL)",
            name=op.f("ck_outbox_messages_sent_timestamp"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_messages")),
        sa.UniqueConstraint(
            "operation_id", "topic", name=op.f("uq_outbox_messages_operation_topic")
        ),
    )
    op.create_index(op.f("ix_outbox_messages_operation_id"), "outbox_messages", ["operation_id"])
    op.create_index(op.f("ix_outbox_messages_available_at"), "outbox_messages", ["available_at"])
    op.create_index(op.f("ix_outbox_messages_created_at"), "outbox_messages", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_outbox_messages_created_at"), table_name="outbox_messages")
    op.drop_index(op.f("ix_outbox_messages_available_at"), table_name="outbox_messages")
    op.drop_index(op.f("ix_outbox_messages_operation_id"), table_name="outbox_messages")
    op.drop_table("outbox_messages")
    op.drop_index(op.f("ix_operations_created_at"), table_name="operations")
    op.drop_index(op.f("ix_operations_available_at"), table_name="operations")
    op.drop_table("operations")
