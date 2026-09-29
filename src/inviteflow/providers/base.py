"""External provider interfaces live here until concrete integrations are approved."""

from inviteflow.domain.hooks import InvitationOutcome, InvitationProvider, ProviderResult

__all__ = ["InvitationOutcome", "InvitationProvider", "ProviderResult"]
