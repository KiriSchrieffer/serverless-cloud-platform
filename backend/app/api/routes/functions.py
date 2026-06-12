from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from backend.app.api.dependencies import get_current_user_id, get_function_registry_service
from backend.app.schemas.function import (
    FunctionCreate,
    FunctionRead,
    FunctionVersionCreate,
    FunctionVersionRead,
)
from backend.app.services.function_registry import (
    FunctionNotFoundError,
    FunctionNameAlreadyExistsError,
    FunctionRegistryService,
    FunctionVersionConflictError,
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


@router.post(
    "/{function_name}/versions",
    response_model=FunctionVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_function_version(
    function_name: str,
    payload: FunctionVersionCreate,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    registry: Annotated[FunctionRegistryService, Depends(get_function_registry_service)],
) -> FunctionVersionRead:
    try:
        return await registry.create_function_version(
            owner_id=owner_id,
            function_name=function_name,
            runtime=payload.runtime,
            handler=payload.handler,
            package_uri=payload.package_uri,
            package_hash=payload.package_hash,
            memory_limit_mb=payload.memory_limit_mb,
            cpu_limit=payload.cpu_limit,
            timeout_seconds=payload.timeout_seconds,
        )
    except FunctionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function '{exc.name}' not found",
        ) from exc
    except FunctionVersionConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not allocate next version for function '{exc.function_name}'",
        ) from exc


@router.get("/{function_name}/versions", response_model=list[FunctionVersionRead])
async def list_function_versions(
    function_name: str,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    registry: Annotated[FunctionRegistryService, Depends(get_function_registry_service)],
) -> list[FunctionVersionRead]:
    try:
        return await registry.list_function_versions(owner_id=owner_id, function_name=function_name)
    except FunctionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function '{exc.name}' not found",
        ) from exc


@router.post("/{function_name}/invoke")
async def invoke_function(function_name: str) -> None:
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED)
