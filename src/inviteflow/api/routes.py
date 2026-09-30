import asyncio
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from inviteflow.api.schemas import (
    CdkBatchRequest,
    ClaimActionRequest,
    ClaimBatchRequest,
    ReconcileRequest,
)
from inviteflow.auth import Actor, require_admin, require_user
from inviteflow.domain.hooks import HookNotImplementedError, HookRegistry
from inviteflow.domain.roles import Role

public_router = APIRouter(tags=["system"])
api_router = APIRouter()


def get_hooks(request: Request) -> HookRegistry:
    if not request.app.state.settings.business_hooks_enabled:
        # Fail closed even if another component installed concrete hooks.
        return HookRegistry.placeholders()
    return cast(HookRegistry, request.app.state.hooks)


Hooks = Annotated[HookRegistry, Depends(get_hooks)]
UserActor = Annotated[Actor, Depends(require_user)]
AdminActor = Annotated[Actor, Depends(require_admin)]


@public_router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "inviteflow"}


@public_router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    database = request.app.state.database
    ready = False
    if database is not None:
        try:
            ready = await asyncio.wait_for(
                database.check_ready(), request.app.state.settings.readiness_timeout_seconds
            )
        except (TimeoutError, asyncio.TimeoutError):
            ready = False
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "not_ready",
            "persistence": "ready" if ready else ("unavailable" if database else "not_configured"),
            "business_hooks_enabled": request.app.state.settings.business_hooks_enabled,
        },
    )


@api_router.get("/capabilities", tags=["system"])
async def capabilities(request: Request) -> dict[str, object]:
    return {
        "service": "inviteflow",
        "version": "0.1.0",
        "business_hooks_enabled": request.app.state.settings.business_hooks_enabled,
        "implemented": [],
        "infrastructure": ["postgresql", "sessions", "csrf", "admin_auth"],
        "supported_roles": [role.value for role in Role],
        "reserved_hooks": [
            "cdk.validate",
            "cdk.reserve",
            "cdk.consume",
            "cdk.release",
            "cdk.revoke",
            "resource.acquire",
            "resource.health_check",
            "resource.release",
            "resource.cleanup",
            "claim.claim_batch",
            "claim.get_snapshot",
            "claim.confirm",
            "claim.retry",
            "claim.follow_up",
            "admin.create_cdk_batch",
            "admin.reconcile_claim",
        ],
    }


@api_router.post("/claims/batches", status_code=202, tags=["claims"])
async def claim_batch(
    payload: ClaimBatchRequest, actor: UserActor, hooks: Hooks
) -> dict[str, object]:
    data = await hooks.claims.claim_batch(payload.codes, actor_id=str(actor.id))
    return {"status": "accepted", "data": data}


@api_router.get("/claims/{claim_id}", tags=["claims"])
async def claim_snapshot(claim_id: UUID, actor: UserActor, hooks: Hooks) -> dict[str, object]:
    data = await hooks.claims.get_snapshot(claim_id, actor_id=str(actor.id))
    return {"status": "ok", "data": data}


@api_router.post("/claims/{claim_id}/confirm", status_code=202, tags=["claims"])
async def confirm_claim(claim_id: UUID, actor: UserActor, hooks: Hooks) -> dict[str, object]:
    data = await hooks.claims.confirm(claim_id, actor_id=str(actor.id))
    return {"status": "accepted", "data": data}


@api_router.post("/claims/{claim_id}/retry", status_code=202, tags=["claims"])
async def retry_claim(
    claim_id: UUID, payload: ClaimActionRequest, actor: UserActor, hooks: Hooks
) -> dict[str, object]:
    data = await hooks.claims.retry(claim_id, actor_id=str(actor.id), reason=payload.reason)
    return {"status": "accepted", "data": data}


@api_router.post("/claims/{claim_id}/followup", status_code=202, tags=["claims"])
async def follow_up_claim(claim_id: UUID, actor: UserActor, hooks: Hooks) -> dict[str, object]:
    data = await hooks.claims.follow_up(claim_id, actor_id=str(actor.id))
    return {"status": "accepted", "data": data}


@api_router.post("/admin/cdk-batches", status_code=202, tags=["admin"])
async def create_cdk_batch(
    payload: CdkBatchRequest, actor: AdminActor, hooks: Hooks
) -> dict[str, object]:
    data = await hooks.admin.create_cdk_batch(payload.model_dump(), actor_id=str(actor.id))
    return {"status": "accepted", "data": data}


@api_router.post("/admin/claims/{claim_id}/reconcile", status_code=202, tags=["admin"])
async def reconcile_claim(
    claim_id: UUID, payload: ReconcileRequest, actor: AdminActor, hooks: Hooks
) -> dict[str, object]:
    data = await hooks.admin.reconcile_claim(claim_id, actor_id=str(actor.id))
    return {"status": "accepted", "reason": payload.reason, "data": data}


async def hook_not_implemented_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HookNotImplementedError)
    return JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "HOOK_NOT_IMPLEMENTED",
                "hook": exc.hook_name,
                "message": "该业务 Hook 尚未接入，当前版本只提供接口骨架。",
            }
        },
    )
