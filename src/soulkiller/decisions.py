"""Cron entrypoint: soulkiller:decisions

Invoked as: python -m soulkiller.decisions
Schedule:   15 4 * * *

Daily decision extraction: detects explicit choices in messages and
stores them with linked personality facets for coherence analysis.
"""
from soulkiller.soulkiller_decisions import main

if __name__ == "__main__":
    main()
