from uuid import UUID

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.dependencies import get_current_user_id, get_invocation_service
from backend.app.schemas.invocation import InvocationRead
from backend.app.services.invocations import InvocationNotFoundError, InvocationService

router = APIRouter()


@router.get("/{invocation_id}", response_model=InvocationRead)
async def get_invocation(
    invocation_id: UUID,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    invocations: Annotated[InvocationService, Depends(get_invocation_service)],
) -> InvocationRead:
    try:
        return await invocations.get_invocation(owner_id=owner_id, invocation_id=invocation_id)
    except InvocationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation '{exc.invocation_id}' not found",
        ) from exc


@router.get("/{invocation_id}/logs")
async def get_invocation_logs(invocation_id: UUID) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
