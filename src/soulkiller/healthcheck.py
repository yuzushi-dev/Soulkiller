"""Cron entrypoint: soulkiller:healthcheck

Invoked as: python -m soulkiller.healthcheck
Schedule:   0 4 * * *

System health check: verifies database integrity, checks cron freshness,
and reports anomalies to the operator log.
"""
from soulkiller.soulkiller_healthcheck import main

if __name__ == "__main__":
    main()
