#!/usr/bin/env python3
"""Create the local Python environment and install development dependencies."""

import subprocess
import sys
import venv

from _environment import ROOT, ensure_env


def main() -> None:
    ensure_env()
    environment = ROOT / ".venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / "bin" / "python"
    subprocess.run([python, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([python, "-m", "pip", "install", "-r", ROOT / "requirements-dev.txt"], check=True)
    print("Bootstrap complete. Run: make dev")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
