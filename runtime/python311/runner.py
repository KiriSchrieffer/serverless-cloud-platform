import importlib
import json
import os
import sys
import traceback
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

    try:
        message = json.load(sys.stdin)
        event = message.get("event", {})
        context = RuntimeContext(**message.get("context", {}))
        handler = load_handler(handler_path)
        result = handler(event, context)
        json.dump({"ok": True, "result": result}, sys.stdout)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        json.dump(
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
            sys.stdout,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
