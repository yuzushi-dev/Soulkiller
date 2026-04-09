#!/usr/bin/env python3
"""Auto-ingest Gadgetbridge DB synced via Syncthing.

Reads Gadgetbridge.db from the Syncthing-synced folder, skips if the file
hasn't changed since last run (checked via mtime), runs the GB biofeedback
parser for today (or --date).

Usage:
  python3 soulkiller_biofeedback_gb_ingest.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lib.log import info, warn, error

SCRIPT     = "soulkiller_biofeedback_gb_ingest"
SYNC_DB    = Path(__file__).resolve().parents[2] / "media" / "gadgetbridge_sync" / "Gadgetbridge.db"
STATE_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "gb_last_mtime.txt"


def already_processed() -> bool:
    if not STATE_PATH.exists() or not SYNC_DB.exists():
        return False
    return STATE_PATH.read_text().strip() == str(int(SYNC_DB.stat().st_mtime))


def mark_processed() -> None:
    STATE_PATH.write_text(str(int(SYNC_DB.stat().st_mtime)))


def run(local_date: str, dry_run: bool = False) -> None:
    if not SYNC_DB.exists():
        warn(SCRIPT, f"Gadgetbridge.db not found at {SYNC_DB} — Syncthing not synced yet")
        return

    if already_processed():
        info(SCRIPT, "Gadgetbridge.db unchanged since last run — skipping")
        return

    info(SCRIPT, f"processing {SYNC_DB} for date {local_date}")

    from soulkiller_biofeedback_gadgetbridge import run as gb_run
    try:
        gb_run(str(SYNC_DB), local_date, dry_run=dry_run)
    except Exception as exc:
        error(SCRIPT, f"GB ingestion failed: {exc}")
        return

    if not dry_run:
        mark_processed()
        info(SCRIPT, "marked as processed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-ingest Gadgetbridge DB from Syncthing folder"
    )
    parser.add_argument("--date", default=None,
                        help="Local Italy date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    local_date = args.date or date.today().isoformat()
    run(local_date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
