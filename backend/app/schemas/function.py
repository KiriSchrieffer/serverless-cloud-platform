"""Function registration, version upload, and listing schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FUNCTION_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"


class FunctionCreate(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=128,
        pattern=FUNCTION_NAME_PATTERN,
        description="Function name unique per user.",
    )


class FunctionRead(BaseModel):
    id: UUID
    owner_id: UUID
    name: str
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
