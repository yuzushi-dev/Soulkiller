"""Cron entrypoint: soulkiller:extract

Invoked as: python -m soulkiller.extract
Schedule:   0 */2 * * *   (every 2 hours)

Reads new messages from inbox.jsonl, calls LLM to extract personality
signals, and inserts observations into the SQLite database.
"""
from soulkiller.soulkiller_extractor import main

if __name__ == "__main__":
    main()
