#!/usr/bin/env python3
"""Soulkiller Voicenote Transcriber

Scans media/inbound/ for .ogg and .mp3 files not yet transcribed,
transcribes them with faster-whisper (tiny model, Italian),
and inserts the text into the soulkiller inbox table so the
entity extractor can process them.

Cron: soulkiller:voicenote-transcribe, daily 04:30 Europe/Rome

State: soulkiller/voicenote-transcriber-state.json
  {"transcribed_files": ["file_24---UUID.ogg", ...]}
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn, error

SCRIPT = "soulkiller_voicenote_transcriber"
MEDIA_DIR = Path(__file__).resolve().parents[2] / "media" / "inbound"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
STATE_FILE = Path(__file__).resolve().parents[1] / "soulkiller" / "voicenote-transcriber-state.json"

WHISPER_MODEL = "tiny"
WHISPER_LANGUAGE = "it"
FROM_ID = "demo-subject"
CHANNEL_ID = "telegram"
AUDIO_EXTENSIONS = {".ogg", ".mp3", ".m4a", ".opus", ".wav"}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"transcribed_files": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def insert_transcription(db, filename: str, text: str, file_mtime: float) -> bool:
    """Insert transcription as inbox message. Returns True if inserted."""
    import sqlite3
    message_id = f"voice:{filename}"
    received_at = datetime.fromtimestamp(file_mtime, tz=timezone.utc).isoformat()
    content = f"[voicenote] {text}"
    try:
        db.execute(
            """INSERT OR IGNORE INTO inbox
               (message_id, from_id, content, channel_id, received_at)
               VALUES (?, ?, ?, ?, ?)""",
            (message_id, FROM_ID, content, CHANNEL_ID, received_at)
        )
        db.commit()
        return db.execute(
            "SELECT changes()"
        ).fetchone()[0] > 0
    except sqlite3.Error as e:
        warn(SCRIPT, "db_insert_error", filename=filename, error=str(e))
        return False


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def load_model():
    from faster_whisper import WhisperModel
    model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    return model


def transcribe(model, audio_path: Path) -> str | None:
    """Transcribe audio file. Returns text or None on failure."""
    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=WHISPER_LANGUAGE,
            beam_size=3,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if not text:
            warn(SCRIPT, "empty_transcription", file=audio_path.name,
                 lang=info.language, prob=round(info.language_probability, 2))
            return None
        return text
    except Exception as e:
        warn(SCRIPT, "transcription_error", file=audio_path.name, error=str(e))
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    state = load_state()
    already_done = set(state.get("transcribed_files", []))

    # Collect audio files sorted by mtime (oldest first)
    audio_files = sorted(
        [f for f in MEDIA_DIR.iterdir() if f.suffix.lower() in AUDIO_EXTENSIONS],
        key=lambda f: f.stat().st_mtime,
    )

    pending = [f for f in audio_files if f.name not in already_done]

    if not pending:
        info(SCRIPT, "nothing_to_transcribe", total_files=len(audio_files))
        return

    info(SCRIPT, "run_start", pending=len(pending), total=len(audio_files))

    if dry_run:
        for f in pending:
            print(f"  would transcribe: {f.name} ({f.stat().st_size // 1024}KB)")
        return

    db = get_db()
    model = load_model()
    transcribed_count = 0
    inserted_count = 0

    try:
        for audio_file in pending:
            info(SCRIPT, "transcribing", file=audio_file.name,
                 size_kb=audio_file.stat().st_size // 1024)

            text = transcribe(model, audio_file)
            if text is None:
                already_done.add(audio_file.name)  # skip on next run too
                continue

            transcribed_count += 1
            info(SCRIPT, "transcribed", file=audio_file.name,
                 chars=len(text), preview=text[:80])

            if insert_transcription(db, audio_file.name, text, audio_file.stat().st_mtime):
                inserted_count += 1

            already_done.add(audio_file.name)
            save_state({"transcribed_files": sorted(already_done)})

    finally:
        db.close()

    info(SCRIPT, "run_complete",
         transcribed=transcribed_count,
         inserted_to_inbox=inserted_count)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Soulkiller Voicenote Transcriber")
    parser.add_argument("--dry-run", action="store_true",
                        help="List files that would be transcribed without processing")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
