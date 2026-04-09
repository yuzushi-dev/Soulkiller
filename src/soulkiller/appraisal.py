"""Cron entrypoint: soulkiller:appraisal

Invoked as: python -m soulkiller.appraisal
Schedule:   30 4 5 * *

Monthly appraisal analysis: infers characteristic cognitive appraisal
patterns (primary/secondary) using Lazarus appraisal theory.
"""
from soulkiller.soulkiller_appraisal import main

if __name__ == "__main__":
    main()
