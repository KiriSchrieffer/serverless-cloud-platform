"""Black-box API-to-Docker workflows against the complete Compose stack."""

import os
import subprocess
import time
from dataclasses import dataclass, field
from io import BytesIO
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "TIMEOUT", "CANCELED"}

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_E2E_TESTS") != "1",
        reason="set RUN_E2E_TESTS=1 with the full Docker Compose stack running",
    ),
]


@dataclass
class ComposeApi:
    client: httpx.Client
    invocation_ids: list[str] = field(default_factory=list)

    def deploy(
        self,
        *,
        source: str,
        timeout_seconds: int = 10,
    ) -> str:
        function_name = f"e2e-{uuid4().hex[:12]}"
        create_response = self.client.post("/functions", json={"name": function_name})
        assert create_response.status_code == 201, create_response.text

        upload_response = self.client.post(
            f"/functions/{function_name}/versions/upload",
            data={
                "runtime": "python3.11",
                "handler": "main.handler",
                "memory_limit_mb": "256",
                "cpu_limit": "0.5",
                "timeout_seconds": str(timeout_seconds),
            },
            files={
                "package": (
                    "function.zip",
                    build_package(source),
                    "application/zip",
                )
            },
        )
        assert upload_response.status_code == 201, upload_response.text
        return function_name

    def invoke(self, function_name: str, payload: object) -> dict[str, object]:
        invocation_id = self.submit(function_name, payload)
        return self.wait_for_terminal(invocation_id)

    def submit(self, function_name: str, payload: object) -> str:
        response = self.client.post(
            f"/functions/{function_name}/invoke",
            json={
                "payload": payload,
                "idempotency_key": f"e2e-{uuid4().hex}",
            },
        )
        assert response.status_code == 202, response.text
        invocation_id = response.json()["invocation_id"]
        self.invocation_ids.append(invocation_id)
        return invocation_id

    def wait_for_status(
        self,
        invocation_id: str,
        expected_status: str,
        *,
        timeout_seconds: float = 15,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_response: dict[str, object] | None = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/invocations/{invocation_id}")
            assert response.status_code == 200, response.text
            last_response = response.json()
            if last_response["status"] == expected_status:
                return last_response
            if last_response["status"] in TERMINAL_STATUSES:
                pytest.fail(
                    f"invocation {invocation_id} reached {last_response['status']} "
                    f"before {expected_status}; response={last_response}"
                )
            time.sleep(0.1)
        pytest.fail(
            f"invocation {invocation_id} did not reach {expected_status} within "
            f"{timeout_seconds}s; last response={last_response}"
        )

    def wait_for_terminal(
        self,
        invocation_id: str,
        *,
        timeout_seconds: float = 45,
    ) -> dict[str, object]:
        deadline = time.monotonic() + timeout_seconds
        last_response: dict[str, object] | None = None
        while time.monotonic() < deadline:
            response = self.client.get(f"/invocations/{invocation_id}")
            assert response.status_code == 200, response.text
            last_response = response.json()
            if last_response["status"] in TERMINAL_STATUSES:
                return last_response
            time.sleep(0.25)
        pytest.fail(
            f"invocation {invocation_id} did not finish within {timeout_seconds}s; "
            f"last response={last_response}"
        )

    def logs(self, invocation_id: str) -> str:
        response = self.client.get(f"/invocations/{invocation_id}/logs")
        assert response.status_code == 200, response.text
        return response.text


@pytest.fixture(scope="module")
def compose_api() -> ComposeApi:
    wait_for_url("http://localhost:8000/healthz")
    wait_for_url("http://localhost:3000/")

    email = f"e2e-{uuid4().hex}@example.local"
    password = "e2e-local-password"
    with httpx.Client(base_url="http://localhost:8000", timeout=15) as client:
        register_response = client.post(
            "/auth/register",
            json={"email": email, "password": password},
        )
        assert register_response.status_code == 201, register_response.text
        login_response = client.post(
            "/auth/login",
            json={"email": email, "password": password},
        )
        assert login_response.status_code == 200, login_response.text
        token = login_response.json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        wait_for_worker(client)
        yield ComposeApi(client)


def test_success_result_and_logs_round_trip(compose_api: ComposeApi) -> None:
    function_name = compose_api.deploy(
        source=(
            "def handler(event, context):\n"
            "    print(f\"handling {context.invocation_id}\", flush=True)\n"
            "    return {'message': 'hello ' + event['name']}\n"
        )
    )

    invocation = compose_api.invoke(function_name, {"name": "Ada"})

    assert invocation["status"] == "SUCCEEDED"
    assert invocation["result_inline"] == {"message": "hello Ada"}
    assert invocation["error_type"] is None
    assert invocation["attempt_count"] == 1
    assert "handling" in compose_api.logs(str(invocation["id"]))


def test_handler_error_is_durable_and_logs_are_retrievable(compose_api: ComposeApi) -> None:
    function_name = compose_api.deploy(
        source=(
            "def handler(event, context):\n"
            "    print('before expected failure', flush=True)\n"
            "    raise ValueError('expected handler failure')\n"
        )
    )

    invocation = compose_api.invoke(function_name, {})

    assert invocation["status"] == "FAILED"
    assert invocation["error_type"] == "ValueError"
    assert invocation["error_message"] == "expected handler failure"
    assert invocation["attempt_count"] == 1
    logs = compose_api.logs(str(invocation["id"]))
    assert "before expected failure" in logs
    assert "ValueError: expected handler failure" in logs


def test_timeout_kills_runtime_and_marks_invocation(compose_api: ComposeApi) -> None:
    function_name = compose_api.deploy(
        source=(
            "import time\n"
            "def handler(event, context):\n"
            "    print('before timeout', flush=True)\n"
            "    time.sleep(10)\n"
            "    return {'unexpected': True}\n"
        ),
        timeout_seconds=3,
    )

    invocation = compose_api.invoke(function_name, {})

    assert invocation["status"] == "TIMEOUT", invocation
    assert invocation["error_type"] == "TimeoutError", invocation
    assert invocation["attempt_count"] == 1, invocation


def test_empty_runtime_output_is_rejected(compose_api: ComposeApi) -> None:
    function_name = compose_api.deploy(
        source=(
            "import os\n"
            "def handler(event, context):\n"
            "    os._exit(0)\n"
        )
    )

    invocation = compose_api.invoke(function_name, {})

    assert invocation["status"] == "FAILED"
    assert invocation["error_type"] == "InvalidRuntimeOutput"
    assert "no JSON result" in str(invocation["error_message"])
    assert invocation["attempt_count"] == 1
    assert compose_api.logs(str(invocation["id"])) == ""


def test_worker_process_crash_recovers_pending_invocation(compose_api: ComposeApi) -> None:
    function_name = compose_api.deploy(
        source=(
            "import time\n"
            "def handler(event, context):\n"
            "    print('worker-crash-recovery-attempt', flush=True)\n"
            "    time.sleep(3)\n"
            "    return {'recovered': True}\n"
        ),
        timeout_seconds=40,
    )
    invocation_id = compose_api.submit(function_name, {})
    compose_api.wait_for_status(invocation_id, "RUNNING")

    run_compose("kill", "worker")
    run_compose("up", "--detach", "worker")

    invocation = compose_api.wait_for_terminal(invocation_id, timeout_seconds=75)

    assert invocation["status"] == "SUCCEEDED"
    assert invocation["result_inline"] == {"recovered": True}
    assert invocation["attempt_count"] == 2
    assert invocation["error_type"] is None
    assert "worker-crash-recovery-attempt" in compose_api.logs(invocation_id)


def test_metrics_reflect_completed_e2e_workflows(compose_api: ComposeApi) -> None:
    response = compose_api.client.get("/metrics/summary")

    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["invocations"]["total"] == len(compose_api.invocation_ids)
    assert summary["invocations"]["terminal"] == len(compose_api.invocation_ids)
    assert summary["invocations"]["succeeded"] == 2
    assert summary["invocations"]["failed"] == 2, summary
    assert summary["invocations"]["timeout"] == 1, summary
    assert summary["invocations"]["p50_latency_ms"] is not None
    assert summary["queue"]["depth"] == 0
    assert summary["queue"]["pending_dispatches"] == 0


def build_package(source: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("main.py", source)
    return buffer.getvalue()


def run_compose(*args: str, timeout_seconds: float = 60) -> None:
    completed = subprocess.run(
        ["docker", "compose", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    assert completed.returncode == 0, (
        f"docker compose {' '.join(args)} failed with exit code "
        f"{completed.returncode}\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def wait_for_url(url: str, *, timeout_seconds: float = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(1)
    pytest.fail(f"{url} did not become ready within {timeout_seconds}s: {last_error}")


def wait_for_worker(client: httpx.Client, *, timeout_seconds: float = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_workers: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        response = client.get("/workers")
        if response.status_code == 200:
            last_workers = response.json()
            if any(not worker["stale"] and worker["status"] != "OFFLINE" for worker in last_workers):
                return
        time.sleep(1)
    pytest.fail(f"worker did not register within {timeout_seconds}s: {last_workers}")
