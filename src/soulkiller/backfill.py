"""Cron entrypoint: soulkiller:backfill

Invoked as: python -m soulkiller.backfill
Schedule:   @manual

On-demand backfill: imports a JSONL file of past messages into
inbox.jsonl, de-duplicating by message_id.
"""
from soulkiller.soulkiller_backfill import main

if __name__ == "__main__":
    main()
