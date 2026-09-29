from fastapi import FastAPI

from inviteflow.api.routes import api_router, hook_not_implemented_handler, public_router
from inviteflow.config import Settings, get_settings
from inviteflow.domain.hooks import HookNotImplementedError, HookRegistry


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime_settings = settings or get_settings()
    app = FastAPI(
        title=runtime_settings.app_name,
        version="0.1.0",
        description="InviteFlow invitation redemption and task orchestration API skeleton.",
    )
    app.state.settings = runtime_settings
    app.state.hooks = HookRegistry.placeholders()
    app.add_exception_handler(HookNotImplementedError, hook_not_implemented_handler)
    app.include_router(public_router)
    app.include_router(api_router, prefix=runtime_settings.api_prefix)
    return app
