"""Cron entrypoint: soulkiller:voicenote

Invoked as: python -m soulkiller.voicenote
Schedule:   @manual

On-demand voice note transcription: transcribes an audio file and
appends the transcript to inbox.jsonl for subsequent extraction.
"""
from soulkiller.soulkiller_voicenote_transcriber import main

if __name__ == "__main__":
    main()
