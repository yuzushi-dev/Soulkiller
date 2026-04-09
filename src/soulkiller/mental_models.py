"""Cron entrypoint: soulkiller:mental-models

Invoked as: python -m soulkiller.mental_models
Schedule:   0 5 5 * *

Monthly mental model extraction: identifies stable conceptual
frameworks and reasoning heuristics the subject applies repeatedly.
"""
from soulkiller.soulkiller_mental_models import main

if __name__ == "__main__":
    main()
