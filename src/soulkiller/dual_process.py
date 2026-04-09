"""Cron entrypoint: soulkiller:dual-process

Invoked as: python -m soulkiller.dual_process
Schedule:   30 5 5 * *

Monthly dual-process analysis: estimates the subject's balance
between System 1 (intuitive) and System 2 (deliberative) cognition.
"""
from soulkiller.soulkiller_dual_process import main

if __name__ == "__main__":
    main()
