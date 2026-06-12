"""Function and immutable function-version registry service."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.function import Function, FunctionVersion
from backend.app.models.user import User


class FunctionNameAlreadyExistsError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Function already exists: {name}")
        self.name = name


class FunctionNotFoundError(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(f"Function not found: {name}")
        self.name = name


class FunctionVersionConflictError(Exception):
    def __init__(self, function_name: str) -> None:
        super().__init__(f"Could not allocate next version for function: {function_name}")
        self.function_name = function_name


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

    async def create_function_version(
        self,
        owner_id: UUID,
        function_name: str,
        runtime: str,
        handler: str,
        package_uri: str,
        package_hash: str,
        memory_limit_mb: int,
        cpu_limit: float,
        timeout_seconds: int,
    ) -> FunctionVersion:
        function = await self.get_function_by_name(owner_id=owner_id, name=function_name)
        if function is None:
            raise FunctionNotFoundError(function_name)

        next_version_number = await self.get_next_version_number(function.id)
        version = FunctionVersion(
            function_id=function.id,
            version_number=next_version_number,
            runtime=runtime,
            handler=handler,
            package_uri=package_uri,
            package_hash=package_hash,
            memory_limit_mb=memory_limit_mb,
            cpu_limit=cpu_limit,
            timeout_seconds=timeout_seconds,
        )
        self.session.add(version)

        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise FunctionVersionConflictError(function_name) from exc

        await self.session.refresh(version)
        return version

    async def list_function_versions(
        self,
        owner_id: UUID,
        function_name: str,
    ) -> list[FunctionVersion]:
        function = await self.get_function_by_name(owner_id=owner_id, name=function_name)
        if function is None:
            raise FunctionNotFoundError(function_name)

        result = await self.session.scalars(
            select(FunctionVersion)
            .where(FunctionVersion.function_id == function.id)
            .order_by(FunctionVersion.version_number)
        )
        return list(result)

    async def get_next_version_number(self, function_id: UUID) -> int:
        current_max = await self.session.scalar(
            select(func.max(FunctionVersion.version_number)).where(
                FunctionVersion.function_id == function_id
            )
        )
        return (current_max or 0) + 1
