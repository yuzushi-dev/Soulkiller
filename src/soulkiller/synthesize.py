"""Cron entrypoint: soulkiller:synthesize

Invoked as: python -m soulkiller.synthesize
Schedule:   0 3 * * *   (daily at 03:00)

Consolidates all accumulated observations into weighted trait scores,
updates confidence levels, and generates cross-facet hypotheses via LLM.
Saves a model snapshot for longitudinal tracking.
"""
from soulkiller.soulkiller_synthesizer import main

if __name__ == "__main__":
    main()
