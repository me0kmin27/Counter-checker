#!/usr/bin/env python3
"""Run the FastAPI development server with local environment settings."""

import os

import uvicorn

from _environment import ROOT, load_env


if __name__ == "__main__":
    env_file = ROOT / ".env"
    if not env_file.exists():
        raise SystemExit("Missing .env; run `make bootstrap` first.")
    load_env(env_file)
    os.environ.setdefault("DATABASE_URL", "sqlite:///./counter_checker.db")
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.getenv("WEB_PORT", "8000")), reload=True)
