"""Invocation request, status, result, and log response schemas."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.enums import InvocationStatus

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


class InvocationCreate(BaseModel):
    payload: JsonValue = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)
    version_number: int | None = Field(default=None, ge=1)


class InvocationAccepted(BaseModel):
    invocation_id: UUID
    status: InvocationStatus
    status_url: str


class InvocationRead(BaseModel):
    id: UUID
    owner_id: UUID
    function_version_id: UUID
    idempotency_key: str | None
    status: InvocationStatus
    payload_ref: str | None
    payload_inline: JsonValue
    result_ref: str | None
    result_inline: JsonValue
    error_type: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    deadline_at: datetime
    attempt_count: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
