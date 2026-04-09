"""Cron entrypoint: soulkiller:passive-scan

Invoked as: python -m soulkiller.passive_scan
Schedule:   0 */6 * * *   (every 6 hours)

Scans relational-agent session transcripts for behavioral meta-signals
and inserts observations into the SQLite database.
Requires SOULKILLER_RELATIONAL_AGENT to be set.
"""
from soulkiller.soulkiller_passive_observer import main

if __name__ == "__main__":
    main()
