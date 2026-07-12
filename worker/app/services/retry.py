"""Worker-side retry policy decisions."""

import random
from collections.abc import Callable
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
    delay_seconds: float = 0.0


class RetryPolicy:
    def __init__(
        self,
        max_attempts: int,
        *,
        initial_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.initial_backoff_seconds = initial_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.jitter = jitter or (lambda upper: random.uniform(0.0, upper))

    def decide(
        self,
        *,
        execution_result: RuntimeExecutionResult,
        attempt_number: int,
    ) -> RetryDecision:
        attempts_remaining = max(0, self.max_attempts - attempt_number)
        should_retry = (
            attempts_remaining > 0
            and self.is_retryable_execution_result(execution_result)
        )
        return RetryDecision(
            should_retry=should_retry,
            attempts_remaining=attempts_remaining,
            delay_seconds=(
                self.backoff_delay_seconds(attempt_number) if should_retry else 0.0
            ),
        )

    def backoff_delay_seconds(self, attempt_number: int) -> float:
        base_delay = min(
            self.max_backoff_seconds,
            self.initial_backoff_seconds * (2 ** max(0, attempt_number - 1)),
        )
        jitter_ceiling = min(base_delay * 0.25, self.max_backoff_seconds - base_delay)
        return round(base_delay + self.jitter(jitter_ceiling), 3)

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
