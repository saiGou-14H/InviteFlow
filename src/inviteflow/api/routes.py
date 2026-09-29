from collections.abc import AsyncIterator
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from inviteflow.api.schemas import (
    CdkBatchRequest,
    ClaimActionRequest,
    ClaimBatchRequest,
    DealerLookupRequest,
    ReconcileRequest,
)
from inviteflow.domain.hooks import HookRegistry, HookNotImplementedError

public_router = APIRouter(tags=["system"])
api_router = APIRouter()


async def get_hooks(request: Request) -> AsyncIterator[HookRegistry]:
    yield cast(HookRegistry, request.app.state.hooks)


@public_router.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "inviteflow"}


@public_router.get("/readyz", tags=["system"])
async def readyz(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "business_hooks_enabled": settings.business_hooks_enabled,
        "persistence": "not_configured",
    }


@api_router.get("/capabilities", tags=["system"])
async def capabilities(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    return {
        "service": "inviteflow",
        "version": "0.1.0",
        "business_hooks_enabled": settings.business_hooks_enabled,
        "implemented": [],
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
            "claim.confirm",
            "claim.retry",
            "claim.follow_up",
            "admin.create_cdk_batch",
            "admin.reconcile_claim",
            "dealer.lookup_cdk",
            "dealer.probe_resend",
        ],
    }


@api_router.post("/claims/batches", status_code=202, tags=["claims"])
async def claim_batch(
    payload: ClaimBatchRequest,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.claims.claim_batch(payload.codes, actor_id="public-session")
    return {"status": "accepted", "data": data}


@api_router.get("/claims/{claim_id}", tags=["claims"])
async def claim_snapshot(claim_id: UUID, hooks: HookRegistry = Depends(get_hooks)) -> dict[str, object]:
    data = await hooks.claims.get_snapshot(claim_id, actor_id="public-session")
    return {"status": "ok", "data": data}


@api_router.post("/claims/{claim_id}/confirm", status_code=202, tags=["claims"])
async def confirm_claim(claim_id: UUID, hooks: HookRegistry = Depends(get_hooks)) -> dict[str, object]:
    data = await hooks.claims.confirm(claim_id, actor_id="public-session")
    return {"status": "accepted", "data": data}


@api_router.post("/claims/{claim_id}/retry", status_code=202, tags=["claims"])
async def retry_claim(
    claim_id: UUID,
    payload: ClaimActionRequest,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.claims.retry(claim_id, actor_id="public-session", reason=payload.reason)
    return {"status": "accepted", "data": data}


@api_router.post("/claims/{claim_id}/followup", status_code=202, tags=["claims"])
async def follow_up_claim(claim_id: UUID, hooks: HookRegistry = Depends(get_hooks)) -> dict[str, object]:
    data = await hooks.claims.follow_up(claim_id, actor_id="public-session")
    return {"status": "accepted", "data": data}


@api_router.post("/admin/cdk-batches", status_code=202, tags=["admin"])
async def create_cdk_batch(
    payload: CdkBatchRequest,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.admin.create_cdk_batch(payload.model_dump(), actor_id="admin-session")
    return {"status": "accepted", "data": data}


@api_router.post("/admin/claims/{claim_id}/reconcile", status_code=202, tags=["admin"])
async def reconcile_claim(
    claim_id: UUID,
    payload: ReconcileRequest,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.admin.reconcile_claim(claim_id, actor_id="admin-session")
    return {"status": "accepted", "reason": payload.reason, "data": data}


@api_router.post("/dealer/cdks/lookup", tags=["dealer"])
async def dealer_lookup(
    payload: DealerLookupRequest,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.dealer.lookup_cdk(payload.code, actor_id="dealer-session")
    return {"status": "ok", "data": data}


@api_router.post("/dealer/claims/{claim_id}/probe-resend", status_code=202, tags=["dealer"])
async def dealer_probe_resend(
    claim_id: UUID,
    hooks: HookRegistry = Depends(get_hooks),
) -> dict[str, object]:
    data = await hooks.dealer.probe_resend(claim_id, actor_id="dealer-session")
    return {"status": "accepted", "data": data}


async def hook_not_implemented_handler(_: Request, exc: HookNotImplementedError) -> JSONResponse:
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
