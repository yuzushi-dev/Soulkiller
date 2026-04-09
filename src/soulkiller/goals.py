"""Cron entrypoint: soulkiller:goals

Invoked as: python -m soulkiller.goals
Schedule:   30 5 1 * *

Monthly goal architecture extraction: identifies current concerns,
personal projects, and goal-linked personality facets.
"""
from soulkiller.soulkiller_goals import main

if __name__ == "__main__":
    main()
