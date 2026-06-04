from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("")
async def list_workers() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
