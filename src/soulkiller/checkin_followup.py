"""Cron entrypoint: soulkiller:checkin-followup

Invoked as: python -m soulkiller.checkin_followup
Schedule:   on-demand (triggered by the soulkiller-capture hook when a
            check-in reply is detected in inbox.jsonl)

Reads the pending-checkin.json signal file, acknowledges the reply,
and records the exchange in the database.
"""
from soulkiller.soulkiller_daily_stress import main

if __name__ == "__main__":
    main()
