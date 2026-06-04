from uuid import UUID

from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/{invocation_id}")
async def get_invocation(invocation_id: UUID) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.get("/{invocation_id}/logs")
async def get_invocation_logs(invocation_id: UUID) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
