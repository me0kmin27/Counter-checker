#!/usr/bin/env python3
"""Fetch mail once for every enabled POP account."""

import sys

from sqlalchemy import select

from _environment import ROOT, load_env

load_env(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import PopAccount  # noqa: E402
from app.pop_service import fetch_account  # noqa: E402


def main() -> int:
    Base.metadata.create_all(engine)
    failed = False
    with SessionLocal() as db:
        accounts = db.scalars(select(PopAccount).where(PopAccount.enabled.is_(True))).all()
        for account in accounts:
            try:
                count = fetch_account(db, account)
                print(f"{account.name}: {count} new")
            except Exception as exc:
                failed = True
                print(f"account #{account.id}: {exc}", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
