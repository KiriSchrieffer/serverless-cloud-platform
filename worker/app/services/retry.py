"""Worker-side retry policy decisions."""

from dataclasses import dataclass

from backend.app.domain.enums import InvocationAttemptStatus
from worker.app.runtime.docker_executor import RuntimeExecutionResult

RETRYABLE_ERROR_TYPES = {
    "DockerException",
    "DockerRuntimeError",
    "FileNotFoundError",
    "InfrastructureError",
    "RuntimeInvocationNotFoundError",
    "RuntimeError",
    "WorkerLostError",
}

NON_RETRYABLE_ERROR_TYPES = {
    "InvalidRuntimeOutput",
    "TimeoutError",
    "ValueError",
}


@dataclass(frozen=True)
class RetryDecision:
    should_retry: bool
    attempts_remaining: int


class RetryPolicy:
    def __init__(self, max_attempts: int) -> None:
        self.max_attempts = max_attempts

    def decide(
        self,
        *,
        execution_result: RuntimeExecutionResult,
        attempt_number: int,
    ) -> RetryDecision:
        attempts_remaining = max(0, self.max_attempts - attempt_number)
        return RetryDecision(
            should_retry=(
                attempts_remaining > 0
                and self.is_retryable_execution_result(execution_result)
            ),
            attempts_remaining=attempts_remaining,
        )

    def is_retryable_execution_result(
        self,
        execution_result: RuntimeExecutionResult,
    ) -> bool:
        if execution_result.status != InvocationAttemptStatus.FAILED:
            return False

        error_type = execution_result.error_type
        if error_type in NON_RETRYABLE_ERROR_TYPES:
            return False
        if error_type in RETRYABLE_ERROR_TYPES:
            return True
        return False
