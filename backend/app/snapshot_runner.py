"""Generate idempotent monthly analytics snapshots.

Examples::

    python -m backend.app.snapshot_runner
    python -m backend.app.snapshot_runner --month 2026-08
    python -m backend.app.snapshot_runner --backfill 12
    python -m backend.app.snapshot_runner --backfill 12 --force

Without ``--force`` existing snapshots are left untouched. ``--force`` is the
explicit correction mechanism and marks the replaced row as ``corrected``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from backend.app.database import SessionLocal
from backend.app.services.analytics import capture_snapshot


def _month(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date().replace(day=1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM") from exc


def _previous_month(value: date) -> date:
    return date(value.year - (value.month == 1), 12 if value.month == 1 else value.month - 1, 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Brightloop monthly analytics snapshots")
    parser.add_argument("--month", type=_month, help="Month to capture (YYYY-MM). Defaults to the current month.")
    parser.add_argument("--backfill", type=int, default=0, metavar="N", help="Capture the current month and N-1 previous months.")
    parser.add_argument("--force", action="store_true", help="Explicitly overwrite an existing snapshot (marks it corrected).")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    current = args.month or now.date().replace(day=1)
    months: list[date] = []
    if args.backfill > 0:
        cursor = current
        for _ in range(min(args.backfill, 120)):
            months.append(cursor)
            cursor = _previous_month(cursor)
    else:
        months.append(current)

    created = 0
    with SessionLocal() as db:
        for month in months:
            snapshot, was_created = capture_snapshot(db, month, overwrite=args.force, now=now)
            created += int(was_created)
            outcome = "created" if was_created else ("corrected" if args.force else "kept")
            print(
                f"{snapshot.snapshot_month:%Y-%m}: {snapshot.source} "
                f"({outcome})"
            )
    print(f"Captured {created} new snapshot(s).")


if __name__ == "__main__":
    main()
