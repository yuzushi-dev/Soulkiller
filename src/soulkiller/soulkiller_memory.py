#!/usr/bin/env python3
"""Soulkiller Communication Metrics & Decision Coherence

Computes programmatic behavioral metrics from inbox messages:
  - activity_hours: 24-bucket histogram + weekday distribution
  - msg_length: mean/median/std chars and words
  - punctuation: ellipsis, !, ?, CAPS, emoji frequency per 100 chars
  - burst_pattern: sequences of rapid messages
  - vocabulary: type-token ratio, sentence length

Converts relevant metrics into soulkiller observations.

Cron: soulkiller:memory-metrics, weekly Monday 05:30 Europe/Rome

Usage:
  python3 soulkiller_memory.py [--platform telegram] [--dry-run]
"""

from __future__ import annotations
import os

import json
import re
import sqlite3
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn

SCRIPT = "soulkiller_memory"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_PLATFORM = "telegram"
DEFAULT_CHAT_ID = "demo-subject"


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_inbox(db) -> list[dict]:
    rows = db.execute(
        "SELECT content, received_at FROM inbox ORDER BY received_at ASC"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Metric computations
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "[\U00010000-\U0010ffff"
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F9FF"
    "\U00002600-\U000027BF"
    "]+",
    flags=re.UNICODE,
)

_ELLIPSIS_RE = re.compile(r'\.{2,}|…')
_CAPS_WORD_RE = re.compile(r'\b[A-Z]{2,}\b')
_LOL_RE = re.compile(r'\b(aha+|lol+|haha+|ahah+|hehe+|xD|XD|:D|😂|🤣)\b', re.IGNORECASE)


