from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.get("/summary")
async def get_metrics_summary() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
