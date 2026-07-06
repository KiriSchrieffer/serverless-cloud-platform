#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
FUNCTION_NAME="${FUNCTION_NAME:-hello}"
WORK_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$WORK_DIR"
}
trap cleanup EXIT

cat >"$WORK_DIR/main.py" <<'PY'
def handler(event, context):
    name = event.get("name", "world")
    print(f"handling invocation {context.invocation_id}", flush=True)
    return {"message": f"hello {name}"}
PY

python3 - "$WORK_DIR" <<'PY'
import pathlib
import sys
import zipfile

work_dir = pathlib.Path(sys.argv[1])
with zipfile.ZipFile(work_dir / "function.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    archive.write(work_dir / "main.py", "main.py")
PY

curl -fsS -X POST "$API_URL/functions" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$FUNCTION_NAME\"}" >/dev/null || true

curl -fsS -X POST "$API_URL/functions/$FUNCTION_NAME/versions/upload" \
  -F "runtime=python3.11" \
  -F "handler=main.handler" \
  -F "memory_limit_mb=256" \
  -F "cpu_limit=0.5" \
  -F "timeout_seconds=30" \
  -F "package=@$WORK_DIR/function.zip;type=application/zip" >/dev/null

INVOKE_RESPONSE="$(curl -fsS -X POST "$API_URL/functions/$FUNCTION_NAME/invoke" \
  -H "Content-Type: application/json" \
  -d '{"payload":{"name":"Ada"},"idempotency_key":"demo-invoke"}')"

INVOCATION_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["invocation_id"])' \
  <<<"$INVOKE_RESPONSE")"

for _ in $(seq 1 30); do
  STATUS_RESPONSE="$(curl -fsS "$API_URL/invocations/$INVOCATION_ID")"
  STATUS="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' \
    <<<"$STATUS_RESPONSE")"
  printf "invocation %s status=%s\n" "$INVOCATION_ID" "$STATUS"
  case "$STATUS" in
    SUCCEEDED|FAILED|TIMEOUT|CANCELED)
      printf "%s\n" "$STATUS_RESPONSE"
      printf "\nlogs:\n"
      curl -fsS "$API_URL/invocations/$INVOCATION_ID/logs" || true
      printf "\n"
      exit 0
      ;;
  esac
  sleep 1
done

echo "Invocation did not reach a terminal state within 30 seconds" >&2
exit 1
