from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from inviteflow.auth import (
    Actor,
    cookie_name,
    create_user_session,
    login_admin,
    logout,
    require_admin,
    require_user,
    session_payload,
    settings_for,
)

router = APIRouter(tags=["sessions"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, Field(min_length=1, max_length=128)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


@router.post("/public/sessions")
async def create_public_session(request: Request, response: Response) -> dict[str, object]:
    return {"status": "ok", "data": await create_user_session(request, response)}


@router.get("/public/session")
async def public_session(
    request: Request, actor: Annotated[Actor, Depends(require_user)]
) -> dict[str, object]:
    settings = settings_for(request)
    token = request.cookies[cookie_name(settings, actor.role)]
    return {"status": "ok", "data": session_payload(settings, token, actor)}


@router.post("/public/logout")
async def public_logout(
    request: Request, response: Response, actor: Annotated[Actor, Depends(require_user)]
) -> dict[str, str]:
    return await logout(request, response, actor)


@router.post("/staff/login")
async def staff_login(
    payload: LoginRequest, request: Request, response: Response
) -> dict[str, object]:
    return {
        "status": "ok",
        "data": await login_admin(request, response, payload.username, payload.password),
    }


@router.get("/staff/session")
async def staff_session(
    request: Request, actor: Annotated[Actor, Depends(require_admin)]
) -> dict[str, object]:
    settings = settings_for(request)
    token = request.cookies[cookie_name(settings, actor.role)]
    return {"status": "ok", "data": session_payload(settings, token, actor)}


@router.post("/staff/logout")
async def staff_logout(
    request: Request, response: Response, actor: Annotated[Actor, Depends(require_admin)]
) -> dict[str, str]:
    return await logout(request, response, actor)
