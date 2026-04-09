#!/usr/bin/env python3
"""Soulkiller Muse 2 EEG Aggregator — Sprint 3.

Reads completed EEG sessions from the DB (stored by soulkiller_muse_recorder),
computes daily aggregates, and writes them into biofeedback_readings so the
main biofeedback pipeline can derive personality observations.

Cron: soulkiller:muse-aggregate, daily 04:30 Europe/Rome

Usage:
  python3 soulkiller_muse_aggregator.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import os
import statistics
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from soulkiller_muse_recorder import EEG_SCHEMA_SQL

# ── Telegram logs topic ───────────────────────────────────────────────────────
# Configure via env vars: TELEGRAM_BOT_TOKEN, TELEGRAM_LOGS_CHAT_ID, TELEGRAM_LOGS_THREAD_ID

_TG_CHAT_ID    = os.environ.get("TELEGRAM_LOGS_CHAT_ID", "")
_TG_THREAD_ID  = os.environ.get("TELEGRAM_LOGS_THREAD_ID", "")


def _tg_bot_token() -> str | None:
    return os.environ.get("TELEGRAM_BOT_TOKEN") or None


def _notify_logs(text: str) -> None:
    """Send a message to the Telegram logs topic (silently on failure)."""
    token = _tg_bot_token()
    if not token or not _TG_CHAT_ID:
        return
    try:
        data = urllib.parse.urlencode({
            "chat_id":           _TG_CHAT_ID,
            "message_thread_id": _TG_THREAD_ID,
            "text":              text,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # log delivery must never break the aggregator

# ── Context tag taxonomy ──────────────────────────────────────────────────────

CONTEXT_FOCUS_TAGS = ["coding", "reading", "work", "study", "writing"]
CONTEXT_CALM_TAGS  = ["morning_baseline", "evening", "meditation", "rest", "breathing"]

# Quality gate: sessions below this score are excluded from aggregation
QUALITY_GATE = 0.5

# ── Core aggregation ──────────────────────────────────────────────────────────

def aggregate_daily_eeg(db, date_str: str, dry_run: bool = False) -> int:
    """Aggregate all quality EEG sessions for date_str into biofeedback_readings.

    Returns the number of signal rows stored (0 if no qualifying sessions).
    """
    # Ensure EEG tables exist (idempotent)
    db.executescript(EEG_SCHEMA_SQL)

    # Fetch all sessions + metrics for the target date that pass quality gate
    sessions = db.execute(
        """
        SELECT
            s.session_id,
            s.quality_score,
            m.avg_delta,
            m.avg_theta,
            m.avg_alpha,
            m.avg_beta,
            m.avg_gamma,
            m.theta_beta_ratio,
            m.engagement_index,
            m.avg_frontal_asymmetry,
            m.alpha_variability,
            m.beta_variability,
            m.focus_score,
            m.calm_score,
            s.context_tag
        FROM eeg_sessions s
        JOIN eeg_session_metrics m ON s.session_id = m.session_id
        WHERE DATE(s.started_at) = ?
          AND s.quality_score >= ?
          AND s.ended_at IS NOT NULL
        """,
        (date_str, QUALITY_GATE),
    ).fetchall()

    if not sessions:
        return 0

    def _avg(field: str) -> float | None:
        vals = [s[field] for s in sessions if s[field] is not None]
        return statistics.mean(vals) if vals else None

    # Build daily signal dict: signal_type → value
    signals: dict[str, float | None] = {
        "eeg_focus_score":        _avg("focus_score"),
        "eeg_calm_score":         _avg("calm_score"),
        "eeg_theta_beta_ratio":   _avg("theta_beta_ratio"),
        "eeg_frontal_asymmetry":  _avg("avg_frontal_asymmetry"),
        "eeg_engagement":         _avg("engagement_index"),
        "eeg_alpha_variability":  _avg("alpha_variability"),
        "eeg_meditation_depth":   _avg("calm_score"),    # alias: calm as meditation depth
        "eeg_cognitive_load":     _avg("theta_beta_ratio"),  # alias: high theta/beta = load
    }

    now = datetime.now(timezone.utc).isoformat()
    count = 0

    for sig_type, val in signals.items():
        if val is None:
            continue
        if dry_run:
            print(f"[DRY] {date_str} {sig_type}={val:.4f}")
            count += 1
            continue
        db.execute(
            """
            INSERT INTO biofeedback_readings (date, signal_type, value, unit, pulled_at)
            VALUES (?, ?, ?, 'score', ?)
            ON CONFLICT(date, signal_type) DO UPDATE SET
                value=excluded.value,
                pulled_at=excluded.pulled_at
            """,
            (date_str, sig_type, val, now),
        )
        count += 1

    if not dry_run:
        db.commit()

    return count


# ── CLI ───────────────────────────────────────────────────────────────────────

def _get_db():
    import sqlite3
    db_path = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def main() -> None:
    parser = argparse.ArgumentParser(description="Soulkiller Muse 2 EEG Aggregator")
    parser.add_argument("--date", default=None,
                        help="Date to aggregate YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be stored without writing")
    args = parser.parse_args()

    date_str = args.date or (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    db = _get_db()
    n = aggregate_daily_eeg(db, date_str, dry_run=args.dry_run)
    db.close()

    summary = f"EEG aggregate {date_str}: {n} signals {'(dry)' if args.dry_run else 'stored'}"
    print(summary)

    if not args.dry_run:
        if n > 0:
            msg = (f"✅ Muse 2 EEG aggregato per {date_str}: {n} segnali salvati in biofeedback_readings.")
        else:
            msg = (f"ℹ️ Muse 2 EEG aggregato per {date_str}: 0 segnali (nessuna sessione qualificante).")
        _notify_logs(msg)


if __name__ == "__main__":
    main()
