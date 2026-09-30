from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field


class ClaimBatchRequest(BaseModel):
    codes: Annotated[list[str], Field(min_length=1, max_length=100)]


class ClaimActionRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)] = "user_requested"


class CdkBatchRequest(BaseModel):
    quantity: Annotated[int, Field(ge=1, le=1000)]
    max_uses: Annotated[int, Field(ge=1, le=1000)] = 1
    expires_at: str | None = None


class ReconcileRequest(BaseModel):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class HookResponse(BaseModel):
    hook: str
    status: str
    data: dict[str, object] | None = None


ClaimId = Annotated[UUID, Field(description="Claim identifier")]
CdkId = Annotated[UUID, Field(description="CDK identifier")]
