import asyncio
import hmac
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from inviteflow.api.errors import ApiError
from inviteflow.config import Settings
from inviteflow.domain.roles import Role
from inviteflow.persistence.database import Database
from inviteflow.persistence.models import (
    AuditLog,
    LoginRateLimit,
    PublicSession,
    StaffAccount,
    StaffSession,
    utcnow,
)
from inviteflow.security import (
    DUMMY_PASSWORD_HASH,
    digest,
    new_token,
    normalize_username,
    verify_password,
)


@dataclass(frozen=True)
class Actor:
    id: UUID
    session_id: UUID
    role: Role
    csrf_hash: str


def settings_for(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def database_for(request: Request) -> Database:
    database: Database | None = request.app.state.database
    if database is None:
        raise ApiError(503, "PERSISTENCE_NOT_CONFIGURED", "尚未配置数据库，无法建立安全会话。")
    return database


def secret_for(settings: Settings) -> str:
    if settings.session_secret is None:
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "尚未配置会话密钥。")
    return settings.session_secret.get_secret_value()


def require_origin(request: Request) -> None:
    settings = settings_for(request)
    if request.headers.get("origin") != settings.public_origin:
        raise ApiError(403, "ORIGIN_REJECTED", "请求来源不被允许。")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise ApiError(403, "ORIGIN_REJECTED", "不接受跨站请求。")


def cookie_name(settings: Settings, role: Role) -> str:
    return settings.user_cookie_name if role == Role.USER else settings.admin_cookie_name


def csrf_token(settings: Settings, token: str) -> str:
    return digest(secret_for(settings), "csrf-token", token)


def set_session_cookie(response: Response, settings: Settings, role: Role, token: str) -> None:
    ttl = (
        settings.user_session_max_seconds
        if role == Role.USER
        else settings.admin_session_max_seconds
    )
    response.set_cookie(
        cookie_name(settings, role),
        token,
        max_age=ttl,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"


def session_payload(settings: Settings, token: str, actor: Actor) -> dict[str, object]:
    return {
        "role": actor.role.value,
        "actor_id": str(actor.id),
        "csrf_token": csrf_token(settings, token),
    }


async def authenticate(request: Request, role: Role, *, csrf: bool = True) -> Actor:
    settings = settings_for(request)
    database = database_for(request)
    token = request.cookies.get(cookie_name(settings, role))
    if not token or len(token) > 256:
        if role == Role.ADMIN and request.cookies.get(settings.user_cookie_name):
            raise ApiError(403, "ADMIN_REQUIRED", "此操作仅管理员可用。")
        raise ApiError(401, "SESSION_REQUIRED", "请先建立有效会话。")
    secret = secret_for(settings)
    now = utcnow()
    model = PublicSession if role == Role.USER else StaffSession
    async with database.sessions.begin() as db:
        session = await db.scalar(
            select(model).where(
                model.token_hash == digest(secret, "session:" + role.value, token),
                model.revoked_at.is_(None),
                model.expires_at > now,
                model.absolute_expires_at > now,
            )
        )
        if session is None:
            raise ApiError(401, "SESSION_EXPIRED", "会话已失效，请重新建立会话。")
        assert isinstance(session, (PublicSession, StaffSession))
        actor_id = session.id
        if isinstance(session, StaffSession):
            account = await db.get(StaffAccount, session.account_id)
            if (
                account is None
                or account.role != "admin"
                or account.status != "active"
                or account.session_epoch != session.session_epoch
            ):
                raise ApiError(401, "SESSION_REVOKED", "管理员会话已撤销。")
            actor_id = account.id
        if csrf and request.method not in {"GET", "HEAD", "OPTIONS"}:
            require_origin(request)
            supplied = request.headers.get("x-csrf-token", "")
            if len(supplied) != 64 or not hmac.compare_digest(
                digest(secret, "csrf-hash", supplied), session.csrf_hash
            ):
                raise ApiError(403, "CSRF_REJECTED", "请求校验失败，请刷新页面后重试。")
        ttl = (
            settings.user_session_ttl_seconds
            if role == Role.USER
            else settings.admin_session_ttl_seconds
        )
        session.expires_at = min(now + timedelta(seconds=ttl), session.absolute_expires_at)
        actor = Actor(id=actor_id, session_id=session.id, role=role, csrf_hash=session.csrf_hash)
    return actor


async def require_user(request: Request) -> Actor:
    return await authenticate(request, Role.USER)


async def require_admin(request: Request) -> Actor:
    return await authenticate(request, Role.ADMIN)


async def create_user_session(request: Request, response: Response) -> dict[str, object]:
    require_origin(request)
    settings = settings_for(request)
    database = database_for(request)
    old_token = request.cookies.get(settings.user_cookie_name)
    if old_token:
        try:
            actor = await authenticate(request, Role.USER, csrf=False)
            return session_payload(settings, old_token, actor)
        except ApiError as exc:
            if exc.status != 401:
                raise
    token = new_token()
    secret = secret_for(settings)
    now = utcnow()
    session = PublicSession(
        token_hash=digest(secret, "session:user", token),
        csrf_hash=digest(secret, "csrf-hash", csrf_token(settings, token)),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.user_session_ttl_seconds),
        absolute_expires_at=now + timedelta(seconds=settings.user_session_max_seconds),
    )
    async with database.sessions.begin() as db:
        db.add(session)
        await db.flush()
        actor = Actor(session.id, session.id, Role.USER, session.csrf_hash)
    set_session_cookie(response, settings, Role.USER, token)
    return session_payload(settings, token, actor)


