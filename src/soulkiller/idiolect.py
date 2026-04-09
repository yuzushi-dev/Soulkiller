"""Cron entrypoint: soulkiller:idiolect

Invoked as: python -m soulkiller.idiolect
Schedule:   0 4 1 * *

Monthly idiolect fingerprint: computes stable linguistic features
(vocabulary richness, syntactic patterns, formulaic expressions).
"""
from soulkiller.soulkiller_idiolect import main

if __name__ == "__main__":
    main()
