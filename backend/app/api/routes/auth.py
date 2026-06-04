from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.post("/register")
async def register() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.post("/login")
async def login() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
