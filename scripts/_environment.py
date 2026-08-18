"""Shared environment-file helpers for the Python operational scripts."""

from __future__ import annotations

import base64
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    """Load simple KEY=VALUE entries without overwriting the process environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def ensure_env(path: Path = ROOT / ".env") -> Path:
    """Create an env file if needed and ensure it contains a valid Fernet key."""
    if not path.exists():
        path.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    key_index = next((i for i, line in enumerate(lines) if line.startswith("APP_SECRET_KEY=")), None)
    current = lines[key_index].split("=", 1)[1].strip() if key_index is not None else ""
    if current:
        try:
            decoded = base64.urlsafe_b64decode(current.encode())
        except Exception as exc:
            raise SystemExit(f"APP_SECRET_KEY is not valid URL-safe base64: {exc}") from exc
        if len(decoded) != 32:
            raise SystemExit("APP_SECRET_KEY must decode to exactly 32 bytes")
    else:
        current = base64.urlsafe_b64encode(os.urandom(32)).decode()
        entry = f"APP_SECRET_KEY={current}"
        if key_index is None:
            lines.append(entry)
        else:
            lines[key_index] = entry
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"Generated APP_SECRET_KEY in {path}")
    path.chmod(0o600)
    return path
