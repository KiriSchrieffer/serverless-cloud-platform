import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = PROJECT_ROOT / "runtime" / "python311" / "runner.py"


def test_runtime_runner_routes_handler_stdout_to_stderr(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def handler(event, context):\n"
        "    print(f'handling {context.invocation_id}', flush=True)\n"
        "    return {'message': f\"hello {event['name']}\"}\n",
        encoding="utf-8",
    )

    result = run_handler(tmp_path, payload={"name": "Ada"})

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "result": {"message": "hello Ada"},
    }
    assert result.stderr == "handling invocation-123\n"


def test_runtime_runner_loads_handler_from_deployment_zip(tmp_path: Path) -> None:
    package_path = tmp_path / "function.zip"
    with ZipFile(package_path, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "main.py",
            "def handler(event, context):\n"
            "    return {'message': 'hello ' + event['name']}\n",
        )

    result = run_handler(package_path, payload={"name": "Ada"})

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "ok": True,
        "result": {"message": "hello Ada"},
    }


def test_runtime_runner_returns_clean_error_for_non_json_result(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "def handler(event, context):\n"
        "    print('before serialization')\n"
        "    return {1, 2, 3}\n",
        encoding="utf-8",
    )

    result = run_handler(tmp_path, payload={})

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "ok": False,
        "error_type": "TypeError",
        "error_message": "Object of type set is not JSON serializable",
    }
    assert "before serialization" in result.stderr
    assert "TypeError" in result.stderr


def run_handler(python_path: Path, *, payload: object) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update({"HANDLER": "main.handler", "PYTHONPATH": str(python_path)})
    message = {
        "event": payload,
        "context": {
            "invocation_id": "invocation-123",
            "function_name": "hello",
            "function_version": "1",
            "deadline_ms": 30_000,
            "memory_limit_mb": 256,
        },
    }
    return subprocess.run(
        [sys.executable, str(RUNNER_PATH)],
        input=json.dumps(message),
        text=True,
        capture_output=True,
        check=False,
        env=environment,
        cwd=PROJECT_ROOT,
    )
