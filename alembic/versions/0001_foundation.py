"""Create the authentication and request-safety foundation.

Revision ID: 0001_foundation
Revises: None

This migration is deliberately self-contained: never import application models.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_accounts",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("username_normalized", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), server_default="admin", nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("session_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("role = 'admin'", name=op.f("ck_staff_accounts_role_admin")),
        sa.CheckConstraint(
            "status IN ('active', 'disabled')", name=op.f("ck_staff_accounts_status_valid")
        ),
        sa.CheckConstraint(
            "session_epoch >= 0", name=op.f("ck_staff_accounts_session_epoch_nonnegative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_accounts")),
        sa.UniqueConstraint(
            "username_normalized", name=op.f("uq_staff_accounts_username_normalized")
        ),
    )
    op.create_table(
        "public_sessions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at >= created_at", name=op.f("ck_public_sessions_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "absolute_expires_at >= expires_at",
            name=op.f("ck_public_sessions_absolute_expiry_bound"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_public_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_public_sessions_token_hash")),
    )
    op.create_index(op.f("ix_public_sessions_expires_at"), "public_sessions", ["expires_at"])
    op.create_table(
        "staff_sessions",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("session_epoch", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at >= created_at", name=op.f("ck_staff_sessions_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "absolute_expires_at >= expires_at",
            name=op.f("ck_staff_sessions_absolute_expiry_bound"),
        ),
        sa.CheckConstraint(
            "session_epoch >= 0", name=op.f("ck_staff_sessions_session_epoch_nonnegative")
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["staff_accounts.id"],
            name=op.f("fk_staff_sessions_account_id_staff_accounts"), ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_staff_sessions_token_hash")),
    )
    op.create_index(op.f("ix_staff_sessions_account_id"), "staff_sessions", ["account_id"])
    op.create_index(op.f("ix_staff_sessions_expires_at"), "staff_sessions", ["expires_at"])
    op.create_table(
        "idempotency_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("scope", sa.String(256), nullable=False),
        sa.Column("key_digest", sa.String(64), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expires_at >= created_at", name=op.f("ck_idempotency_requests_expiry_after_creation")
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name=op.f("ck_idempotency_requests_response_status_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_requests")),
        sa.UniqueConstraint("scope", "key_digest", name=op.f("uq_idempotency_requests_scope")),
    )
    op.create_index(
        op.f("ix_idempotency_requests_expires_at"), "idempotency_requests", ["expires_at"]
    )
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("actor_kind", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "safe_metadata", postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"])
    op.create_table(
        "login_rate_limits",
        sa.Column("scope_digest", sa.String(64), nullable=False),
        sa.Column(
            "window_started_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_login_rate_limits_attempts_nonnegative")),
        sa.PrimaryKeyConstraint("scope_digest", name=op.f("pk_login_rate_limits")),
    )
    op.create_index(
        op.f("ix_login_rate_limits_window_started_at"), "login_rate_limits", ["window_started_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_login_rate_limits_window_started_at"), table_name="login_rate_limits")
    op.drop_table("login_rate_limits")
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index(op.f("ix_idempotency_requests_expires_at"), table_name="idempotency_requests")
    op.drop_table("idempotency_requests")
    op.drop_index(op.f("ix_staff_sessions_expires_at"), table_name="staff_sessions")
    op.drop_index(op.f("ix_staff_sessions_account_id"), table_name="staff_sessions")
    op.drop_table("staff_sessions")
    op.drop_index(op.f("ix_public_sessions_expires_at"), table_name="public_sessions")
    op.drop_table("public_sessions")
    op.drop_table("staff_accounts")
