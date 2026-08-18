#!/usr/bin/env python3
"""Prepare and validate the environment used by Docker Compose."""

import os
from pathlib import Path

from _environment import ROOT, ensure_env


if __name__ == "__main__":
    ensure_env(Path(os.getenv("ENV_FILE", ROOT / ".env")))
