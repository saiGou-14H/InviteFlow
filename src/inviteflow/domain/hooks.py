from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import UUID


class HookNotImplementedError(RuntimeError):
    """Raised when a business boundary has no concrete implementation yet."""

    def __init__(self, hook_name: str) -> None:
        self.hook_name = hook_name
        super().__init__(f"business hook is not implemented: {hook_name}")


class CdkHook(Protocol):
    async def validate(self, code: str) -> dict[str, Any]: ...

    async def reserve(self, cdk_id: UUID, claim_id: UUID) -> dict[str, Any]: ...

    async def consume(self, cdk_id: UUID, claim_id: UUID) -> dict[str, Any]: ...

    async def release(self, cdk_id: UUID, claim_id: UUID, *, reason: str) -> dict[str, Any]: ...

    async def revoke(self, cdk_id: UUID, *, actor_id: str, reason: str) -> dict[str, Any]: ...


class ResourceHook(Protocol):
    async def acquire(self, claim_id: UUID) -> dict[str, Any]: ...

    async def health_check(self, resource_id: UUID) -> dict[str, Any]: ...

    async def release(self, resource_id: UUID, *, reason: str) -> dict[str, Any]: ...

    async def cleanup(self, resource_id: UUID, *, generation: int) -> dict[str, Any]: ...


class ClaimHook(Protocol):
    async def claim_batch(self, codes: list[str], *, actor_id: str) -> dict[str, Any]: ...

    async def get_snapshot(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]: ...

    async def confirm(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]: ...

    async def retry(self, claim_id: UUID, *, actor_id: str, reason: str) -> dict[str, Any]: ...

    async def follow_up(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]: ...


class AdminHook(Protocol):
    async def create_cdk_batch(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]: ...

    async def reconcile_claim(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]: ...


class DealerHook(Protocol):
    async def lookup_cdk(self, code: str, *, actor_id: str) -> dict[str, Any]: ...

    async def probe_resend(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]: ...


class InvitationOutcome(str, Enum):
    CONFIRMED_SUCCESS = "confirmed_success"
    CONFIRMED_NO_EFFECT = "confirmed_no_effect"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProviderResult:
    outcome: InvitationOutcome
    result_code: str
    evidence_reference: str | None = None


class InvitationProvider(Protocol):
    """Provider boundary for a reviewed external invitation integration."""

    async def probe(self, *, operation_id: UUID, resource_id: UUID) -> ProviderResult: ...

    async def execute(self, *, operation_id: UUID, resource_id: UUID) -> ProviderResult: ...

    async def reconcile(self, *, operation_id: UUID, resource_id: UUID) -> ProviderResult: ...


class NotImplementedCdkHook:
    async def validate(self, code: str) -> dict[str, Any]:
        raise HookNotImplementedError("cdk.validate")

    async def reserve(self, cdk_id: UUID, claim_id: UUID) -> dict[str, Any]:
        raise HookNotImplementedError("cdk.reserve")

    async def consume(self, cdk_id: UUID, claim_id: UUID) -> dict[str, Any]:
        raise HookNotImplementedError("cdk.consume")

    async def release(self, cdk_id: UUID, claim_id: UUID, *, reason: str) -> dict[str, Any]:
        raise HookNotImplementedError("cdk.release")

    async def revoke(self, cdk_id: UUID, *, actor_id: str, reason: str) -> dict[str, Any]:
        raise HookNotImplementedError("cdk.revoke")


class NotImplementedResourceHook:
    async def acquire(self, claim_id: UUID) -> dict[str, Any]:
        raise HookNotImplementedError("resource.acquire")

    async def health_check(self, resource_id: UUID) -> dict[str, Any]:
        raise HookNotImplementedError("resource.health_check")

    async def release(self, resource_id: UUID, *, reason: str) -> dict[str, Any]:
        raise HookNotImplementedError("resource.release")

    async def cleanup(self, resource_id: UUID, *, generation: int) -> dict[str, Any]:
        raise HookNotImplementedError("resource.cleanup")


class NotImplementedClaimHook:
    async def claim_batch(self, codes: list[str], *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("claim.claim_batch")

    async def get_snapshot(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("claim.get_snapshot")

    async def confirm(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("claim.confirm")

    async def retry(self, claim_id: UUID, *, actor_id: str, reason: str) -> dict[str, Any]:
        raise HookNotImplementedError("claim.retry")

    async def follow_up(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("claim.follow_up")


class NotImplementedAdminHook:
    async def create_cdk_batch(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("admin.create_cdk_batch")

    async def reconcile_claim(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("admin.reconcile_claim")


class NotImplementedDealerHook:
    async def lookup_cdk(self, code: str, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("dealer.lookup_cdk")

    async def probe_resend(self, claim_id: UUID, *, actor_id: str) -> dict[str, Any]:
        raise HookNotImplementedError("dealer.probe_resend")


@dataclass(slots=True)
class HookRegistry:
    cdk: CdkHook
    resource: ResourceHook
    claims: ClaimHook
    admin: AdminHook
    dealer: DealerHook

    @classmethod
    def placeholders(cls) -> "HookRegistry":
        return cls(
            cdk=NotImplementedCdkHook(),
            resource=NotImplementedResourceHook(),
            claims=NotImplementedClaimHook(),
            admin=NotImplementedAdminHook(),
            dealer=NotImplementedDealerHook(),
        )
