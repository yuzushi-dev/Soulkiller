"""Cron entrypoint: soulkiller:profile-sync

Invoked as: python -m soulkiller.profile_sync
Schedule:   30 3 * * *   (daily at 03:30, after synthesize)

Syncs the current trait model to subject_profile.json and regenerates
the human-readable PORTRAIT.md that is injected into agent sessions
by the soulkiller-bootstrap hook.
"""
from soulkiller.soulkiller_profile_bridge import main

if __name__ == "__main__":
    main()
