"""Cron entrypoint: soulkiller:sdt

Invoked as: python -m soulkiller.sdt
Schedule:   0 6 1 * *

Monthly Self-Determination Theory (SDT) assessment: estimates
autonomous vs controlled motivation and basic need satisfaction.
"""
from soulkiller.soulkiller_sdt import main

if __name__ == "__main__":
    main()
