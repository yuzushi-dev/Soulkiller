"""Cron entrypoint: soulkiller:biofeedback-pull

Invoked as: python -m soulkiller.biofeedback
Schedule:   5 4 * * *

Nightly wearable data ingestion (Zepp/Amazfit): pulls HRV, sleep, and
activity data and converts them to biofeedback observations.
"""
from soulkiller.soulkiller_biofeedback import main

if __name__ == "__main__":
    main()
