import importlib
import json
import os
import sys
import traceback
from contextlib import redirect_stdout
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeContext:
    invocation_id: str
    function_name: str
    function_version: str
    deadline_ms: int
    memory_limit_mb: int


def load_handler(handler_path: str) -> Any:
    module_name, function_name = handler_path.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def main() -> None:
    handler_path = os.environ.get("HANDLER", "main.handler")
    exit_code = 0

    try:
        message = json.load(sys.stdin)
        event = message.get("event", {})
        context = RuntimeContext(**message.get("context", {}))
        handler = load_handler(handler_path)
        with redirect_stdout(sys.stderr):
            result = handler(event, context)
        output = json.dumps({"ok": True, "result": result})
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        output = json.dumps(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            }
        )
        exit_code = 1

    sys.stdout.write(output)
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
