from fastapi import APIRouter, HTTPException, status

router = APIRouter()


@router.post("")
async def create_function() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.get("")
async def list_functions() -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.post("/{function_name}/versions")
async def create_function_version(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.get("/{function_name}/versions")
async def list_function_versions(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.post("/{function_name}/invoke")
async def invoke_function(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
