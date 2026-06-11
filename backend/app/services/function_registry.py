"""Function and immutable function-version registry service."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.function import Function
from backend.app.models.user import User


class FunctionNameAlreadyExistsError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Function already exists: {name}")
        self.name = name


class FunctionRegistryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_function(self, owner_id: UUID, name: str) -> Function:
        await self.ensure_owner_exists(owner_id)

        existing = await self.get_function_by_name(owner_id=owner_id, name=name)
        if existing is not None:
            raise FunctionNameAlreadyExistsError(name)

        function = Function(owner_id=owner_id, name=name)
        self.session.add(function)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise FunctionNameAlreadyExistsError(name) from exc

        await self.session.refresh(function)
        return function

    async def ensure_owner_exists(self, owner_id: UUID) -> None:
        user = await self.session.get(User, owner_id)
        if user is not None:
            return

        self.session.add(
            User(
                id=owner_id,
                email=f"dev-{owner_id}@example.local",
                password_hash="development-only",
            )
        )

    async def list_functions(self, owner_id: UUID) -> list[Function]:
        result = await self.session.scalars(
            select(Function)
            .where(Function.owner_id == owner_id, Function.deleted_at.is_(None))
            .order_by(Function.created_at, Function.name)
        )
        return list(result)

    async def get_function_by_name(self, owner_id: UUID, name: str) -> Function | None:
        result = await self.session.scalars(
            select(Function).where(
                Function.owner_id == owner_id,
                Function.name == name,
                Function.deleted_at.is_(None),
            )
        )
        return result.one_or_none()