def _parse_ts(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def compute_activity_hours(messages: list[dict]) -> dict:
    """24-bucket hour histogram + weekday distribution."""
    hour_counts = [0] * 24
    dow_counts = [0] * 7  # 0=Monday … 6=Sunday
    for m in messages:
        dt = _parse_ts(m["received_at"])
        if not dt:
            continue
        hour_counts[dt.hour] += 1
        dow_counts[dt.weekday()] += 1

    peak_hour = hour_counts.index(max(hour_counts))
    active = [h for h in range(24) if hour_counts[h] > 0]
    active_range = f"{min(active):02d}-{max(active):02d}" if active else "unknown"
    peak_dow = dow_counts.index(max(dow_counts))
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    return {
        "hours": hour_counts,
        "peak_hour": peak_hour,
        "active_range": active_range,
        "dow": dow_counts,
        "peak_dow": dow_names[peak_dow],
        "weekend_pct": round(
            (dow_counts[5] + dow_counts[6]) / max(sum(dow_counts), 1) * 100, 1
        ),
    }


def compute_msg_length(messages: list[dict]) -> dict:
    """Char and word length statistics."""
    chars = [len(m["content"]) for m in messages]
    words = [len(m["content"].split()) for m in messages]
    if not chars:
        return {}
    short = sum(1 for c in chars if c < 20)
    long_ = sum(1 for c in chars if c > 200)
    return {
        "mean_chars": round(statistics.mean(chars), 1),
        "median_chars": round(statistics.median(chars), 1),
        "std_chars": round(statistics.stdev(chars) if len(chars) > 1 else 0, 1),
        "mean_words": round(statistics.mean(words), 1),
        "short_pct": round(short / len(chars) * 100, 1),
        "long_pct": round(long_ / len(chars) * 100, 1),
    }


def compute_punctuation(messages: list[dict]) -> dict:
    """Punctuation/style markers per 100 chars."""
    total_chars = sum(len(m["content"]) for m in messages)
    if total_chars == 0:
        return {}
    full_text = " ".join(m["content"] for m in messages)

    def per_100(count: int) -> float:
        return round(count / total_chars * 100, 2)

    return {
        "ellipsis_per_100": per_100(len(_ELLIPSIS_RE.findall(full_text))),
        "exclamation_per_100": per_100(full_text.count("!")),
        "question_per_100": per_100(full_text.count("?")),
        "caps_per_100": per_100(len(_CAPS_WORD_RE.findall(full_text))),
        "emoji_per_100": per_100(len(_EMOJI_RE.findall(full_text))),
        "lol_per_100": per_100(len(_LOL_RE.findall(full_text))),
    }


def compute_burst_pattern(messages: list[dict]) -> dict:
    """Sequences of 2+ messages within 60 seconds."""
    BURST_GAP_SECONDS = 60
    bursts: list[int] = []
    current_burst = [messages[0]] if messages else []
    for m in messages[1:]:
        prev_dt = _parse_ts(current_burst[-1]["received_at"])
        curr_dt = _parse_ts(m["received_at"])
        if prev_dt and curr_dt and (curr_dt - prev_dt).total_seconds() <= BURST_GAP_SECONDS:
            current_burst.append(m)
            continue
        if len(current_burst) >= 2:
            bursts.append(len(current_burst))
        current_burst = [m]
    if len(current_burst) >= 2:
        bursts.append(len(current_burst))

    total = len(messages)
    burst_msg_count = sum(bursts)
    return {
        "burst_count": len(bursts),
        "avg_burst_size": round(statistics.mean(bursts), 1) if bursts else 0,
        "max_burst_size": max(bursts) if bursts else 0,
        "single_msg_pct": round((total - burst_msg_count) / max(total, 1) * 100, 1),
        "burst_msg_pct": round(burst_msg_count / max(total, 1) * 100, 1),
    }


def compute_vocabulary(messages: list[dict]) -> dict:
    """Type-token ratio and language mix estimate."""
    all_words: list[str] = []
    for m in messages:
        all_words.extend(re.findall(r'\b[a-zA-ZàèéìòùÀÈÉÌÒÙ]+\b', m["content"].lower()))

    if not all_words:
        return {}

    unique = set(all_words)
    ttr = round(len(unique) / len(all_words), 4)

    it_markers = {"il", "la", "le", "lo", "gli", "una", "un", "che", "di", "del",
                  "della", "dei", "con", "per", "non", "ma", "si", "mi", "ti",
                  "ci", "vi", "ho", "ha", "hai", "sei", "sono"}
    en_markers = {"the", "and", "or", "but", "not", "is", "are", "was", "were",
                  "have", "has", "had", "will", "would", "can", "could", "should"}
    it_count = sum(1 for w in all_words if w in it_markers)
    en_count = sum(1 for w in all_words if w in en_markers)
    total_markers = max(it_count + en_count, 1)

    sentences = re.split(r'[.!?]+', " ".join(m["content"] for m in messages))
    sentences = [s.strip() for s in sentences if len(s.strip()) > 3]
    avg_sent_len = round(
        statistics.mean(len(s.split()) for s in sentences), 1
    ) if sentences else 0

    return {
        "unique_words": len(unique),
        "total_words": len(all_words),
        "ttr": ttr,
        "avg_sentence_length": avg_sent_len,
        "it_pct": round(it_count / total_markers * 100, 1),
        "en_pct": round(en_count / total_markers * 100, 1),
    }


def compute_all_metrics(messages: list[dict]) -> list[dict[str, Any]]:
    """Compute all communication metrics. Returns list of metric rows."""
    if not messages:
        return []

    n = len(messages)
    results = []
    for metric_type, fn in [
        ("activity_hours", compute_activity_hours),
        ("msg_length", compute_msg_length),
        ("punctuation", compute_punctuation),
        ("burst_pattern", compute_burst_pattern),
        ("vocabulary", compute_vocabulary),
    ]:
        try:
            data = fn(messages)
            if data:
                results.append({"metric_type": metric_type, "metric_data": data, "sample_size": n})
        except Exception as e:
            warn(SCRIPT, "metric_error", metric=metric_type, error=str(e))
    return results


# ---------------------------------------------------------------------------
# Store metrics
# ---------------------------------------------------------------------------

def store_metrics(db, platform: str, chat_id: str, period: str,
                  metrics: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    for m in metrics:
        db.execute(
            """INSERT INTO communication_metrics
               (platform, chat_id, period, metric_type, metric_data, sample_size, computed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(platform, chat_id, period, metric_type)
               DO UPDATE SET metric_data=excluded.metric_data,
                             sample_size=excluded.sample_size,
                             computed_at=excluded.computed_at""",
            (platform, chat_id, period,
             m["metric_type"], json.dumps(m["metric_data"]),
             m["sample_size"], now)
        )
        stored += 1
    db.commit()
    return stored


# ---------------------------------------------------------------------------
# Convert metrics → observations
# ---------------------------------------------------------------------------

def metrics_to_observations(metrics: list[dict]) -> list[dict]:
    obs = []
    data_map = {m["metric_type"]: m["metric_data"] for m in metrics}

    # Temporal chronotype from peak activity hour
    ah = data_map.get("activity_hours", {})
    if ah:
        peak_h = ah.get("peak_hour", 12)
        if peak_h < 6:
            label, pos = "extreme night owl", 0.1
        elif peak_h < 10:
            label, pos = "early bird", 0.8
        elif peak_h < 13:
            label, pos = "morning person", 0.65
        elif peak_h < 17:
            label, pos = "afternoon person", 0.5
        elif peak_h < 21:
            label, pos = "evening person", 0.35
        else:
            label, pos = "night owl", 0.2
        obs.append({
            "facet_id": "temporal.chronotype",
            "value_position": pos,
            "confidence": 0.65,
            "evidence": (f"Peak Telegram activity at {peak_h:02d}:xx ({label}). "
                         f"Active range: {ah.get('active_range', '?')}. "
                         f"Weekend: {ah.get('weekend_pct', '?')}% of messages."),
            "source_type": "communication_metrics",
            "source_ref": "metrics:activity_hours",
        })

    # Ellipsis → introspective vs direct expression
    punc = data_map.get("punctuation", {})
    if punc and punc.get("ellipsis_per_100", 0) > 1.5:
        obs.append({
            "facet_id": "emotional.emotional_expression",
            "value_position": 0.35,
            "confidence": 0.5,
            "evidence": (f"Ellipsis at {punc['ellipsis_per_100']:.1f}/100 chars - "
                         "trailing/unfinished thoughts suggest internal processing over direct expression."),
            "source_type": "communication_metrics",
            "source_ref": "metrics:punctuation",
        })

    # Burst pattern → stream-of-consciousness vs composed thinking
    burst = data_map.get("burst_pattern", {})
    if burst and burst.get("burst_msg_pct", 0) > 40:
        obs.append({
            "facet_id": "cognitive.thinking_style",
            "value_position": 0.3,
            "confidence": 0.55,
            "evidence": (f"{burst['burst_msg_pct']:.0f}% of messages sent in rapid bursts "
                         f"(avg {burst['avg_burst_size']:.1f} msgs) - "
                         "associative/stream-of-consciousness style."),
            "source_type": "communication_metrics",
            "source_ref": "metrics:burst_pattern",
        })

    # Message length → communication depth
    ml = data_map.get("msg_length", {})
    if ml:
        mc = ml.get("mean_chars", 0)
        if mc < 50:
            label, pos = "terse", 0.3
        elif mc < 120:
            label, pos = "moderate", 0.5
        else:
            label, pos = "verbose", 0.7
        obs.append({
            "facet_id": "cognitive.communication_depth",
            "value_position": pos,
            "confidence": 0.6,
            "evidence": (f"Mean message {mc:.0f} chars ({label}). "
                         f"{ml.get('short_pct', 0):.0f}% very short, "
                         f"{ml.get('long_pct', 0):.0f}% long."),
            "source_type": "communication_metrics",
            "source_ref": "metrics:msg_length",
        })

    return obs


def store_observations(db, obs: list[dict]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    stored = 0
    for o in obs:
        try:
            db.execute(
                """INSERT OR IGNORE INTO observations
                   (facet_id, signal_position, signal_strength, content,
                    source_type, source_ref, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (o["facet_id"], o["value_position"], o["confidence"],
                 o["evidence"], o["source_type"], o["source_ref"], now)
            )
            stored += 1
        except Exception as e:
            warn(SCRIPT, "obs_insert_error", facet=o.get("facet_id"), error=str(e))
    db.commit()
    return stored


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(platform: str = DEFAULT_PLATFORM, chat_id: str = DEFAULT_CHAT_ID,
        dry_run: bool = False) -> None:
    db = get_db()
    try:
        messages = load_inbox(db)
        if not messages:
            info(SCRIPT, "no_messages")
            return

        info(SCRIPT, "run_start", messages=len(messages))

        metrics = compute_all_metrics(messages)
        info(SCRIPT, "metrics_computed", count=len(metrics))

        if dry_run:
            for m in metrics:
                print(f"\n[{m['metric_type']}] n={m['sample_size']}")
                print(json.dumps(m["metric_data"], indent=2, ensure_ascii=False))
        else:
            stored = store_metrics(db, platform, chat_id, "all", metrics)
            info(SCRIPT, "metrics_stored", count=stored)

        obs = metrics_to_observations(metrics)
        info(SCRIPT, "observations_derived", count=len(obs))

        if dry_run:
            print("\n--- Observations ---")
            for o in obs:
                print(f"  {o['facet_id']} pos={o['value_position']} conf={o['confidence']}")
                print(f"  {o['evidence'][:120]}")
        else:
            n_obs = store_observations(db, obs)
            info(SCRIPT, "observations_stored", count=n_obs)

        info(SCRIPT, "run_complete")
    finally:
        db.close()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Soulkiller Communication Metrics")
    parser.add_argument("--platform", default=DEFAULT_PLATFORM)
    parser.add_argument("--chat-id", default=DEFAULT_CHAT_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(platform=args.platform, chat_id=args.chat_id, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
