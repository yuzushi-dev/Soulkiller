"""Cron entrypoint: soulkiller:motives

Invoked as: python -m soulkiller.motives
Schedule:   @manual

On-demand motive analysis: infers implicit and explicit motivational
patterns from accumulated observations and goal data.
"""
from soulkiller.soulkiller_motives import main

if __name__ == "__main__":
    main()
