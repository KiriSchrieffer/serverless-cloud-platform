import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.domain.enums import InvocationAttemptStatus, InvocationStatus
from backend.app.models.function import Function, FunctionVersion
from backend.app.models.invocation import Invocation
from backend.app.models.user import User
from worker.app.queue.consumer import parse_xreadgroup_response
from worker.app.runtime.docker_executor import DockerRuntimeExecutor

OWNER_ID = UUID("00000000-0000-0000-0000-000000000001")


class FakeContainer:
    def __init__(
        self,
        *,
        stdout: bytes = b'{"ok":true,"result":{"message":"hello"}}',
        stderr: bytes = b"",
        status_code: int = 0,
        timeout: bool = False,
    ) -> None:
        self.id = "container-123"
        self.stdout = stdout
        self.stderr = stderr
        self.status_code = status_code
        self.timeout = timeout
        self.wait_timeout = None
        self.killed = False
        self.removed = False

    def wait(self, timeout: float):
        self.wait_timeout = timeout
        if self.timeout:
            raise TimeoutError("container deadline exceeded")
        return {"StatusCode": self.status_code}

    def logs(self, *, stdout: bool, stderr: bool) -> bytes:
        if stdout:
            return self.stdout
        if stderr:
            return self.stderr
        return b""

    def kill(self) -> None:
        self.killed = True

    def remove(self, *, force: bool) -> None:
        self.removed = force


class FakeContainers:
    def __init__(self, container: FakeContainer) -> None:
        self.container = container
        self.run_kwargs = None
        self.input_message = None

    def run(self, image: str, **kwargs):
        self.run_kwargs = {"image": image, **kwargs}
        input_source = self.get_input_source(kwargs["volumes"])
        self.input_message = json.loads(Path(input_source).read_text(encoding="utf-8"))
        return self.container

    @staticmethod
    def get_input_source(volumes: dict[str, dict[str, str]]) -> str:
        for source, options in volumes.items():
            if options["bind"] == "/var/input.json":
                return source
        raise AssertionError("runtime input mount not found")


class FakeDockerClient:
    def __init__(self, container: FakeContainer) -> None:
        self.containers = FakeContainers(container)


@pytest.mark.asyncio
async def test_docker_runtime_executor_runs_container_with_sandbox_options(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    storage_root = tmp_path / "storage"
    fake_container = FakeContainer(stderr=b"debug log")
    docker_client = FakeDockerClient(fake_container)

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        task = make_task(invocation)
        executor = DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            runtime_image="runtime:test",
            workspace_root=tmp_path,
            storage_root=storage_root,
            clock=lambda: invocation.queued_at,
        )

        result = await executor.execute(task)

    run_kwargs = docker_client.containers.run_kwargs
    assert result.status == InvocationAttemptStatus.SUCCEEDED
    assert result.result_inline == {"message": "hello"}
    assert result.logs_ref == "storage/logs/%s.stderr.log" % invocation.id
    assert (storage_root / "logs" / f"{invocation.id}.stderr.log").read_bytes() == b"debug log"
    assert fake_container.removed is True
    assert run_kwargs["image"] == "runtime:test"
    assert run_kwargs["command"] == ["python /opt/runtime/runner.py < /var/input.json"]
    assert run_kwargs["entrypoint"] == ["/bin/sh", "-c"]
    assert run_kwargs["environment"] == {
        "HANDLER": "main.handler",
        "PYTHONPATH": "/var/task/function.zip",
    }
    assert run_kwargs["volumes"][str(package_path)] == {
        "bind": "/var/task/function.zip",
        "mode": "ro",
    }
    assert run_kwargs["mem_limit"] == "256m"
    assert run_kwargs["nano_cpus"] == 500_000_000
    assert run_kwargs["network_disabled"] is True
    assert run_kwargs["read_only"] is True
    assert run_kwargs["cap_drop"] == ["ALL"]
    assert run_kwargs["security_opt"] == ["no-new-privileges"]
    assert run_kwargs["pids_limit"] == 64
    assert run_kwargs["tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=64m"}
    assert docker_client.containers.input_message == {
        "event": {"name": "Ada"},
        "context": {
            "invocation_id": str(invocation.id),
            "function_name": "hello",
            "function_version": "1",
            "deadline_ms": 30000,
            "memory_limit_mb": 256,
        },
    }


@pytest.mark.asyncio
async def test_docker_runtime_executor_maps_runtime_failure(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    fake_container = FakeContainer(
        stdout=b'{"ok":false,"error_type":"ValueError","error_message":"bad input"}',
        stderr=b"traceback",
        status_code=1,
    )
    docker_client = FakeDockerClient(fake_container)

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=tmp_path / "storage",
            clock=lambda: invocation.queued_at,
        ).execute(make_task(invocation))

    assert result.status == InvocationAttemptStatus.FAILED
    assert result.error_type == "ValueError"
    assert result.error_message == "bad input"
    assert result.exit_code == 1
    assert result.logs_ref is not None


