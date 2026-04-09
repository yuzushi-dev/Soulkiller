"""Cron entrypoint: soulkiller:memory

Invoked as: python -m soulkiller.memory
Schedule:   0 5 * * 0

Weekly memory consolidation (Sunday): computes communication metrics
from the message corpus and stores them in the memory layer.
"""
from soulkiller.soulkiller_memory import main

if __name__ == "__main__":
    main()
