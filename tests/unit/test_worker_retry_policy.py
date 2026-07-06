from backend.app.domain.enums import InvocationAttemptStatus
from worker.app.runtime.docker_executor import RuntimeExecutionResult
from worker.app.services.retry import RetryPolicy


def test_retry_policy_retries_infrastructure_failures_when_attempts_remain() -> None:
    decision = RetryPolicy(max_attempts=3).decide(
        execution_result=RuntimeExecutionResult.failed(
            "RuntimeError",
            "docker daemon unavailable",
            exit_code=None,
        ),
        attempt_number=1,
    )

    assert decision.should_retry is True
    assert decision.attempts_remaining == 2


def test_retry_policy_stops_when_attempts_are_exhausted() -> None:
    decision = RetryPolicy(max_attempts=3).decide(
        execution_result=RuntimeExecutionResult.failed(
            "RuntimeError",
            "docker daemon unavailable",
            exit_code=None,
        ),
        attempt_number=3,
    )

    assert decision.should_retry is False
    assert decision.attempts_remaining == 0


def test_retry_policy_does_not_retry_user_code_failures() -> None:
    decision = RetryPolicy(max_attempts=3).decide(
        execution_result=RuntimeExecutionResult.failed("ValueError", "invalid input"),
        attempt_number=1,
    )

    assert decision.should_retry is False
    assert decision.attempts_remaining == 2


def test_retry_policy_does_not_retry_timeouts_by_default() -> None:
    decision = RetryPolicy(max_attempts=3).decide(
        execution_result=RuntimeExecutionResult.timed_out("deadline exceeded"),
        attempt_number=1,
    )

    assert decision.should_retry is False
    assert decision.attempts_remaining == 2


def test_retry_policy_does_not_retry_successful_invocations() -> None:
    decision = RetryPolicy(max_attempts=3).decide(
        execution_result=RuntimeExecutionResult(
            status=InvocationAttemptStatus.SUCCEEDED,
            result_inline={"ok": True},
        ),
        attempt_number=1,
    )

    assert decision.should_retry is False
