"""Cron entrypoint: soulkiller:attachment

Invoked as: python -m soulkiller.attachment
Schedule:   0 5 3 * *

Monthly attachment analysis: infers attachment style from relational
behavior patterns and entity interaction data.
"""
from soulkiller.soulkiller_attachment import main

if __name__ == "__main__":
    main()
