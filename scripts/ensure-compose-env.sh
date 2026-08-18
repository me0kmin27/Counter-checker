#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
EXAMPLE_FILE="$ROOT_DIR/.env.example"

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$EXAMPLE_FILE" "$ENV_FILE"
fi

current_key="$(sed -n 's/^APP_SECRET_KEY=//p' "$ENV_FILE" | tail -n 1)"
if [[ -z "$current_key" ]]; then
  generated_key="$(python3 - <<'PY'
import base64
import os

print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)"
  if grep -q '^APP_SECRET_KEY=' "$ENV_FILE"; then
    sed -i.bak "s|^APP_SECRET_KEY=.*$|APP_SECRET_KEY=$generated_key|" "$ENV_FILE"
    rm -f "$ENV_FILE.bak"
  else
    printf '\nAPP_SECRET_KEY=%s\n' "$generated_key" >> "$ENV_FILE"
  fi
  echo "Generated APP_SECRET_KEY in $ENV_FILE"
else
  APP_SECRET_KEY="$current_key" python3 - <<'PY'
import base64
import os

try:
    decoded = base64.urlsafe_b64decode(os.environ["APP_SECRET_KEY"].encode())
except Exception as exc:
    raise SystemExit(f"APP_SECRET_KEY is not valid URL-safe base64: {exc}")
if len(decoded) != 32:
    raise SystemExit("APP_SECRET_KEY must decode to exactly 32 bytes")
PY
fi

chmod 600 "$ENV_FILE"
