from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_current_user_id, get_metrics_service
from backend.app.core.config import settings
from backend.app.schemas.worker import WorkerRead
from backend.app.services.metrics import PlatformMetricsService

router = APIRouter()


@router.get("", response_model=list[WorkerRead])
async def list_workers(
    _: Annotated[UUID, Depends(get_current_user_id)],
    metrics: Annotated[PlatformMetricsService, Depends(get_metrics_service)],
) -> list[WorkerRead]:
    return await metrics.list_workers(stale_after_seconds=settings.stale_worker_seconds)
