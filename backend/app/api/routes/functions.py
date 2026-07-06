from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from backend.app.api.dependencies import (
    get_current_user_id,
    get_function_registry_service,
    get_invocation_queue_publisher,
    get_invocation_service,
    get_package_storage_service,
)
from backend.app.schemas.invocation import InvocationAccepted, InvocationCreate
from backend.app.schemas.function import (
    HANDLER_PATTERN,
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
from backend.app.services.invocation_queue import (
    InvocationQueuePublishError,
    InvocationQueuePublisherProtocol,
)
from backend.app.services.invocations import FunctionVersionNotFoundError, InvocationService
from backend.app.services.storage import LocalPackageStorageService, PackageValidationError

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


@router.post(
    "/{function_name}/versions/upload",
    response_model=FunctionVersionRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_function_version(
    function_name: str,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    registry: Annotated[FunctionRegistryService, Depends(get_function_registry_service)],
    storage_service: Annotated[
        LocalPackageStorageService,
        Depends(get_package_storage_service),
    ],
    package: Annotated[UploadFile, File(description="Zip package containing handler code.")],
    runtime: Annotated[str, Form(pattern=r"^python3\.11$")] = "python3.11",
    handler: Annotated[
        str,
        Form(min_length=3, max_length=255, pattern=HANDLER_PATTERN),
    ] = "main.handler",
    memory_limit_mb: Annotated[int, Form(ge=64, le=1024)] = 256,
    cpu_limit: Annotated[float, Form(ge=0.1, le=2.0)] = 0.5,
    timeout_seconds: Annotated[int, Form(ge=1, le=300)] = 30,
) -> FunctionVersionRead:
    try:
        next_version_number = await registry.get_next_function_version_number(
            owner_id=owner_id,
            function_name=function_name,
        )
        stored_package = storage_service.store_function_package(
            owner_id=owner_id,
            function_name=function_name,
            version_number=next_version_number,
            handler=handler,
            contents=await package.read(),
        )
        return await registry.create_function_version(
            owner_id=owner_id,
            function_name=function_name,
            runtime=runtime,
            handler=handler,
            package_uri=stored_package.package_uri,
            package_hash=stored_package.package_hash,
            memory_limit_mb=memory_limit_mb,
            cpu_limit=cpu_limit,
            timeout_seconds=timeout_seconds,
            version_number=next_version_number,
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
    except PackageValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.detail) from exc


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


@router.post(
    "/{function_name}/invoke",
    response_model=InvocationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def invoke_function(
    function_name: str,
    payload: InvocationCreate,
    owner_id: Annotated[UUID, Depends(get_current_user_id)],
    invocations: Annotated[InvocationService, Depends(get_invocation_service)],
    publisher: Annotated[
        InvocationQueuePublisherProtocol,
        Depends(get_invocation_queue_publisher),
    ],
) -> InvocationAccepted:
    try:
        result = await invocations.create_invocation(
            owner_id=owner_id,
            function_name=function_name,
            payload=payload.payload,
            idempotency_key=payload.idempotency_key,
            version_number=payload.version_number,
        )
        invocation = result.invocation
        if result.created:
            await publisher.publish_invocation(invocation)
            await invocations.commit_invocation(invocation)
    except FunctionNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Function '{exc.name}' not found",
        ) from exc
    except FunctionVersionNotFoundError as exc:
        if exc.version_number is None:
            detail = f"Function '{exc.function_name}' has no versions"
        else:
            detail = f"Function '{exc.function_name}' version {exc.version_number} not found"
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail) from exc
    except InvocationQueuePublishError as exc:
        await invocations.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to enqueue invocation '{exc.invocation_id}'",
        ) from exc

    return InvocationAccepted(
        invocation_id=invocation.id,
        status=invocation.status,
        status_url=f"/invocations/{invocation.id}",
    )
