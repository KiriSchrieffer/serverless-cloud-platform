"""Invocation lifecycle service for accepted asynchronous requests."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation
from backend.app.services.function_registry import FunctionNotFoundError


class FunctionVersionNotFoundError(Exception):
    def __init__(self, function_name: str, version_number: int | None = None) -> None:
        if version_number is None:
            message = f"Function has no versions: {function_name}"
        else:
            message = f"Function version not found: {function_name}:{version_number}"
        super().__init__(message)
        self.function_name = function_name
        self.version_number = version_number


class InvocationNotFoundError(Exception):
    def __init__(self, invocation_id: UUID) -> None:
        super().__init__(f"Invocation not found: {invocation_id}")
        self.invocation_id = invocation_id


@dataclass(frozen=True)
class InvocationCreateResult:
    invocation: Invocation
    created: bool


class InvocationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_invocation(
        self,
        owner_id: UUID,
        function_name: str,
        payload: dict | list | str | int | float | bool | None,
        idempotency_key: str | None = None,
        version_number: int | None = None,
    ) -> InvocationCreateResult:
        if idempotency_key is not None:
            existing = await self.get_invocation_by_idempotency_key(owner_id, idempotency_key)
            if existing is not None:
                return InvocationCreateResult(invocation=existing, created=False)

        function_version = await self.resolve_function_version(
            owner_id=owner_id,
            function_name=function_name,
            version_number=version_number,
        )
        queued_at = self.utcnow()
        deadline_at = queued_at + timedelta(seconds=function_version.timeout_seconds)

        invocation = Invocation(
            owner_id=owner_id,
            function_version_id=function_version.id,
            idempotency_key=idempotency_key,
            status=InvocationStatus.QUEUED,
            payload_inline=payload,
            queued_at=queued_at,
            deadline_at=deadline_at,
            attempt_count=0,
        )
        self.session.add(invocation)
        await self.session.flush()
        return InvocationCreateResult(invocation=invocation, created=True)

    async def commit_invocation(self, invocation: Invocation) -> None:
        await self.session.commit()
        await self.session.refresh(invocation)

    async def rollback(self) -> None:
        await self.session.rollback()

    async def get_invocation(self, owner_id: UUID, invocation_id: UUID) -> Invocation:
        result = await self.session.scalars(
            select(Invocation).where(
                Invocation.id == invocation_id,
                Invocation.owner_id == owner_id,
            )
        )
        invocation = result.one_or_none()
        if invocation is None:
            raise InvocationNotFoundError(invocation_id)
        return invocation

    async def get_invocation_by_idempotency_key(
        self,
        owner_id: UUID,
        idempotency_key: str,
    ) -> Invocation | None:
        result = await self.session.scalars(
            select(Invocation)
            .where(
                Invocation.owner_id == owner_id,
                Invocation.idempotency_key == idempotency_key,
            )
            .order_by(Invocation.created_at)
            .limit(1)
        )
        return result.one_or_none()

    async def resolve_function_version(
        self,
        owner_id: UUID,
        function_name: str,
        version_number: int | None,
    ) -> FunctionVersion:
        function_result = await self.session.scalars(
            select(Function).where(
                Function.owner_id == owner_id,
                Function.name == function_name,
                Function.deleted_at.is_(None),
            )
        )
        function = function_result.one_or_none()
        if function is None:
            raise FunctionNotFoundError(function_name)

        version_query = select(FunctionVersion).where(FunctionVersion.function_id == function.id)
        if version_number is None:
            version_query = version_query.order_by(FunctionVersion.version_number.desc()).limit(1)
        else:
            version_query = version_query.where(FunctionVersion.version_number == version_number)

        version_result = await self.session.scalars(version_query)
        function_version = version_result.one_or_none()
        if function_version is None:
            raise FunctionVersionNotFoundError(function_name, version_number)
        return function_version

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)
