#!/usr/bin/env python3
"""Gadgetbridge SQLite export parser - ingests biofeedback into soulkiller.db.

Reads the Gadgetbridge.db exported from the Android app (hamburger → Database
Management → Export DB) and populates biofeedback_readings + observations for
the Amazfit Helio Ring signals (HRV, RHR, stress, SpO2, PAI, sleep).

Usage:
  python3 soulkiller_biofeedback_gadgetbridge.py --db /path/to/Gadgetbridge.db
  python3 soulkiller_biofeedback_gadgetbridge.py --db /path/to/Gadgetbridge.db --date 2026-03-24
  python3 soulkiller_biofeedback_gadgetbridge.py --db /path/to/Gadgetbridge.db --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# Reuse storage + observation helpers from the main biofeedback module
sys.path.insert(0, str(Path(__file__).resolve().parent))
from soulkiller_biofeedback import derive_observations, get_db, store_all

from lib.log import info, warn

SCRIPT = "soulkiller_biofeedback_gb"

# Italy CET = UTC+1 (winter); update to +2 after last Sunday of March
_ITALY_OFFSET_H = 1


# ── Date windowing ────────────────────────────────────────────────────────────

def _utc_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _day_window_ms(local_date_str: str) -> tuple[int, int]:
    """Return (start_ms, end_ms) in UTC for the full local Italy day."""
    d = date.fromisoformat(local_date_str)
    tz = timezone(timedelta(hours=_ITALY_OFFSET_H))
    start = datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
    return _utc_ms(start), _utc_ms(start) + 86_400_000


def _night_window_ms(local_date_str: str) -> tuple[int, int]:
    """UTC window for the night BEFORE local_date waking (20:00 prev UTC → 12:00 day UTC)."""
    d = date.fromisoformat(local_date_str)
    prev = d - timedelta(days=1)
    t_start = _utc_ms(datetime(prev.year, prev.month, prev.day, 20, 0, tzinfo=timezone.utc))
    t_end   = _utc_ms(datetime(d.year,    d.month,    d.day,    12, 0, tzinfo=timezone.utc))
    return t_start, t_end


def _night_window_s(local_date_str: str) -> tuple[int, int]:
    """Same as _night_window_ms but in seconds (for EXTENDED_ACTIVITY)."""
    s, e = _night_window_ms(local_date_str)
    return s // 1000, e // 1000


# ── Signal extractors ─────────────────────────────────────────────────────────

def extract_rhr(gb: sqlite3.Connection, local_date: str) -> float | None:
    start, end = _day_window_ms(local_date)
    row = gb.execute(
        "SELECT HEART_RATE FROM HUAMI_HEART_RATE_RESTING_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? ORDER BY TIMESTAMP DESC LIMIT 1",
        (start, end),
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def extract_hrv_and_sleep(gb: sqlite3.Connection, local_date: str) -> dict:
    """HRV RMSSD + sleep timing from GENERIC_HRV_VALUE_SAMPLE (Helio Ring nightly)."""
    t_start, t_end = _night_window_ms(local_date)
    rows = gb.execute(
        "SELECT TIMESTAMP, VALUE FROM GENERIC_HRV_VALUE_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? ORDER BY TIMESTAMP",
        (t_start, t_end),
    ).fetchall()
    if len(rows) < 10:
        return {}

    onset_ts  = rows[0][0]  / 1000.0   # seconds
    offset_ts = rows[-1][0] / 1000.0
    total_min = round((offset_ts - onset_ts) / 60)

    values = [r[1] for r in rows if r[1] is not None and 10 <= r[1] <= 120]
    hrv_rmssd = round(statistics.mean(values), 1) if len(values) >= 10 else None

    result: dict = {
        "sleep_onset_ts":  onset_ts,
        "sleep_offset_ts": offset_ts,
        "sleep_total_min": float(total_min),
    }
    if hrv_rmssd is not None:
        result["hrv_rmssd"] = hrv_rmssd
    return result


def extract_stress_avg(gb: sqlite3.Connection, local_date: str) -> float | None:
    start, end = _day_window_ms(local_date)
    rows = gb.execute(
        "SELECT STRESS FROM HUAMI_STRESS_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? AND TYPE_NUM = 1",
        (start, end),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None and 0 < r[0] <= 100]
    return round(statistics.mean(vals), 1) if vals else None


def extract_spo2(gb: sqlite3.Connection, local_date: str) -> float | None:
    """Nightly auto SpO2 (TYPE_NUM=0)."""
    t_start, t_end = _night_window_ms(local_date)
    rows = gb.execute(
        "SELECT SPO2 FROM HUAMI_SPO2_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? AND TYPE_NUM = 0",
        (t_start, t_end),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None and 70 <= r[0] <= 100]
    return round(statistics.mean(vals), 1) if vals else None


def extract_respiratory_rate(gb: sqlite3.Connection, local_date: str) -> float | None:
    """Media frequenza respiratoria notturna da HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE.

    Il nome della colonna varia per versione di Gadgetbridge: RATE o RESPIRATORY_RATE.
    """
    t_start, t_end = _night_window_ms(local_date)
    # Rileva il nome della colonna dalla versione di Gadgetbridge installata
    cols = {r[1] for r in gb.execute(
        "PRAGMA table_info(HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE)"
    ).fetchall()}
    col = "RATE" if "RATE" in cols else "RESPIRATORY_RATE" if "RESPIRATORY_RATE" in cols else None
    if col is None:
        return None
    rows = gb.execute(
        f"SELECT {col} FROM HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ?",
        (t_start, t_end),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None and 8 <= r[0] <= 30]
    return round(statistics.mean(vals), 1) if vals else None


def extract_skin_temperature(gb: sqlite3.Connection, local_date: str) -> float | None:
    """Temperatura cutanea notturna media da GENERIC_TEMPERATURE_SAMPLE."""
    t_start, t_end = _night_window_ms(local_date)
    rows = gb.execute(
        "SELECT TEMPERATURE FROM GENERIC_TEMPERATURE_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ?",
        (t_start, t_end),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None and 30.0 <= r[0] <= 42.0]
    return round(statistics.mean(vals), 2) if vals else None


def extract_hr_continuous(gb: sqlite3.Connection, local_date: str) -> dict | None:
    """HR continuo per le ore di veglia. Ritorna mean, std, min, max, n_samples."""
    start, end = _day_window_ms(local_date)
    rows = gb.execute(
        "SELECT HEART_RATE FROM GENERIC_HEART_RATE_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? AND HEART_RATE > 0",
        (start, end),
    ).fetchall()
    vals = [r[0] for r in rows if r[0] is not None and 30 <= r[0] <= 220]
    if len(vals) < 10:
        return None
    return {
        "hr_mean": round(statistics.mean(vals), 1),
        "hr_std": round(statistics.stdev(vals), 1),
        "hr_min": min(vals),
        "hr_max": max(vals),
        "hr_samples": len(vals),
    }


def extract_sleep_stages_detail(gb: sqlite3.Connection, local_date: str) -> dict | None:
    """Stadi dettagliati del sonno da HUAMI_EXTENDED_ACTIVITY_SAMPLE.

    RAW_KIND: 112=deep, 121/122=light, 123=REM, 120=awake (Amazfit encoding)
    """
    t_start, t_end = _night_window_s(local_date)
    rows = gb.execute(
        "SELECT RAW_KIND, TIMESTAMP FROM HUAMI_EXTENDED_ACTIVITY_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? ORDER BY TIMESTAMP",
        (t_start, t_end),
    ).fetchall()
    if not rows:
        return None

    _stage_map = {112: "deep", 121: "light", 122: "light", 123: "rem", 120: "awake"}
    stage_minutes: dict[str, float] = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
    transitions = 0
    prev_stage = None

    for i, row in enumerate(rows):
        stage = _stage_map.get(row[0])
        if stage is None:
            continue
        # Ogni sample copre il tempo fino al sample successivo (default 1 min)
        if i + 1 < len(rows):
            duration = max(1, (rows[i + 1][1] - row[1]) // 60)
        else:
            duration = 1
        stage_minutes[stage] += duration
        if prev_stage and stage != prev_stage:
            transitions += 1
        prev_stage = stage

    total = sum(stage_minutes.values())
    if total < 30:
        return None

    sleep_min = total - stage_minutes["awake"]
    return {
        "deep_min": stage_minutes["deep"],
        "light_min": stage_minutes["light"],
        "rem_min": stage_minutes["rem"],
        "awake_min": stage_minutes["awake"],
        "total_min": total,
        "transitions": transitions,
        "sleep_efficiency": round(sleep_min / total * 100, 1) if total > 0 else None,
    }


def extract_hr_max(gb: sqlite3.Connection, local_date: str) -> float | None:
    """HR massimo giornaliero da HUAMI_HEART_RATE_MAX_SAMPLE."""
    start, end = _day_window_ms(local_date)
    row = gb.execute(
        "SELECT HEART_RATE FROM HUAMI_HEART_RATE_MAX_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? ORDER BY HEART_RATE DESC LIMIT 1",
        (start, end),
    ).fetchone()
    v = row[0] if row else None
    return float(v) if v and 50 <= v <= 220 else None


def extract_pai(gb: sqlite3.Connection, local_date: str) -> dict | None:
    """Estrae PAI giornaliero da HUAMI_PAI_SAMPLE.

    Ritorna un dict con pai_today (attività del giorno) e zone di intensità,
    invece del solo PAI_TOTAL (accumulato storico - semanticamente sbagliato
    per il modello giornaliero).
    """
    start, end = _day_window_ms(local_date)
    row = gb.execute(
        "SELECT PAI_TODAY, PAI_LOW, PAI_MODERATE, PAI_HIGH, "
        "TIME_LOW, TIME_MODERATE, TIME_HIGH, PAI_TOTAL "
        "FROM HUAMI_PAI_SAMPLE "
        "WHERE TIMESTAMP BETWEEN ? AND ? ORDER BY TIMESTAMP DESC LIMIT 1",
        (start, end),
    ).fetchone()
    if not row or row[0] is None:
        return None
    pai_today = float(row[0])
    if pai_today < 0:
        return None
    return {
        "pai_today":      pai_today,
        "pai_low":        float(row[1]) if row[1] else 0.0,
        "pai_moderate":   float(row[2]) if row[2] else 0.0,
        "pai_high":       float(row[3]) if row[3] else 0.0,
        "time_low_min":   int(row[4])   if row[4] else 0,
        "time_mod_min":   int(row[5])   if row[5] else 0,
        "time_high_min":  int(row[6])   if row[6] else 0,
        "pai_total_acc":  float(row[7]) if row[7] else 0.0,  # storico, solo per riferimento
    }


# ── Gadgetbridge DB helper ────────────────────────────────────────────────────

def open_gb(path: str) -> sqlite3.Connection:
    gb = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    gb.row_factory = sqlite3.Row
    return gb


# ── Main ingestion ────────────────────────────────────────────────────────────

def run(gb_path: str, local_date: str, dry_run: bool = False) -> None:
    gb = open_gb(gb_path)

    # Verify device
    row = gb.execute("SELECT name FROM DEVICE LIMIT 1").fetchone()
    device_name = row[0] if row else "unknown"
    info(SCRIPT, f"Gadgetbridge device: {device_name}")

    signals: dict[str, tuple[float | None, str, dict]] = {}

    # ── RHR ───────────────────────────────────────────────────────────────────
    rhr = extract_rhr(gb, local_date)
    if rhr is not None:
        signals["rhr"] = (rhr, "bpm", {"source": "HUAMI_HEART_RATE_RESTING_SAMPLE"})
        info(SCRIPT, f"RHR = {rhr} bpm")
    else:
        warn(SCRIPT, "no RHR for this date")

    # ── HRV + sleep timing ────────────────────────────────────────────────────
    hrv_sleep = extract_hrv_and_sleep(gb, local_date)
    if hrv_sleep:
        if "hrv_rmssd" in hrv_sleep:
            signals["hrv_rmssd"] = (hrv_sleep["hrv_rmssd"], "ms",
                                    {"source": "GENERIC_HRV_VALUE_SAMPLE"})
            info(SCRIPT, f"HRV RMSSD = {hrv_sleep['hrv_rmssd']} ms")
        if "sleep_total_min" in hrv_sleep:
            signals["sleep_total_min"] = (hrv_sleep["sleep_total_min"], "min", {})
            onset_dt = datetime.fromtimestamp(hrv_sleep["sleep_onset_ts"],
                                              tz=timezone(timedelta(hours=_ITALY_OFFSET_H)))
            offset_dt = datetime.fromtimestamp(hrv_sleep["sleep_offset_ts"],
                                               tz=timezone(timedelta(hours=_ITALY_OFFSET_H)))
            info(SCRIPT,
                 f"sleep {onset_dt.strftime('%H:%M')} → {offset_dt.strftime('%H:%M')} "
                 f"({hrv_sleep['sleep_total_min']:.0f} min)")
        if "sleep_onset_ts" in hrv_sleep:
            signals["sleep_onset_ts"] = (hrv_sleep["sleep_onset_ts"], "ts", {})
        if "sleep_offset_ts" in hrv_sleep:
            signals["sleep_offset_ts"] = (hrv_sleep["sleep_offset_ts"], "ts", {})
    else:
        warn(SCRIPT, "no HRV/sleep data for this night")

    # ── Stress ────────────────────────────────────────────────────────────────
    stress = extract_stress_avg(gb, local_date)
    if stress is not None:
        signals["stress_avg"] = (stress, "score", {"source": "HUAMI_STRESS_SAMPLE"})
        info(SCRIPT, f"stress avg = {stress}")
    else:
        warn(SCRIPT, "no stress data for this date")

    # ── SpO2 ──────────────────────────────────────────────────────────────────
    spo2 = extract_spo2(gb, local_date)
    if spo2 is not None:
        signals["spo2"] = (spo2, "%", {"source": "HUAMI_SPO2_SAMPLE"})
        info(SCRIPT, f"SpO2 avg (night) = {spo2}%")
    else:
        warn(SCRIPT, "no SpO2 data for this night")

    # ── PAI ───────────────────────────────────────────────────────────────────
    pai = extract_pai(gb, local_date)
    if pai is not None:
        signals["pai_score"] = (
            pai["pai_today"],
            "score",
            {"source": "HUAMI_PAI_SAMPLE", **pai},
        )
        info(SCRIPT,
             f"PAI today={pai['pai_today']:.3f} "
             f"(low={pai['pai_low']:.2f} mod={pai['pai_moderate']:.2f} "
             f"high={pai['pai_high']:.2f})")
    else:
        warn(SCRIPT, "no PAI data for this date")

    # ── Respiratory rate ──────────────────────────────────────────────────────
    try:
        rr = extract_respiratory_rate(gb, local_date)
        if rr is not None:
            signals["sleep_rr"] = (rr, "breaths/min",
                                   {"source": "HUAMI_SLEEP_RESPIRATORY_RATE_SAMPLE"})
            info(SCRIPT, f"sleep RR = {rr} breaths/min")
    except Exception as exc:
        warn(SCRIPT, f"respiratory rate extraction failed: {exc}")

    # ── Skin temperature ──────────────────────────────────────────────────────
    try:
        skin_temp = extract_skin_temperature(gb, local_date)
        if skin_temp is not None:
            signals["skin_temp"] = (skin_temp, "°C",
                                    {"source": "GENERIC_TEMPERATURE_SAMPLE"})
            info(SCRIPT, f"skin temp = {skin_temp}°C")
    except Exception as exc:
        warn(SCRIPT, f"skin temperature extraction failed: {exc}")

    # ── HR continuo ───────────────────────────────────────────────────────────
    try:
        hr_cont = extract_hr_continuous(gb, local_date)
        if hr_cont is not None:
            signals["hr_continuous"] = (hr_cont["hr_mean"], "bpm",
                                        {"source": "GENERIC_HEART_RATE_SAMPLE", **hr_cont})
            info(SCRIPT,
                 f"HR continuo: mean={hr_cont['hr_mean']} std={hr_cont['hr_std']} "
                 f"n={hr_cont['hr_samples']}")
    except Exception as exc:
        warn(SCRIPT, f"HR continuous extraction failed: {exc}")

    # ── Sleep stages dettagliati ──────────────────────────────────────────────
    try:
        stages = extract_sleep_stages_detail(gb, local_date)
        if stages is not None:
            signals["sleep_stages_deep_min"]   = (stages["deep_min"],  "min",   stages)
            signals["sleep_stages_rem_min"]    = (stages["rem_min"],   "min",   stages)
            signals["sleep_stages_efficiency"] = (stages["sleep_efficiency"], "%", stages)
            info(SCRIPT,
                 f"sleep stages: deep={stages['deep_min']}min rem={stages['rem_min']}min "
                 f"eff={stages['sleep_efficiency']}%")
    except Exception as exc:
        warn(SCRIPT, f"sleep stages extraction failed: {exc}")

    # ── HR massimo giornaliero ────────────────────────────────────────────────
    try:
        hr_max = extract_hr_max(gb, local_date)
        if hr_max is not None:
            signals["hr_max_daily"] = (hr_max, "bpm",
                                       {"source": "HUAMI_HEART_RATE_MAX_SAMPLE"})
            info(SCRIPT, f"HR max = {hr_max} bpm")
    except Exception as exc:
        warn(SCRIPT, f"HR max extraction failed: {exc}")

    gb.close()

    if not signals:
        warn(SCRIPT, "no signals extracted - nothing to write")
        return

    info(SCRIPT, f"signals: {sorted(signals.keys())}")

    db = get_db()
    store_all(db, local_date, signals, dry_run=dry_run)
    n_obs = derive_observations(db, local_date, dry_run=dry_run)
    db.close()

    info(SCRIPT, f"done: {len(signals)} signals, {n_obs} observations")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest Gadgetbridge export into soulkiller biofeedback"
    )
    parser.add_argument("--db", required=True,
                        help="Path to Gadgetbridge SQLite DB file")
    parser.add_argument("--date", default=None,
                        help="Local Italy date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print signals without writing to DB")
    args = parser.parse_args()

    local_date = args.date or date.today().isoformat()

    if not Path(args.db).exists():
        print(f"ERROR: {args.db} not found", file=sys.stderr)
        sys.exit(1)

    info(SCRIPT, f"parsing {args.db} for date {local_date}")
    run(args.db, local_date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
