"""Cron entrypoint: soulkiller:stress-index

Invoked as: python -m soulkiller.stress_index
Schedule:   0 6 * * 1

Weekly stress index (Monday): computes a composite daily stress score
from physiological and behavioral signals using sigmoid normalization.
"""
from soulkiller.soulkiller_stress_index import main

if __name__ == "__main__":
    main()
