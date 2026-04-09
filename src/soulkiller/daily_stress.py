"""Cron entrypoint: soulkiller:daily-stress

Invoked as: python -m soulkiller.daily_stress
Schedule:   30 6 * * *   (daily at 06:30, after biofeedback pull)

Composite daily stress index from physiological signals (HRV, RHR,
sleep score, stress_avg) and behavioural message volume baseline.
Alerts via Telegram if stress_index >= 0.65.
"""
from soulkiller.soulkiller_daily_stress import main

if __name__ == "__main__":
    main()
