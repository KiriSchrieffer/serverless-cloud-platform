#!/usr/bin/env bash
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
FUNCTION_NAME="${FUNCTION_NAME:-hello}"
DEMO_EMAIL="${DEMO_EMAIL:-demo@example.local}"
DEMO_PASSWORD="${DEMO_PASSWORD:-local-demo-password}"
DEMO_IDEMPOTENCY_KEY="${DEMO_IDEMPOTENCY_KEY:-demo-invoke-$(date +%s)}"
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

curl -fsS -X POST "$API_URL/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\"}" \
  >/dev/null || true

LOGIN_RESPONSE="$(curl -fsS -X POST "$API_URL/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$DEMO_EMAIL\",\"password\":\"$DEMO_PASSWORD\"}")"
ACCESS_TOKEN="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' \
  <<<"$LOGIN_RESPONSE")"
AUTH_HEADER="Authorization: Bearer $ACCESS_TOKEN"

curl -fsS -X POST "$API_URL/functions" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"$FUNCTION_NAME\"}" >/dev/null || true

curl -fsS -X POST "$API_URL/functions/$FUNCTION_NAME/versions/upload" \
  -H "$AUTH_HEADER" \
  -F "runtime=python3.11" \
  -F "handler=main.handler" \
  -F "memory_limit_mb=256" \
  -F "cpu_limit=0.5" \
  -F "timeout_seconds=30" \
  -F "package=@$WORK_DIR/function.zip;type=application/zip" >/dev/null

INVOKE_RESPONSE="$(curl -fsS -X POST "$API_URL/functions/$FUNCTION_NAME/invoke" \
  -H "$AUTH_HEADER" \
  -H "Content-Type: application/json" \
  -d "{\"payload\":{\"name\":\"Ada\"},\"idempotency_key\":\"$DEMO_IDEMPOTENCY_KEY\"}")"

INVOCATION_ID="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["invocation_id"])' \
  <<<"$INVOKE_RESPONSE")"

for _ in $(seq 1 30); do
  STATUS_RESPONSE="$(curl -fsS -H "$AUTH_HEADER" "$API_URL/invocations/$INVOCATION_ID")"
  STATUS="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])' \
    <<<"$STATUS_RESPONSE")"
  printf "invocation %s status=%s\n" "$INVOCATION_ID" "$STATUS"
  case "$STATUS" in
    SUCCEEDED|FAILED|TIMEOUT|CANCELED)
      printf "%s\n" "$STATUS_RESPONSE"
      printf "\nlogs:\n"
      curl -fsS -H "$AUTH_HEADER" "$API_URL/invocations/$INVOCATION_ID/logs" || true
      printf "\n"
      exit 0
      ;;
  esac
  sleep 1
done

echo "Invocation did not reach a terminal state within 30 seconds" >&2
exit 1
