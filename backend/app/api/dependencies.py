from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import AsyncSessionLocal
from backend.app.services.invocations import InvocationService
from backend.app.services.function_registry import FunctionRegistryService

DEVELOPMENT_OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_current_user_id(
    x_owner_id: Annotated[str | None, Header(alias="X-Owner-Id")] = None,
) -> UUID:
    if x_owner_id is None:
        return DEVELOPMENT_OWNER_ID

    try:
        return UUID(x_owner_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Owner-Id must be a valid UUID",
        ) from exc


def get_function_registry_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FunctionRegistryService:
    return FunctionRegistryService(session)


def get_invocation_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvocationService:
    return InvocationService(session)
