"""Runtime execution contracts for Docker-backed function execution."""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.domain.enums import InvocationAttemptStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation
from worker.app.core.config import settings
from worker.app.queue.consumer import InvocationTask

JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None

TASK_DIR = "/var/task"
ZIP_TASK_PATH = f"{TASK_DIR}/function.zip"
INPUT_PATH = "/var/input.json"
CONTAINER_COMMAND = f"python /opt/runtime/runner.py < {INPUT_PATH}"


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


@dataclass(frozen=True)
class RuntimeInvocationSpec:
    invocation_id: UUID
    function_name: str
    function_version: int
    handler: str
    package_uri: str
    payload: JsonValue
    memory_limit_mb: int
    cpu_limit: float
    timeout_seconds: float


class RuntimeInvocationNotFoundError(Exception):
    def __init__(self, invocation_id: UUID) -> None:
        super().__init__(f"Runtime invocation not found: {invocation_id}")
        self.invocation_id = invocation_id


class DockerRuntimeExecutor:
    def __init__(
        self,
        session: AsyncSession,
        *,
        docker_client: Any | None = None,
        runtime_image: str = settings.runtime_image,
        workspace_root: Path | str | None = settings.workspace_root,
        storage_root: Path | str = settings.storage_root,
        max_inline_result_bytes: int = settings.max_inline_result_bytes,
        max_log_bytes: int = settings.max_log_bytes,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.docker_client = docker_client
        self.runtime_image = runtime_image
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.storage_root = self.resolve_path(storage_root)
        self.max_inline_result_bytes = max_inline_result_bytes
        self.max_log_bytes = max_log_bytes
        self.clock = clock or self.utcnow

    async def execute(self, task: InvocationTask) -> RuntimeExecutionResult:
        """Execute one invocation task inside a Docker runtime container."""
        remaining_seconds = self.remaining_deadline_seconds(task)
        if remaining_seconds <= 0:
            return RuntimeExecutionResult.timed_out(
                "Invocation deadline exceeded before runtime start",
                duration_ms=0,
            )
        spec = await self.load_invocation_spec(
            task,
            remaining_seconds=remaining_seconds,
        )
        return await asyncio.to_thread(self.execute_container, spec)

    async def load_invocation_spec(
        self,
        task: InvocationTask,
        *,
        remaining_seconds: float,
    ) -> RuntimeInvocationSpec:
        result = await self.session.execute(
            select(Invocation, FunctionVersion, Function)
            .join(
                FunctionVersion,
                Invocation.function_version_id == FunctionVersion.id,
            )
            .join(Function, FunctionVersion.function_id == Function.id)
            .where(
                Invocation.id == task.invocation_id,
                Invocation.function_version_id == task.function_version_id,
                Invocation.owner_id == task.owner_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            raise RuntimeInvocationNotFoundError(task.invocation_id)

        invocation, version, function = row
        return RuntimeInvocationSpec(
            invocation_id=invocation.id,
            function_name=function.name,
            function_version=version.version_number,
            handler=version.handler,
            package_uri=version.package_uri,
            payload=invocation.payload_inline,
            memory_limit_mb=version.memory_limit_mb,
            cpu_limit=float(version.cpu_limit),
            timeout_seconds=min(float(version.timeout_seconds), remaining_seconds),
        )

    def execute_container(self, spec: RuntimeInvocationSpec) -> RuntimeExecutionResult:
        docker_client = self.get_docker_client()
        package_path = self.resolve_path(spec.package_uri)
        if not package_path.exists():
            raise FileNotFoundError(f"Function package not found: {package_path}")

        input_path = self.write_runtime_input(spec)
        container = None
        started = time.monotonic()
        try:
            container = docker_client.containers.run(
                self.runtime_image,
                command=[CONTAINER_COMMAND],
                entrypoint=["/bin/sh", "-c"],
                detach=True,
                stdout=True,
                stderr=True,
                environment=self.build_environment(spec, package_path),
                volumes=self.build_volumes(package_path, input_path),
                working_dir=TASK_DIR,
                mem_limit=f"{spec.memory_limit_mb}m",
                nano_cpus=max(1, int(spec.cpu_limit * 1_000_000_000)),
                network_disabled=True,
                read_only=True,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                pids_limit=64,
                tmpfs={"/tmp": "rw,noexec,nosuid,size=64m"},
                remove=False,
            )
            wait_result = container.wait(timeout=spec.timeout_seconds)
            duration_ms = self.elapsed_ms(started)
            exit_code = self.extract_exit_code(wait_result)
            stdout = self.read_logs(container, stdout=True, stderr=False)
            stderr = self.read_logs(container, stdout=False, stderr=True)
            logs_ref = self.write_logs(spec.invocation_id, stderr)
            return self.parse_runtime_output(
                invocation_id=spec.invocation_id,
                stdout=stdout,
                exit_code=exit_code,
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=getattr(container, "id", None),
            )
        except Exception as exc:
            if not self.is_timeout_error(exc):
                raise

            duration_ms = self.elapsed_ms(started)
            if container is not None:
                container.kill()
                stderr = self.read_logs(container, stdout=False, stderr=True)
            else:
                stderr = b""

            logs_ref = self.write_logs(spec.invocation_id, stderr)
            return RuntimeExecutionResult.timed_out(
                f"Invocation exceeded {spec.timeout_seconds:g}s timeout",
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=getattr(container, "id", None),
            )
        finally:
            if container is not None:
                container.remove(force=True)
            input_path.unlink(missing_ok=True)

    def build_environment(
        self,
        spec: RuntimeInvocationSpec,
        package_path: Path,
    ) -> dict[str, str]:
        python_path = TASK_DIR if package_path.is_dir() else ZIP_TASK_PATH
        return {
            "HANDLER": spec.handler,
            "PYTHONPATH": python_path,
        }

    def build_volumes(
        self,
        package_path: Path,
        input_path: Path,
    ) -> dict[str, dict[str, str]]:
        package_bind = TASK_DIR if package_path.is_dir() else ZIP_TASK_PATH
        return {
            str(package_path): {"bind": package_bind, "mode": "ro"},
            str(input_path): {"bind": INPUT_PATH, "mode": "ro"},
        }

    def build_input_message(self, spec: RuntimeInvocationSpec) -> dict[str, Any]:
        return {
            "event": spec.payload if spec.payload is not None else {},
            "context": {
                "invocation_id": str(spec.invocation_id),
                "function_name": spec.function_name,
                "function_version": str(spec.function_version),
                "deadline_ms": int(spec.timeout_seconds * 1000),
                "memory_limit_mb": spec.memory_limit_mb,
            },
        }

    def write_runtime_input(self, spec: RuntimeInvocationSpec) -> Path:
        input_dir = self.storage_root / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_path = input_dir / f"{spec.invocation_id}.json"
        input_path.write_text(
            json.dumps(self.build_input_message(spec), separators=(",", ":")),
            encoding="utf-8",
        )
        return input_path

    def parse_runtime_output(
        self,
        *,
        invocation_id: UUID,
        stdout: bytes,
        exit_code: int,
        duration_ms: int,
        logs_ref: str | None,
        container_id: str | None,
    ) -> RuntimeExecutionResult:
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        if not stdout_text:
            return RuntimeExecutionResult.failed(
                "InvalidRuntimeOutput",
                f"Runtime produced no JSON result; exit code {exit_code}",
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        try:
            envelope = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            return RuntimeExecutionResult.failed(
                "InvalidRuntimeOutput",
                f"Runtime stdout is not valid JSON: {exc.msg}",
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        if not isinstance(envelope, dict):
            return RuntimeExecutionResult.failed(
                "InvalidRuntimeOutput",
                "Runtime stdout JSON must be an object",
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        if envelope.get("ok") is True and exit_code == 0:
            return self.build_success_result(
                invocation_id=invocation_id,
                result=envelope.get("result"),
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        if envelope.get("ok") is False:
            return RuntimeExecutionResult.failed(
                str(envelope.get("error_type") or "RuntimeError"),
                str(envelope.get("error_message") or f"Runtime exited with code {exit_code}"),
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        return RuntimeExecutionResult.failed(
            "RuntimeExitError",
            f"Runtime exited with code {exit_code}",
            duration_ms=duration_ms,
            logs_ref=logs_ref,
            container_id=container_id,
            exit_code=exit_code,
        )

    def build_success_result(
        self,
        *,
        invocation_id: UUID,
        result: JsonValue,
        duration_ms: int,
        logs_ref: str | None,
        container_id: str | None,
        exit_code: int,
    ) -> RuntimeExecutionResult:
        result_bytes = json.dumps(result, separators=(",", ":")).encode("utf-8")
        if len(result_bytes) <= self.max_inline_result_bytes:
            return RuntimeExecutionResult.succeeded(
                result,
                duration_ms=duration_ms,
                logs_ref=logs_ref,
                container_id=container_id,
                exit_code=exit_code,
            )

        result_path = self.write_result(invocation_id, result_bytes)
        return RuntimeExecutionResult(
            status=InvocationAttemptStatus.SUCCEEDED,
            result_ref=self.storage_ref(result_path),
            logs_ref=logs_ref,
            container_id=container_id,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )

    def write_result(self, invocation_id: UUID, result_bytes: bytes) -> Path:
        results_dir = self.storage_root / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        result_path = results_dir / f"{invocation_id}.json"
        result_path.write_bytes(result_bytes)
        return result_path

    def write_logs(self, invocation_id: UUID, logs: bytes) -> str | None:
        if not logs:
            return None

        logs_dir = self.storage_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{invocation_id}.stderr.log"
        log_path.write_bytes(logs[: self.max_log_bytes])
        return self.storage_ref(log_path)

    def storage_ref(self, path: Path) -> str:
        try:
            return path.relative_to(self.workspace_root).as_posix()
        except ValueError:
            return path.as_posix()

    def resolve_path(self, path: Path | str) -> Path:
        resolved = Path(path)
        if resolved.is_absolute():
            return resolved
        return (self.workspace_root / resolved).resolve()

    def remaining_deadline_seconds(self, task: InvocationTask) -> float:
        return max(0.0, (task.deadline_at - self.clock()).total_seconds())

    @staticmethod
    def utcnow() -> datetime:
        return datetime.now(UTC).replace(tzinfo=None)

    def get_docker_client(self) -> Any:
        if self.docker_client is None:
            import docker

            self.docker_client = docker.from_env()
        return self.docker_client

    @staticmethod
    def extract_exit_code(wait_result: object) -> int:
        if isinstance(wait_result, dict):
            value = wait_result.get("StatusCode", 1)
        else:
            value = wait_result
        if isinstance(value, (bytes, str, int, float)):
            return int(value)
        return 1

    @staticmethod
    def elapsed_ms(started: float) -> int:
        return max(0, int((time.monotonic() - started) * 1000))

    @staticmethod
    def read_logs(container: Any, *, stdout: bool, stderr: bool) -> bytes:
        logs = container.logs(stdout=stdout, stderr=stderr)
        if isinstance(logs, str):
            return logs.encode("utf-8")
        return logs or b""

    @staticmethod
    def is_timeout_error(exc: Exception) -> bool:
        return isinstance(exc, TimeoutError) or "Timeout" in exc.__class__.__name__
