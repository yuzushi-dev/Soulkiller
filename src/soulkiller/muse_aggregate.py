"""Cron entrypoint: soulkiller:muse-aggregate

Invoked as: python -m soulkiller.muse_aggregate
Schedule:   30 4 * * *

Daily Muse 2 EEG session aggregation: computes focus, calm, frontal
asymmetry, and engagement metrics from completed EEG sessions.
"""
from soulkiller.soulkiller_muse_aggregator import main

if __name__ == "__main__":
    main()
