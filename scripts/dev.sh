#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -x .venv/bin/uvicorn ]] || { echo "Run scripts/bootstrap.sh first." >&2; exit 1; }
[[ -f .env ]] || { echo "Missing .env; run scripts/bootstrap.sh first." >&2; exit 1; }

set -a
# shellcheck disable=SC1091
. ./.env
set +a
export DATABASE_URL="${DATABASE_URL:-sqlite:///./counter_checker.db}"
exec .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port "${WEB_PORT:-8000}"
