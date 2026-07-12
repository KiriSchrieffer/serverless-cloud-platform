"""Worker registration and heartbeat model."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import Uuid

from backend.app.domain.enums import WorkerStatus
from backend.app.models.base import Base, TimestampMixin


class Worker(TimestampMixin, Base):
    __tablename__ = "workers"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    consumer_name: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    status: Mapped[WorkerStatus] = mapped_column(
        Enum(WorkerStatus, name="worker_status"),
        index=True,
        nullable=False,
    )
    last_heartbeat: Mapped[datetime] = mapped_column(index=True, nullable=False)
    active_invocations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(nullable=False)

    attempts = relationship("InvocationAttempt", back_populates="worker")
