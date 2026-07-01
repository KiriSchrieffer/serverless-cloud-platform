"""Runtime execution contracts for Docker-backed function execution."""

from dataclasses import dataclass
from typing import Any

from backend.app.domain.enums import InvocationAttemptStatus
from worker.app.queue.consumer import InvocationTask

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(frozen=True)
class RuntimeExecutionResult:
    status: InvocationAttemptStatus
    result_inline: JsonValue = None
    result_ref: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    logs_ref: str | None = None
    container_id: str | None = None
    exit_code: int | None = None
    duration_ms: int | None = None

    @classmethod
    def succeeded(
        cls,
        result: JsonValue,
        *,
        duration_ms: int | None = None,
        logs_ref: str | None = None,
        container_id: str | None = None,
        exit_code: int = 0,
    ) -> "RuntimeExecutionResult":
        return cls(
            status=InvocationAttemptStatus.SUCCEEDED,
            result_inline=result,
            logs_ref=logs_ref,
            container_id=container_id,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )

    @classmethod
    def failed(
        cls,
        error_type: str,
        error_message: str,
        *,
        duration_ms: int | None = None,
        logs_ref: str | None = None,
        container_id: str | None = None,
        exit_code: int | None = 1,
    ) -> "RuntimeExecutionResult":
        return cls(
            status=InvocationAttemptStatus.FAILED,
            error_type=error_type,
            error_message=error_message,
            logs_ref=logs_ref,
            container_id=container_id,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )

    @classmethod
    def timed_out(
        cls,
        error_message: str,
        *,
        duration_ms: int | None = None,
        logs_ref: str | None = None,
        container_id: str | None = None,
        exit_code: int | None = None,
    ) -> "RuntimeExecutionResult":
        return cls(
            status=InvocationAttemptStatus.TIMEOUT,
            error_type="TimeoutError",
            error_message=error_message,
            logs_ref=logs_ref,
            container_id=container_id,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )


class DockerRuntimeExecutor:
    async def execute(self, task: InvocationTask) -> RuntimeExecutionResult:
        """Execute one invocation task inside a Docker runtime container."""
        raise NotImplementedError("Docker runtime execution will be implemented in a later step")
