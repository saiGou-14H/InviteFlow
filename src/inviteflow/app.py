from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from asyncpg import PostgresError  # type: ignore[import-untyped]
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from inviteflow.api.errors import ApiError, api_error_handler
from inviteflow.api.middleware import RequestBoundaryMiddleware
from inviteflow.api.routes import api_router, hook_not_implemented_handler, public_router
from inviteflow.api.sessions import router as session_router
from inviteflow.config import Settings, get_settings
from inviteflow.domain.hooks import HookNotImplementedError, HookRegistry
from inviteflow.persistence.database import Database


async def validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Never echo invalid input: it can contain passwords, codes or credentials.
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求字段不符合要求。",
                "fields": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()],
            }
        },
    )


async def persistence_error_handler(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "code": "PERSISTENCE_UNAVAILABLE",
                "message": "数据库暂时不可用，请稍后重试。",
            }
        },
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    database = (
        Database(runtime_settings.database_url.get_secret_value())
        if runtime_settings.database_url
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if app.state.database is not None:
                await app.state.database.dispose()

    app = FastAPI(
        title=runtime_settings.app_name,
        version="0.1.0",
        description="InviteFlow two-role platform foundation; business integrations remain Hooks.",
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.database = database
    app.state.hooks = HookRegistry.placeholders()
    app.add_middleware(RequestBoundaryMiddleware, max_bytes=runtime_settings.max_request_bytes)
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(SQLAlchemyError, persistence_error_handler)
    app.add_exception_handler(PostgresError, persistence_error_handler)
    app.add_exception_handler(OSError, persistence_error_handler)
    app.add_exception_handler(TimeoutError, persistence_error_handler)
    app.add_exception_handler(HookNotImplementedError, hook_not_implemented_handler)
    app.include_router(public_router)
    app.include_router(session_router, prefix=runtime_settings.api_prefix)
    app.include_router(api_router, prefix=runtime_settings.api_prefix)
    return app
