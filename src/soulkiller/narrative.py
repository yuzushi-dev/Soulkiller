"""Cron entrypoint: soulkiller:narrative

Invoked as: python -m soulkiller.narrative
Schedule:   0 6 3 * *

Monthly narrative identity analysis: extracts the subject's self-story
structure, redemption sequences, and contamination sequences.
"""
from soulkiller.soulkiller_narrative import main

if __name__ == "__main__":
    main()
