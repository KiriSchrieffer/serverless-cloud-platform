"""Function and immutable function-version models."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.models.base import Base, TimestampMixin


class Function(TimestampMixin, Base):
    __tablename__ = "functions"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_functions_owner_name"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    owner = relationship("User", back_populates="functions")
    versions = relationship(
        "FunctionVersion",
        back_populates="function",
        cascade="all, delete-orphan",
        order_by="FunctionVersion.version_number",
    )


class FunctionVersion(Base):
    __tablename__ = "function_versions"
    __table_args__ = (
        UniqueConstraint("function_id", "version_number", name="uq_function_versions_function_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    function_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("functions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    runtime: Mapped[str] = mapped_column(String(64), nullable=False)
    handler: Mapped[str] = mapped_column(String(255), nullable=False)
    package_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    package_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_limit_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_limit: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    function = relationship("Function", back_populates="versions")
    invocations = relationship("Invocation", back_populates="function_version")
