"""Idempotency for database-only commands, not external calls or arbitrary Hooks.

Handlers MUST use the supplied transaction, never commit it or call a provider.
They must authorize the actor/object on every request, including before replay.
Responses must be safe projections: never store raw codes, cookies or credentials.
External actions belong to a future transactional Outbox/Worker integration.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from inviteflow.api.errors import ApiError
from inviteflow.persistence.database import Database
from inviteflow.persistence.models import IdempotencyRequest, utcnow
from inviteflow.security import digest


@dataclass(frozen=True)
class CommandResult:
    status: int
    body: dict[str, object]
    replayed: bool = False


Command = Callable[[AsyncSession], Awaitable[CommandResult]]


class IdempotencyExecutor:
    def __init__(self, database: Database, secret: str, ttl_seconds: int = 86400) -> None:
        if len(secret) < 32 or ttl_seconds < 60:
            raise ValueError("idempotency requires a strong secret and positive retention")
        self.database = database
        self.secret = secret
        self.ttl_seconds = ttl_seconds

    async def execute(
        self, *, scope: str, key: str, payload: dict[str, object], command: Command
    ) -> CommandResult:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", key):
            raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "需要 8–128 位有效幂等键。")
        if not 1 <= len(scope) <= 256:
            raise ValueError("scope must identify the actor and command, at most 256 characters")
        canonical = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        key_hash = digest(self.secret, "idempotency-key", key)
        request_hash = digest(self.secret, "idempotency-request", canonical)
        lock_hash = digest(self.secret, "idempotency-lock", scope + "\0" + key_hash)
        lock_id = int.from_bytes(bytes.fromhex(lock_hash[:16]), byteorder="big", signed=True)
        async with self.database.sessions.begin() as db:
            acquired = await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id}
            )
            if not acquired:
                raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "同一请求正在处理中，请稍后重试。")
            now = utcnow()
            row = await db.scalar(
                select(IdempotencyRequest).where(
                    IdempotencyRequest.scope == scope, IdempotencyRequest.key_digest == key_hash
                )
            )
            if row is not None and row.expires_at <= now:
                await db.delete(row)
                await db.flush()
                row = None
            if row is not None:
                if row.request_digest != request_hash:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同请求内容。")
                if row.response_status is None or row.response_body is None:
                    raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "请求状态待查证。")
                return CommandResult(row.response_status, row.response_body, replayed=True)
            row = IdempotencyRequest(
                scope=scope,
                key_digest=key_hash,
                request_digest=request_hash,
                created_at=now,
                expires_at=now + timedelta(seconds=self.ttl_seconds),
            )
            db.add(row)
            await db.flush()
            result = await command(db)
            if not db.in_transaction():
                raise RuntimeError("Command must not close the supplied transaction")
            if not 200 <= result.status < 300:
                raise ValueError("Only accepted/successful database commands may be cached")
            if len(json.dumps(result.body, allow_nan=False).encode()) > 65536:
                raise ValueError("Idempotency receipt exceeds 64 KiB")
            row.response_status = result.status
            row.response_body = result.body
            return result
