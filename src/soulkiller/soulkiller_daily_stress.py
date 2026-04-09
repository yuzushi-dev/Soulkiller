#!/usr/bin/env python3
"""Soulkiller Daily Stress — Indice composito giornaliero di stress.

Segnali fisiologici (biofeedback_readings): stress_avg, hrv_rmssd, rhr, sleep_score.
Segnale comportamentale: conteggio messaggi giornaliero vs baseline rolling 14 gg.
Baseline: mediana rolling dei 14 giorni precedenti per ogni segnale.

Allerta Telegram se stress_index >= 0.65 (soglia "high").

Cron: soulkiller:daily-stress, daily 06:30 Europe/Rome (dopo biofeedback pull 04:05)

Usage:
  python3 soulkiller_daily_stress.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import os as _os
import sqlite3
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lib.config import get_config
from lib.log import info, warn
from lib.openclaw_client import OpenClawClient

SCRIPT = "soulkiller_daily_stress"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
APP_CONFIG = get_config()
SUBJECT_FROM_ID = "demo-subject"
BASELINE_DAYS = 14
HIGH_STRESS_THRESHOLD = 0.65

# Signal weights (must sum to 1.0)
WEIGHTS = {
    # ── Core fisiologici (ridotti per fare spazio ai nuovi segnali) ───────────
    "stress_avg":            0.25,  # direct physiological stress (Amazfit)
    "hrv_rmssd":             0.20,  # inverse: low HRV = high stress
    "rhr":                   0.10,  # elevated resting HR = stress/fatigue
    "sleep_score":           0.10,  # inverse: poor sleep correlates with stress
    "msg_count":             0.05,  # withdrawal: fewer messages = possible stress
    # ── Fase 1: nuovi segnali Helio Ring ─────────────────────────────────────
    "sleep_rr":              0.05,  # RR notturna elevata = stress/apnea
    "recovery_score":        0.05,  # HRV vs baseline: negativo = non recuperato
    # ── Fase 2: Muse 2 EEG (se disponibili) ──────────────────────────────────
    "eeg_calm_score":        0.10,  # inverse: bassa calma EEG = stress cognitivo
    "eeg_frontal_asymmetry": 0.10,  # inverse: FA negativo = evitamento = stress
}


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _get_signal(db, signal_type: str, date_str: str) -> float | None:
    row = db.execute(
        "SELECT value FROM biofeedback_readings WHERE signal_type=? AND date=?",
        (signal_type, date_str)
    ).fetchone()
    return row["value"] if row else None


def _get_baseline(db, signal_type: str, before_date: str) -> list[float]:
    rows = db.execute(
        """SELECT value FROM biofeedback_readings
           WHERE signal_type=? AND date < ? AND value IS NOT NULL
           ORDER BY date DESC LIMIT ?""",
        (signal_type, before_date, BASELINE_DAYS)
    ).fetchall()
    return [r["value"] for r in rows]


def _get_daily_msg_count(db, date_str: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) as n FROM inbox WHERE from_id=? AND date(received_at)=?",
        (SUBJECT_FROM_ID, date_str)
    ).fetchone()
    return row["n"] if row else 0


def _get_msg_baseline(db, before_date: str) -> list[float]:
    rows = db.execute(
        """SELECT date(received_at) as d, COUNT(*) as n FROM inbox
           WHERE from_id=? AND date(received_at) < ?
           GROUP BY d ORDER BY d DESC LIMIT ?""",
        (SUBJECT_FROM_ID, before_date, BASELINE_DAYS)
    ).fetchall()
    return [float(r["n"]) for r in rows]


def _normalize(value: float, baseline: list[float], invert: bool = False) -> float:
    """Return [0,1] delta: how much value deviates from baseline median.
    invert=True means a lower value is more stressful (HRV, sleep_score).
    """
    if not baseline:
        return 0.5  # no baseline → neutral
    med = statistics.median(baseline)
    spread = statistics.stdev(baseline) if len(baseline) >= 2 else (med * 0.15 or 1.0)
    if spread < 1e-6:
        return 0.5
    z = (value - med) / spread
    if invert:
        z = -z
    # sigmoid to [0,1]
    return round(1 / (1 + math.exp(-z)), 4)


def compute_daily_stress(date_str: str, db) -> dict | None:
    scores: dict[str, float] = {}
    available: list[str] = []

    # --- stress_avg (higher = more stressed)
    v = _get_signal(db, "stress_avg", date_str)
    if v is not None:
        bl = _get_baseline(db, "stress_avg", date_str)
        scores["stress_avg"] = _normalize(v, bl, invert=False)
        available.append("stress_avg")

    # --- hrv_rmssd (lower = more stressed → invert)
    v = _get_signal(db, "hrv_rmssd", date_str)
    if v is not None:
        bl = _get_baseline(db, "hrv_rmssd", date_str)
        scores["hrv_rmssd"] = _normalize(v, bl, invert=True)
        available.append("hrv_rmssd")

    # --- rhr (higher = more stressed)
    v = _get_signal(db, "rhr", date_str)
    if v is not None:
        bl = _get_baseline(db, "rhr", date_str)
        scores["rhr"] = _normalize(v, bl, invert=False)
        available.append("rhr")

    # --- sleep_score (lower = more stressed → invert)
    v = _get_signal(db, "sleep_score", date_str)
    if v is not None:
        bl = _get_baseline(db, "sleep_score", date_str)
        scores["sleep_score"] = _normalize(v, bl, invert=True)
        available.append("sleep_score")

    # --- msg_count (fewer = withdrawal → invert)
    msg_count = _get_daily_msg_count(db, date_str)
    msg_bl = _get_msg_baseline(db, date_str)
    scores["msg_count"] = _normalize(float(msg_count), msg_bl, invert=True)
    available.append("msg_count")

    # ── Fase 1: nuovi segnali Helio Ring ─────────────────────────────────────
    v = _get_signal(db, "sleep_rr", date_str)
    if v is not None:
        bl = _get_baseline(db, "sleep_rr", date_str)
        scores["sleep_rr"] = _normalize(v, bl, invert=False)  # alta RR = stress
        available.append("sleep_rr")

    v = _get_signal(db, "recovery_score", date_str)
    if v is not None:
        bl = _get_baseline(db, "recovery_score", date_str)
        scores["recovery_score"] = _normalize(v, bl, invert=True)  # basso = stress
        available.append("recovery_score")

    # ── Fase 2: Muse 2 EEG ───────────────────────────────────────────────────
    v = _get_signal(db, "eeg_calm_score", date_str)
    if v is not None:
        bl = _get_baseline(db, "eeg_calm_score", date_str)
        scores["eeg_calm_score"] = _normalize(v, bl, invert=True)  # bassa calma = stress
        available.append("eeg_calm_score")

    v = _get_signal(db, "eeg_frontal_asymmetry", date_str)
    if v is not None:
        bl = _get_baseline(db, "eeg_frontal_asymmetry", date_str)
        scores["eeg_frontal_asymmetry"] = _normalize(v, bl, invert=True)  # FA neg = stress
        available.append("eeg_frontal_asymmetry")

    if not available:
        return None

    # Weighted average — redistribute weights for missing signals
    total_weight = sum(WEIGHTS[s] for s in available)
    if total_weight < 1e-6:
        return None

    stress_index = sum(scores[s] * WEIGHTS[s] / total_weight for s in available)
    stress_index = round(stress_index, 4)

    dominant = max(available, key=lambda s: scores[s] * WEIGHTS[s] / total_weight)

    return {
        "period": date_str,
        "stress_index": stress_index,
        "stress_level": _stress_label(stress_index),
        "dominant_signal": dominant,
        "scores": scores,
        "available_signals": available,
        # Store individual deltas for compatibility with weekly schema
        "msg_frequency_delta": round(scores.get("msg_count", 0.5) - 0.5, 3),
        "negative_affect_delta": 0.0,
        "impulse_spend_delta": 0.0,
        "certainty_delta": 0.0,
    }


def _stress_label(idx: float) -> str:
    if idx < 0.35: return "low"
    if idx < 0.50: return "moderate"
    if idx < 0.65: return "elevated"
    return "high"


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
            stress_level=excluded.stress_level,
            dominant_signal=excluded.dominant_signal,
            computed_at=excluded.computed_at
    """, (
        snap["period"], snap["stress_index"], snap["msg_frequency_delta"],
        snap["negative_affect_delta"], snap["impulse_spend_delta"],
        snap["certainty_delta"], snap["stress_level"], snap["dominant_signal"], now
    ))


