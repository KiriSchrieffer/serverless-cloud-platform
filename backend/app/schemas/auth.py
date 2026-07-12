"""Auth request, user, and token response schemas."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.core.security import PasswordTooLongError, password_bytes

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("Email address is invalid")
        return normalized


class RegisterRequest(EmailRequest):
    password: str = Field(min_length=8, max_length=256)

    @field_validator("password")
    @classmethod
    def validate_password_bytes(cls, value: str) -> str:
        try:
            password_bytes(value)
        except PasswordTooLongError as exc:
            raise ValueError(str(exc)) from exc
        return value


class LoginRequest(EmailRequest):
    password: str = Field(min_length=1, max_length=256)


class UserRead(BaseModel):
    id: UUID
    email: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
