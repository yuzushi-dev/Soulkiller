#!/usr/bin/env python3
"""Soulkiller Stress Index — Indice composito settimanale di stress.

Segnali: negative_affect (LIWC), frequenza messaggi, spesa impulsiva (budget).
Baseline calcolata sulla mediana storica di ogni segnale.

Cron: soulkiller:stress-index, settimanale lunedì 06:00 Europe/Rome

Usage:
  python3 soulkiller_stress_index.py [--week YYYY-WW] [--dry-run]
"""
from __future__ import annotations

import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

from lib.log import info, warn

SCRIPT = "soulkiller_stress_index"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
SUBJECT_FROM_ID = "demo-subject"


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def iso_week(dt_str: str) -> str:
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return f"{dt.isocalendar()[0]:04d}-{dt.isocalendar()[1]:02d}"


def get_msg_counts_by_week(db) -> dict[str, int]:
    rows = db.execute(
        "SELECT received_at FROM inbox WHERE from_id=?", (SUBJECT_FROM_ID,)
    ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        w = iso_week(r["received_at"])
        counts[w] = counts.get(w, 0) + 1
    return counts


def get_liwc_by_week(db) -> dict[str, dict]:
    rows = db.execute(
        "SELECT period, negative_affect, certainty_ratio FROM liwc_metrics"
    ).fetchall()
    return {r["period"]: dict(r) for r in rows}


def _compute_baseline(values: list[float]) -> float:
    if not values:
        return 0.0
    return statistics.median(values)


def _sigmoid_index(raw: float) -> float:
    return round(1 / (1 + math.exp(-4 * raw)), 3)


def _z_score_cert(cur_cert: float, all_cert_values: list[float]) -> float:
    """Compute z-score of cur_cert against last 90-day window (IMP-06).

    Uses the 13 most recent liwc records as the rolling baseline.
    Falls back to raw delta if < 4 records are available.
    Returns a value in roughly [-3, 3] representing deviation from personal norm.
    """
    # Use last 13 periods as proxy for ~90 days (monthly periods)
    recent = all_cert_values[-13:]
    if len(recent) < 4:
        return 0.0  # insufficient baseline — caller will fall back
    mu = statistics.mean(recent)
    try:
        sigma = statistics.stdev(recent)
    except statistics.StatisticsError:
        sigma = 0.0
    if sigma < 1e-6:
        return 0.0
    return (cur_cert - mu) / sigma


def stress_level_label(idx: float) -> str:
    if idx < 0.35: return "low"
    if idx < 0.50: return "moderate"
    if idx < 0.65: return "elevated"
    return "high"


def compute_stress(week: str, db) -> dict | None:
    msg_counts = get_msg_counts_by_week(db)
    liwc_data  = get_liwc_by_week(db)

    if week not in msg_counts:
        return None

    # Baselines
    all_counts = list(msg_counts.values())
    baseline_msgs = _compute_baseline(all_counts)
    all_cert_values = [v["certainty_ratio"] for v in liwc_data.values() if v["certainty_ratio"] is not None]
    baseline_neg  = _compute_baseline([v["negative_affect"] for v in liwc_data.values() if v["negative_affect"] is not None])
    baseline_cert = _compute_baseline(all_cert_values)

    # Current week signals
    cur_msgs = msg_counts.get(week, 0)
    cur_liwc = liwc_data.get(week, {})
    cur_neg  = cur_liwc.get("negative_affect") or baseline_neg
    cur_cert = cur_liwc.get("certainty_ratio") or baseline_cert

    # Deltas (positive = stress direction)
    freq_delta = (baseline_msgs - cur_msgs) / (baseline_msgs + 1)  # withdrawal = stress
    neg_delta  = (cur_neg - baseline_neg)  / (baseline_neg + 0.1)

    # IMP-06: z-score certainty_rigidity against 90-day rolling baseline
    z = _z_score_cert(cur_cert, all_cert_values)
    if z != 0.0:
        # Map z to [-1, 1] range (clip at ±3σ), positive = more rigid than usual = stress
        cert_delta = max(-1.0, min(1.0, z / 3.0))
    else:
        # Insufficient baseline — fall back to raw delta
        cert_delta = (cur_cert - baseline_cert) / (baseline_cert + 0.1)

    # Weighted composite
    raw = freq_delta * 0.35 + neg_delta * 0.45 + cert_delta * 0.20
    stress_idx = _sigmoid_index(raw)

    # Dominant signal
    signals = {
        "withdrawal": freq_delta * 0.35,
        "negative_affect": neg_delta * 0.45,
        "rigidity": cert_delta * 0.20,
    }
    dominant = max(signals, key=lambda k: abs(signals[k]))

    return {
        "period": week,
        "stress_index": stress_idx,
        "msg_frequency_delta": round(freq_delta, 3),
        "negative_affect_delta": round(neg_delta, 3),
        "impulse_spend_delta": 0.0,
        "certainty_delta": round(cert_delta, 3),
        "stress_level": stress_level_label(stress_idx),
        "dominant_signal": dominant,
    }


def store_snapshot(db, snap: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO stress_snapshots
            (period, stress_index, msg_frequency_delta, negative_affect_delta,
             impulse_spend_delta, certainty_delta, stress_level, dominant_signal, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(period) DO UPDATE SET
            stress_index=excluded.stress_index,
            msg_frequency_delta=excluded.msg_frequency_delta,
            negative_affect_delta=excluded.negative_affect_delta,
            certainty_delta=excluded.certainty_delta,
            stress_level=excluded.stress_level,
            dominant_signal=excluded.dominant_signal,
            computed_at=excluded.computed_at
    """, (
        snap["period"], snap["stress_index"], snap["msg_frequency_delta"],
        snap["negative_affect_delta"], snap["impulse_spend_delta"],
        snap["certainty_delta"], snap["stress_level"], snap["dominant_signal"], now
    ))


def run(week: str | None = None, dry_run: bool = False) -> None:
    db = get_db()
    try:
        # Ensure LIWC data exists
        liwc_count = db.execute("SELECT COUNT(*) FROM liwc_metrics").fetchone()[0]
        if liwc_count == 0:
            warn(SCRIPT, "no_liwc_data", hint="Run soulkiller_liwc.py --all first")
            return

        import datetime as dt
        # Build list of weeks to process
        if week:
            weeks = [week]
        else:
            # All weeks that have messages
            msg_counts = get_msg_counts_by_week(db)
            weeks = sorted(msg_counts.keys())

        info(SCRIPT, "run_start", weeks=len(weeks))
        high_stress_weeks = []

        for w in weeks:
            snap = compute_stress(w, db)
            if snap is None:
                continue

            if dry_run:
                print(f"[{w}] stress={snap['stress_index']} ({snap['stress_level']}) "
                      f"dominant={snap['dominant_signal']}")
                if snap["stress_index"] >= 0.65:
                    print(f"  ⚠️  HIGH STRESS DETECTED")
                continue

            store_snapshot(db, snap)
            info(SCRIPT, "snapshot_stored",
                 period=w, stress=snap["stress_index"],
                 stress_level=snap["stress_level"], dominant=snap["dominant_signal"])

            if snap["stress_index"] >= 0.65:
                high_stress_weeks.append(w)

        if not dry_run:
            db.commit()
            if high_stress_weeks:
                warn(SCRIPT, "high_stress_detected", weeks=high_stress_weeks)
            info(SCRIPT, "run_complete")
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Stress Index")
    p.add_argument("--week", help="YYYY-WW week to compute")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(week=args.week, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
