"""Worker health response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from backend.app.domain.enums import WorkerStatus


class WorkerRead(BaseModel):
    id: UUID
    hostname: str
    consumer_name: str | None = None
    status: WorkerStatus
    last_heartbeat: datetime
    heartbeat_age_seconds: int
    stale: bool
    active_invocations: int
    max_concurrency: int
    started_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
