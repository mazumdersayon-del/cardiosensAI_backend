"""Erase accounts whose 30-day deletion grace period is over (admin deletions; see services/erasure.py).

    python -m app.scripts.purge_deleted            # erase what is due
    python -m app.scripts.purge_deleted --dry-run  # only list it

On the server `deploy.sh` runs this daily (cardiosensai-purge.timer). Safe to run any time.
"""

import argparse

from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.storage import get_storage
from app.models import User
from app.services.erasure import purge_due


def main() -> int:
    p = argparse.ArgumentParser(description="Erase accounts whose deletion grace period is over")
    p.add_argument("--dry-run", action="store_true", help="only list the accounts that are due")
    args = p.parse_args()
    with SessionLocal() as db:
        if args.dry_run:
            due = db.scalars(select(User).where(User.status == "deleting", User.erase_after <= utcnow()))
            ids = [u.unique_id for u in due]
            print(f"{len(ids)} account(s) due: {', '.join(i or '?' for i in ids) or '-'}")
            return 0
        n, keys = purge_due(db)
        db.commit()
    storage = get_storage()
    for k in keys:
        storage.delete(k)
    print(f"Erased {n} account(s), removed {len(keys)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
