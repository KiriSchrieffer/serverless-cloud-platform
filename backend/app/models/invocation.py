"""Invocation and invocation-attempt models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, Uuid

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus
from backend.app.models.base import Base, TimestampMixin


class Invocation(TimestampMixin, Base):
    __tablename__ = "invocations"
    __table_args__ = (
        Index("ix_invocations_owner_status_created", "owner_id", "status", "created_at"),
        Index(
            "uq_invocations_owner_idempotency_key",
            "owner_id",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    function_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("function_versions.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[InvocationStatus] = mapped_column(
        Enum(InvocationStatus, name="invocation_status"),
        index=True,
        nullable=False,
    )
    payload_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    payload_inline: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    result_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    result_inline: Mapped[dict | list | str | int | float | bool | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    owner = relationship("User", back_populates="invocations")
    function_version = relationship("FunctionVersion", back_populates="invocations")
    attempts = relationship(
        "InvocationAttempt",
        back_populates="invocation",
        cascade="all, delete-orphan",
        order_by="InvocationAttempt.attempt_number",
    )
    dispatches = relationship(
        "InvocationDispatch",
        back_populates="invocation",
        cascade="all, delete-orphan",
        order_by="InvocationDispatch.attempt_number",
    )


class InvocationAttempt(Base):
    __tablename__ = "invocation_attempts"
    __table_args__ = (
        UniqueConstraint(
            "invocation_id",
            "attempt_number",
            name="uq_invocation_attempts_invocation_attempt",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    invocation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invocations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    worker_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workers.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[InvocationAttemptStatus] = mapped_column(
        Enum(InvocationAttemptStatus, name="invocation_attempt_status"),
        nullable=False,
    )
    container_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    logs_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    started_at: Mapped[datetime] = mapped_column(nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    invocation = relationship("Invocation", back_populates="attempts")
    worker = relationship("Worker", back_populates="attempts")
