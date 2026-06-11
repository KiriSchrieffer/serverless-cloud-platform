from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.dependencies import get_current_user_id, get_function_registry_service
from backend.app.schemas.function import FunctionCreate, FunctionRead
from backend.app.services.function_registry import (
    FunctionNameAlreadyExistsError,
    FunctionRegistryService,
)

router = APIRouter()


@router.post("", response_model=FunctionRead, status_code=status.HTTP_201_CREATED)
async def create_function(
    payload: FunctionCreate,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    registry: Annotated[FunctionRegistryService, Depends(get_function_registry_service)],
) -> FunctionRead:
    try:
        return await registry.create_function(owner_id=owner_id, name=payload.name)
    except FunctionNameAlreadyExistsError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Function '{exc.name}' already exists",
        ) from exc


@router.get("", response_model=list[FunctionRead])
async def list_functions(
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    registry: Annotated[FunctionRegistryService, Depends(get_function_registry_service)],
) -> list[FunctionRead]:
    return await registry.list_functions(owner_id=owner_id)


@router.post("/{function_name}/versions")
async def create_function_version(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.get("/{function_name}/versions")
async def list_function_versions(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)


@router.post("/{function_name}/invoke")
async def invoke_function(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