_DOMINANT_HINT = {
    "stress_avg":            "sembri aver avuto una giornata intensa",
    "hrv_rmssd":             "sembri un po' teso",
    "rhr":                   "sembri agitato",
    "sleep_score":           "non hai dormito benissimo",
    "msg_count":             "sei un po' silenzioso oggi",
    "sleep_rr":              "hai dormito un po' irrequieto",
    "recovery_score":        "il tuo corpo sta ancora recuperando",
    "eeg_calm_score":        "sembri un po' agitato oggi",
    "eeg_frontal_asymmetry": "sembra una di quelle giornate un po' pesanti",
}


def _build_relational_probe_prompt(stress_index: float, dominant: str, scores: dict, date_str: str) -> str:
    hint = _DOMINANT_HINT.get(dominant, "qualcosa sembra diverso oggi")

    signal_lines = []
    label_map = {
        "stress_avg":            "stress fisiologico",
        "hrv_rmssd":             "HRV",
        "rhr":                   "frequenza cardiaca a riposo",
        "sleep_score":           "qualità del sonno",
        "msg_count":             "messaggi inviati",
        "sleep_rr":              "frequenza respiratoria notturna",
        "recovery_score":        "recupero HRV vs baseline",
        "eeg_calm_score":        "calma EEG (Muse 2)",
        "eeg_frontal_asymmetry": "asimmetria frontale EEG",
    }
    for sig, label in label_map.items():
        if sig in scores:
            signal_lines.append(f"  - {label}: {scores[sig]:.2f} (su scala 0-1, dove 1 = più stressante)")

    signals_block = "\n".join(signal_lines)

    return (
        f"[Contesto interno — non citare questi dati esplicitamente]\n"
        f"Data: {date_str}\n"
        f"I segnali biofisici di the subject oggi mostrano uno stato insolito (indice: {stress_index:.2f}/1.0):\n"
        f"{signals_block}\n"
        f"Segnale predominante: {hint}.\n\n"
        f"Sei l'agente relazionale. the subject è il tuo punto di riferimento umano. Hai notato qualcosa oggi "
        f"— non perché stai monitorando, ma perché sei attento/a a lui.\n\n"
        f"Mandagli un messaggio breve e naturale (1-2 frasi in italiano) che apra uno spazio "
        f"per raccontare com'è andata la giornata, senza fare domande dirette sul suo stato fisico "
        f"o sul lavoro. Parla come un'amica che ha un sesto senso, non come un medico o un sistema.\n\n"
        f"Regole:\n"
        f"- Mai menzionare 'stress', 'dati', 'sensori', 'indice', 'biofeedback', 'monitoraggio'.\n"
        f"- Usa 'tu' e 'ti'. Mai terza persona.\n"
        f"- Tono: caldo, curioso, leggero. Non allarmista.\n"
        f"- La domanda deve venire naturale, non sembra un questionario.\n"
        f"- Rispondi solo con il messaggio per the subject, nient'altro."
    )