async def enforce_login_limit(request: Request, username: str) -> None:
    settings = settings_for(request)
    database = database_for(request)
    secret = secret_for(settings)
    # Never trust X-Forwarded-For here. Trusted proxy processing is an edge concern.
    peer = request.client.host if request.client else "unknown"
    scopes = sorted([digest(secret, "login-account", username), digest(secret, "login-peer", peer)])
    now = utcnow()
    exceeded = False
    async with database.sessions.begin() as db:
        for scope in scopes:
            # A no-op conflict update locks the existing window atomically with
            # lookup. Maintenance must not delete it between INSERT and SELECT.
            row = await db.scalar(
                insert(LoginRateLimit)
                .values(scope_digest=scope, window_started_at=now, attempts=0)
                .on_conflict_do_update(
                    index_elements=[LoginRateLimit.scope_digest],
                    set_={"scope_digest": LoginRateLimit.scope_digest},
                )
                .returning(LoginRateLimit)
                .execution_options(populate_existing=True)
            )
            assert row is not None
            if now >= row.window_started_at + timedelta(seconds=settings.login_window_seconds):
                row.window_started_at = now
                row.attempts = 0
            if row.attempts >= settings.login_attempt_limit:
                exceeded = True
            else:
                row.attempts += 1
    if exceeded:
        raise ApiError(429, "LOGIN_RATE_LIMITED", "登录尝试过于频繁，请稍后重试。")


async def login_admin(
    request: Request, response: Response, username: str, password: str
) -> dict[str, object]:
    require_origin(request)
    settings = settings_for(request)
    database = database_for(request)
    try:
        username = normalize_username(username)
    except ValueError:
        raise ApiError(401, "INVALID_CREDENTIALS", "账号或密码错误。") from None
    await enforce_login_limit(request, username)
    async with database.sessions() as db:
        account = await db.scalar(
            select(StaffAccount).where(StaffAccount.username_normalized == username)
        )
        encoded = account.password_hash if account else DUMMY_PASSWORD_HASH
    valid = await asyncio.to_thread(verify_password, encoded, password)
    secret = secret_for(settings)
    target = digest(secret, "login-account", username)
    if not valid or account is None or account.status != "active" or account.role != "admin":
        async with database.sessions.begin() as db:
            db.add(AuditLog(actor_kind="anonymous", action="auth.login_failed", target=target))
        raise ApiError(401, "INVALID_CREDENTIALS", "账号或密码错误。")
    now = utcnow()
    token = new_token()
    async with database.sessions.begin() as db:
        # Recheck account under lock after password verification, including password resets.
        current = await db.scalar(
            select(StaffAccount).where(StaffAccount.id == account.id).with_for_update()
        )
        if (
            current is None
            or current.status != "active"
            or current.role != "admin"
            or current.session_epoch != account.session_epoch
            or current.password_hash != encoded
        ):
            raise ApiError(401, "INVALID_CREDENTIALS", "账号或密码错误。")
        old_token = request.cookies.get(settings.admin_cookie_name)
        if old_token:
            previous = await db.scalar(
                select(StaffSession).where(
                    StaffSession.token_hash == digest(secret, "session:admin", old_token)
                )
            )
            if previous:
                previous.revoked_at = now
        session = StaffSession(
            account_id=current.id,
            session_epoch=current.session_epoch,
            token_hash=digest(secret, "session:admin", token),
            csrf_hash=digest(secret, "csrf-hash", csrf_token(settings, token)),
            created_at=now,
            expires_at=now + timedelta(seconds=settings.admin_session_ttl_seconds),
            absolute_expires_at=now + timedelta(seconds=settings.admin_session_max_seconds),
        )
        db.add(session)
        await db.flush()
        db.add(AuditLog(actor_kind="admin", actor_id=str(current.id), action="auth.login"))
        actor = Actor(current.id, session.id, Role.ADMIN, session.csrf_hash)
    set_session_cookie(response, settings, Role.ADMIN, token)
    return session_payload(settings, token, actor)


async def logout(request: Request, response: Response, actor: Actor) -> dict[str, str]:
    model = PublicSession if actor.role == Role.USER else StaffSession
    async with database_for(request).sessions.begin() as db:
        session = await db.get(model, actor.session_id)
        if isinstance(session, (PublicSession, StaffSession)):
            session.revoked_at = utcnow()
        db.add(AuditLog(actor_kind=actor.role.value, actor_id=str(actor.id), action="auth.logout"))
    settings = settings_for(request)
    response.delete_cookie(
        cookie_name(settings, actor.role),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return {"status": "ok"}
