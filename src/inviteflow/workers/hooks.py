"""Reserved worker integration points.

Persistent jobs, transaction outbox and provider execution will be implemented after
an external provider contract is reviewed. The initial service deliberately has no
in-memory business worker.
"""

from typing import Protocol
from uuid import UUID


class JobHandler(Protocol):
    async def handle(self, job_id: UUID) -> None: ...


class OutboxPublisher(Protocol):
    async def publish(self, event_id: UUID) -> None: ...


class SchedulerHook(Protocol):
    async def schedule_due_work(self) -> int: ...
