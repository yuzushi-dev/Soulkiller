"""Cron entrypoint: soulkiller:checkin

Invoked as: python -m soulkiller.checkin
Schedule:   */30 9-22 * * *   (every 30 min, active hours)

Scores all 60 facets, selects the highest gap-score facet, generates a
natural-language probe question, and delivers it via the configured channel.
"""
from soulkiller.soulkiller_question_engine import main

if __name__ == "__main__":
    main()
