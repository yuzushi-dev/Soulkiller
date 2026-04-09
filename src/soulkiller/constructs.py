"""Cron entrypoint: soulkiller:constructs

Invoked as: python -m soulkiller.constructs
Schedule:   0 6 5 * *

Monthly personal constructs analysis: elicits bipolar evaluative
dimensions using Kelly's Personal Construct Theory framework.
"""
from soulkiller.soulkiller_constructs import main

if __name__ == "__main__":
    main()
