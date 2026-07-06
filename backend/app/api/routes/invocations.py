from uuid import UUID

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from backend.app.api.dependencies import (
    get_current_user_id,
    get_invocation_service,
    get_log_storage_service,
)
from backend.app.schemas.invocation import InvocationRead
from backend.app.services.invocations import InvocationNotFoundError, InvocationService
from backend.app.services.storage import LocalLogStorageService, LogFileNotFoundError

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
async def get_invocation_logs(
    invocation_id: UUID,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    invocations: Annotated[InvocationService, Depends(get_invocation_service)],
    log_storage: Annotated[LocalLogStorageService, Depends(get_log_storage_service)],
) -> PlainTextResponse:
    try:
        logs_ref = await invocations.get_latest_logs_ref(
            owner_id=owner_id,
            invocation_id=invocation_id,
        )
        return PlainTextResponse(
            content=log_storage.read_logs(logs_ref),
            media_type="text/plain",
        )
    except InvocationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation '{exc.invocation_id}' not found",
        ) from exc
    except LogFileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invocation logs '{exc.logs_ref}' not found",
        ) from exc
