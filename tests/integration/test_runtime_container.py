"""Smoke test for the real Python runtime image protocol."""

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.getenv("RUN_DOCKER_TESTS") != "1",
        reason="set RUN_DOCKER_TESTS=1 with Docker and the runtime image available",
    ),
]


def test_runtime_image_executes_handler_without_corrupting_stdout(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def handler(event, context):\n"
        "    print('runtime log line', flush=True)\n"
        "    return {'message': 'hello ' + event['name']}\n",
        encoding="utf-8",
    )
    request = {
        "event": {"name": "Ada"},
        "context": {
            "invocation_id": "runtime-smoke",
            "function_name": "hello",
            "function_version": "1",
            "deadline_ms": 30000,
            "memory_limit_mb": 256,
        },
    }

    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "-e",
            "HANDLER=main.handler",
            "-e",
            "PYTHONPATH=/var/task",
            "-v",
            f"{tmp_path}:/var/task:ro",
            "serverless-python311-runtime:latest",
        ],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "ok": True,
        "result": {"message": "hello Ada"},
    }
    assert "runtime log line" in completed.stderr
