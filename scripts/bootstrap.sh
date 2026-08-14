#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v python3 >/dev/null || { echo "Python 3 is required." >&2; exit 1; }
[[ -f .env ]] || cp .env.example .env

if ! grep -Eq '^APP_SECRET_KEY=.+$' .env; then
  secret="$(python3 - <<'PY'
try:
    from cryptography.fernet import Fernet
except ImportError:
    import base64, os
    print(base64.urlsafe_b64encode(os.urandom(32)).decode())
else:
    print(Fernet.generate_key().decode())
PY
)"
  sed -i.bak "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$secret|" .env && rm -f .env.bak
fi

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements-dev.txt
echo "Bootstrap complete. Run: scripts/dev.sh"
