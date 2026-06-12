"""Function registration, version upload, and listing schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FUNCTION_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_-]{0,127}$"
HANDLER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)+$"


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


class FunctionVersionCreate(BaseModel):
    runtime: str = Field(default="python3.11", pattern=r"^python3\.11$")
    handler: str = Field(
        default="main.handler",
        min_length=3,
        max_length=255,
        pattern=HANDLER_PATTERN,
    )
    package_uri: str = Field(min_length=1, max_length=1024)
    package_hash: str = Field(min_length=16, max_length=128)
    memory_limit_mb: int = Field(default=256, ge=64, le=1024)
    cpu_limit: float = Field(default=0.5, ge=0.1, le=2.0)
    timeout_seconds: int = Field(default=30, ge=1, le=300)


class FunctionVersionRead(BaseModel):
    id: UUID
    function_id: UUID
    version_number: int
    runtime: str
    handler: str
    package_uri: str
    package_hash: str
    memory_limit_mb: int
    cpu_limit: float
    timeout_seconds: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
