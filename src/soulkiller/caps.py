"""Cron entrypoint: soulkiller:caps

Invoked as: python -m soulkiller.caps
Schedule:   30 5 2 * *

Monthly CAPS synthesis: extracts if-then behavioral signatures
from session and message data using the CAPS personality framework.
"""
from soulkiller.soulkiller_caps import main

if __name__ == "__main__":
    main()
