"""Cron entrypoint: soulkiller:budget-bridge

Invoked as: python -m soulkiller.budget_bridge
Schedule:   20 4 * * *

Nightly Actual Budget bridge: imports financial transaction summaries
and converts them to lifestyle and stress-related observations.
"""
from soulkiller.soulkiller_budget_bridge import main

if __name__ == "__main__":
    main()
