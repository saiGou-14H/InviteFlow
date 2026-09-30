"""Idempotency for database-only commands, never external calls or arbitrary Hooks.

Handlers MUST use the supplied transaction without committing it. Authorize the
actor/object before replay. Store only safe response projections, never secrets.
External actions belong to a transactional Outbox and a reviewed Worker adapter.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import Header, Request
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
_KEY = re.compile(r"[A-Za-z0-9._:-]{8,128}")


def validate_idempotency_key(key: str | None) -> str:
    if key is None or not _KEY.fullmatch(key):
        raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "需要 8–128 位有效幂等键。")
    return key


def require_idempotency_key(
    request: Request,
    key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            description="Required unique command key, 8–128 ASCII characters",
        ),
    ],
) -> str:
    if len(request.headers.getlist("idempotency-key")) != 1:
        raise ApiError(400, "INVALID_IDEMPOTENCY_KEY", "幂等键请求头必须且只能出现一次。")
    return validate_idempotency_key(key)


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
        key = validate_idempotency_key(key)
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
            # Even expired incomplete/invalid receipts require investigation.
            # Do not delete one and blindly execute a potentially applied command.
            if row is not None and (
                row.response_status is None or not isinstance(row.response_body, dict)
            ):
                raise ApiError(409, "IDEMPOTENCY_IN_PROGRESS", "请求状态待查证。")
            if row is not None and row.expires_at <= now:
                await db.delete(row)
                await db.flush()
                row = None
            if row is not None:
                if row.request_digest != request_hash:
                    raise ApiError(409, "IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同请求内容。")
                assert row.response_status is not None and row.response_body is not None
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
            if not isinstance(result.body, dict):
                raise ValueError("Idempotency receipt must be a JSON object")
            encoded = json.dumps(result.body, allow_nan=False)
            if len(encoded.encode()) > 65536:
                raise ValueError("Idempotency receipt exceeds 64 KiB")
            row.response_status = result.status
            row.response_body = json.loads(encoded)
            return result