@pytest.mark.asyncio
async def test_docker_runtime_executor_rejects_invalid_stdout(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    docker_client = FakeDockerClient(FakeContainer(stdout=b"not-json"))

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=tmp_path / "storage",
            clock=lambda: invocation.queued_at,
        ).execute(make_task(invocation))

    assert result.status == InvocationAttemptStatus.FAILED
    assert result.error_type == "InvalidRuntimeOutput"
    assert "not valid JSON" in result.error_message


@pytest.mark.asyncio
async def test_docker_runtime_executor_returns_timeout_and_kills_container(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    fake_container = FakeContainer(stderr=b"still running", timeout=True)
    docker_client = FakeDockerClient(fake_container)

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=tmp_path / "storage",
            clock=lambda: invocation.queued_at,
        ).execute(make_task(invocation))

    assert result.status == InvocationAttemptStatus.TIMEOUT
    assert result.error_type == "TimeoutError"
    assert "30s timeout" in result.error_message
    assert fake_container.killed is True
    assert fake_container.removed is True


@pytest.mark.asyncio
async def test_docker_runtime_executor_stores_large_results_by_reference(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    storage_root = tmp_path / "storage"
    fake_container = FakeContainer(stdout=b'{"ok":true,"result":{"blob":"abcdef"}}')
    docker_client = FakeDockerClient(fake_container)

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=storage_root,
            max_inline_result_bytes=8,
            clock=lambda: invocation.queued_at,
        ).execute(make_task(invocation))

    assert result.status == InvocationAttemptStatus.SUCCEEDED
    assert result.result_inline is None
    assert result.result_ref == "storage/results/%s.json" % invocation.id
    assert (storage_root / "results" / f"{invocation.id}.json").read_bytes() == (
        b'{"blob":"abcdef"}'
    )


@pytest.mark.asyncio
async def test_docker_runtime_executor_clips_timeout_to_remaining_deadline(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    fake_container = FakeContainer()
    docker_client = FakeDockerClient(fake_container)

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        task = make_task(invocation)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=tmp_path / "storage",
            clock=lambda: task.deadline_at - timedelta(seconds=2),
        ).execute(task)

    assert result.status == InvocationAttemptStatus.SUCCEEDED
    assert fake_container.wait_timeout == 2.0
    assert docker_client.containers.input_message["context"]["deadline_ms"] == 2_000


@pytest.mark.asyncio
async def test_docker_runtime_executor_skips_expired_deadline(
    test_sessionmaker: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    package_path = tmp_path / "function.zip"
    package_path.write_bytes(b"zip-content")
    docker_client = FakeDockerClient(FakeContainer())

    async with test_sessionmaker() as session:
        invocation = await create_invocation(session, package_path=package_path)
        task = make_task(invocation)
        result = await DockerRuntimeExecutor(
            session,
            docker_client=docker_client,
            workspace_root=tmp_path,
            storage_root=tmp_path / "storage",
            clock=lambda: task.deadline_at + timedelta(milliseconds=1),
        ).execute(task)

    assert result.status == InvocationAttemptStatus.TIMEOUT
    assert result.duration_ms == 0
    assert docker_client.containers.run_kwargs is None


async def create_invocation(
    session: AsyncSession,
    *,
    package_path: Path,
) -> Invocation:
    user = User(
        id=OWNER_ID,
        email=f"runtime-{uuid4().hex}@example.local",
        password_hash="development-only",
    )
    function = Function(owner_id=OWNER_ID, name="hello")
    session.add_all([user, function])
    await session.flush()

    version = FunctionVersion(
        function_id=function.id,
        version_number=1,
        runtime="python3.11",
        handler="main.handler",
        package_uri=package_path.as_posix(),
        package_hash="0123456789abcdef0123456789abcdef",
        memory_limit_mb=256,
        cpu_limit=0.5,
        timeout_seconds=30,
    )
    session.add(version)
    await session.flush()

    queued_at = datetime(2026, 7, 2, 10, 0, 0)
    invocation = Invocation(
        owner_id=OWNER_ID,
        function_version_id=version.id,
        status=InvocationStatus.QUEUED,
        payload_inline={"name": "Ada"},
        queued_at=queued_at,
        deadline_at=queued_at + timedelta(seconds=30),
        attempt_count=0,
    )
    session.add(invocation)
    await session.commit()
    await session.refresh(invocation)
    return invocation


def make_task(invocation: Invocation):
    return parse_xreadgroup_response(
        [
            (
                "invocations",
                [
                    (
                        "1710000000000-0",
                        {
                            "invocation_id": str(invocation.id),
                            "function_version_id": str(invocation.function_version_id),
                            "owner_id": str(invocation.owner_id),
                            "attempt_number": "1",
                            "queued_at": invocation.queued_at.isoformat(),
                            "deadline_at": invocation.deadline_at.isoformat(),
                        },
                    )
                ],
            )
        ]
    )[0]