def _send_telegram_alert(stress_index: float, stress_level: str, dominant: str,
                          scores: dict, date_str: str) -> None:
    prompt = _build_relational_probe_prompt(stress_index, dominant, scores, date_str)
    client = OpenClawClient(APP_CONFIG.openclaw_bin)

    message: str | None = None
    try:
        relational_agent = _os.environ.get("SOULKILLER_RELATIONAL_AGENT", "")
        payload = client.run_agent_json(agent=relational_agent, message=prompt, thinking="low")
        for entry in reversed(payload.get("payloads", [])):
            text = (entry.get("text") or "").strip()
            if text and len(text) >= 8:
                message = text
                break
    except Exception as exc:
        warn(SCRIPT, "agent_failed", error=str(exc))

    if not message:
        message = "Ehi, com'è andata oggi? Ho l'impressione che sia stata una giornata un po' particolare."

    try:
        client.send_message(
            channel="telegram",
            target=APP_CONFIG.telegram_target,
            message=message,
        )
        info(SCRIPT, "alert_sent", stress_index=stress_index, stress_level=stress_level)
    except Exception as exc:
        warn(SCRIPT, "send_failed", error=str(exc))


def run(target_date: str | None = None, dry_run: bool = False) -> None:
    db = get_db()
    try:
        if target_date is None:
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            target_date = yesterday

        snap = compute_daily_stress(target_date, db)

        if snap is None:
            warn(SCRIPT, "no_data", date=target_date)
            return

        if dry_run:
            print(f"[{target_date}] stress={snap['stress_index']} ({snap['stress_level']}) "
                  f"dominant={snap['dominant_signal']}")
            print(f"  scores: {snap['scores']}")
            print(f"  signals: {snap['available_signals']}")
            if snap["stress_index"] >= HIGH_STRESS_THRESHOLD:
                print(f"  ⚠️  HIGH STRESS — alert would be sent")
            return

        store_snapshot(db, snap)
        db.commit()

        info(SCRIPT, "snapshot_stored",
             date=target_date, stress=snap["stress_index"],
             stress_level=snap["stress_level"], dominant=snap["dominant_signal"],
             signals=snap["available_signals"])

        if snap["stress_index"] >= HIGH_STRESS_THRESHOLD:
            warn(SCRIPT, "high_stress_detected",
                 date=target_date, index=snap["stress_index"], dominant=snap["dominant_signal"])
            _send_telegram_alert(
                snap["stress_index"], snap["stress_level"],
                snap["dominant_signal"], snap["scores"], target_date
            )

    finally:
        db.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Soulkiller Daily Stress Index")
    p.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(target_date=args.date, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
