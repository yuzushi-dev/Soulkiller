"""Cron entrypoint: soulkiller:liwc

Invoked as: python -m soulkiller.liwc
Schedule:   0 3 * * 0

Weekly LIWC analysis (Sunday): computes psycholinguistic word-category
metrics from the past week of inbox messages.
"""
from soulkiller.soulkiller_liwc import main

if __name__ == "__main__":
    main()
