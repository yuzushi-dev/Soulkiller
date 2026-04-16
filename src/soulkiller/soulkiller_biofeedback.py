#!/usr/bin/env python3
"""Soulkiller Biofeedback Ingestion - Amazfit Helio Ring via Zepp API.

Pulls nightly health data (sleep stages, HRV, RHR, stress, SpO2, PAI) and
converts readings into soulkiller personality observations (IMP-20–28).

Cron: soulkiller:biofeedback-pull, daily 04:05 Europe/Rome

Usage:
  python3 soulkiller_biofeedback.py [--date YYYY-MM-DD] [--dry-run]
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sqlite3
import struct
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from huami_token.zepp import ZeppSession

from lib.log import info, warn, error

SCRIPT = "soulkiller_biofeedback"

def _data_dir() -> Path:
    env = os.environ.get("SOULKILLER_DATA_DIR", "")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "runtime"

DB_PATH = _data_dir() / "soulkiller.db"
CREDS_PATH = _data_dir() / "biofeedback.credentials.json"

# Zepp account credentials must be provided explicitly in the OSS repo.
EMAIL = os.environ.get("SOULKILLER_ZEPP_EMAIL", "")
PASSWORD = os.environ.get("SOULKILLER_ZEPP_PASSWORD", "")

# ── IMP mappings ─────────────────────────────────────────────────────────────

FACET_MAP: dict[str, str | None] = {
    "hrv_rmssd":       "emotional.resilience_pattern",
    "sleep_deep_pct":  "emotional.distress_tolerance",
    "sleep_rem_pct":   "emotional.emotion_clarity",
    "sleep_onset_ts":  "temporal.planning_horizon",
    "sleep_score":     "emotional.distress_tolerance",   # corroboration signal
    "pai_score":       "temporal.routine_attachment",
    "stress_avg":      "temporal.impulsivity_regulation",
    "rhr":             None,   # IMP-23: feeds allostatic_load_index (stress index), not a direct facet obs
    "spo2":            None,   # IMP-28: model validity flag only; apnea_flag_hypothesis if < 90 for 3 nights
    # non-observation signals (stored but not mapped to facets)
    "sleep_deep_min":  None,
    "sleep_light_min": None,
    "sleep_rem_min":   None,
    "sleep_total_min": None,
    "sleep_offset_ts": None,
    # ── Fase 1 expansion: Gadgetbridge extra signals ──────────────────────────
    "sleep_rr":              "emotional.stress_response",      # RR elevato = stress/apnea
    "skin_temp":             None,                             # stored for circadian tracking
    "hr_continuous":         None,                             # stored for computed signals
    "hr_max_daily":          None,                             # stored for activation patterns
    "sleep_stages_deep_min": None,                             # stored; used via computed
    "sleep_stages_rem_min":  None,                             # stored; used via computed
    "sleep_stages_efficiency": "emotional.distress_tolerance", # efficienza sonno (da GB stages)
    # ── Computed signals (calcolati da derive_computed_signals) ───────────────
    "sleep_efficiency":      "emotional.distress_tolerance",   # efficienza sonno (da Zepp)
    "circadian_regularity":  "temporal.routine_attachment",    # regolarità ritmo circadiano
    "recovery_score":        "emotional.resilience_pattern",   # HRV vs baseline 14gg
    "hr_reactivity":         "emotional.emotional_expression", # std HR diurna
    "activity_consistency":  "temporal.routine_attachment",    # CV PAI su 7gg (corroboration)
    # ── Sprint 2+3: Muse 2 EEG signals ───────────────────────────────────────
    "eeg_focus_score":       "cognitive.analytical_approach",  # beta-driven focus 0-100
    "eeg_calm_score":        "emotional.distress_tolerance",   # alpha-driven calm 0-100
    "eeg_theta_beta_ratio":  "cognitive.decision_speed",       # low = decisive, high = deliberate
    "eeg_frontal_asymmetry": "emotional.emotional_expression", # log(AF8/AF7) approach vs withdrawal
    "eeg_engagement":        "cognitive.information_gathering",# beta/(alpha+theta) engagement index
    "eeg_alpha_variability": "meta_cognition.reflection_habit",# std alpha → reflective fluctuation
    "eeg_meditation_depth":  "emotional.resilience_pattern",   # calm score from rest sessions
    "eeg_cognitive_load":    None,                             # theta/beta ratio (stored, no obs)
}

# Thresholds for position normalisation
HRV_LOW, HRV_HIGH = 20.0, 60.0
STRESS_LOW, STRESS_HIGH = 30.0, 70.0

# ── DB ────────────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


# ── Auth ──────────────────────────────────────────────────────────────────────

def _zepp_login(email: str, password: str) -> dict:
    """Authenticate via huami-token ZeppSession. Returns {"app_token": ..., "user_id": ...}."""
    session = ZeppSession(email, password)
    session.login()
    app_token = session._app_token
    user_id = str(session._user_id)
    if not app_token or not user_id:
        raise RuntimeError("ZeppSession.login() returned no app_token/user_id")
    info(SCRIPT, f"auth ok user_id={user_id}")
    return {"app_token": app_token, "user_id": user_id}


def load_token() -> dict:
    """Return cached token or re-authenticate."""
    if CREDS_PATH.exists():
        data = json.loads(CREDS_PATH.read_text())
        obtained_str = data.get("obtained_at", "2000-01-01T00:00:00+00:00")
        try:
            obtained = datetime.fromisoformat(obtained_str)
            if obtained.tzinfo is None:
                obtained = obtained.replace(tzinfo=timezone.utc)
        except ValueError:
            obtained = datetime.min.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - obtained).days
        if False:  # always re-authenticate; Zepp tokens expire unpredictably
            info(SCRIPT, f"using cached token (age {age_days}d)")
            return data
    info(SCRIPT, "obtaining fresh token via Zepp auth")
    token_data = _zepp_login(EMAIL, PASSWORD)
    token_data["obtained_at"] = datetime.now(timezone.utc).isoformat()
    CREDS_PATH.write_text(json.dumps(token_data, indent=2))
    CREDS_PATH.chmod(0o600)
    return token_data


# ── API helpers ───────────────────────────────────────────────────────────────

def _zepp_get(url: str, params: dict, app_token: str) -> dict:
    r = requests.get(
        url,
        params=params,
        headers={"apptoken": app_token},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


# ── Band data: sleep + heart rate ─────────────────────────────────────────────

def pull_band_data(date_str: str, app_token: str, user_id: str) -> dict:
    """Fetch band_data for a single date. Returns raw parsed blobs."""
    raw = _zepp_get(
        "https://api-mifit-de2.zepp.com/v1/data/band_data.json",
        {
            "query_type": "detail",
            "device_type": "android_phone",
            "userid": user_id,
            "from_date": date_str,
            "to_date": date_str,
        },
        app_token,
    )
    result: dict = {}
    for item in raw.get("data", []):
        if item.get("date_time") != date_str:
            continue
        summary_b64 = item.get("summary")
        if summary_b64:
            try:
                full_summary = json.loads(base64.b64decode(summary_b64).decode())
                # Sleep data is nested under 'slp' key
                result["sleep_raw"] = full_summary.get("slp", {})
                result["full_summary"] = full_summary
            except Exception as exc:
                warn(SCRIPT, f"summary decode error: {exc}")
        # data_hr: 1440 uint8 bytes, 254/255 = no reading
        data_hr = item.get("data_hr")
        if data_hr:
            result["data_hr_b64"] = data_hr
    return result


def decode_sleep(slp: dict) -> dict:
    """Extract sleep metrics from the 'slp' sub-dict of band_data summary."""
    deep_min = int(slp.get("dp", 0) or 0)
    light_min = int(slp.get("lt", 0) or 0)
    total_min = deep_min + light_min
    onset_ts = slp.get("st")     # unix seconds; 0 or negative = no data
    offset_ts = slp.get("ed")    # unix seconds
    rhr = int(slp.get("rhr", 0) or 0)
    sleep_score = int(slp.get("ss", 0) or 0)

    # Validate timestamps (negative or 0 means no data)
    valid_onset = float(onset_ts) if onset_ts and onset_ts > 0 else None
    valid_offset = float(offset_ts) if offset_ts and offset_ts > 0 else None

    # REM stages from stage array (mode 8 = REM); many ring firmware skip this
    rem_min = 0
    for stage in slp.get("stage", []):
        if isinstance(stage, dict) and stage.get("mode") == 8:
            rem_min += int(stage.get("stop", 0)) - int(stage.get("start", 0))

    return {
        "sleep_deep_min": deep_min if total_min > 0 else None,
        "sleep_rem_min": rem_min if total_min > 0 else None,
        "sleep_light_min": light_min if total_min > 0 else None,
        "sleep_total_min": total_min if total_min > 0 else None,
        "sleep_onset_ts": valid_onset,
        "sleep_offset_ts": valid_offset,
        "sleep_score": float(sleep_score) if sleep_score > 0 else None,
        "sleep_deep_pct": round(deep_min / total_min * 100, 1) if total_min > 0 else None,
        "sleep_rem_pct": round(rem_min / total_min * 100, 1) if (total_min > 0 and rem_min > 0) else None,
    }


def decode_heartrate(data_hr_b64: str) -> dict:
    """Decode 1440-byte base64 HR blob (uint8, 254/255 = no reading)."""
    try:
        raw = base64.b64decode(data_hr_b64)
        values = list(raw)  # uint8
        valid = [v for v in values if 30 <= v <= 200]
    except Exception as exc:
        warn(SCRIPT, f"HR decode error: {exc}")
        return {}
    if not valid:
        return {}
    return {
        "rhr": float(min(valid)),
        "hr_mean": round(sum(valid) / len(valid), 1),
        "hr_max": float(max(valid)),
        "hr_sample_count": len(valid),
    }


# ── Events ────────────────────────────────────────────────────────────────────

def pull_events(event_type: str, date_str: str, app_token: str, user_id: str) -> list[dict]:
    try:
        raw = _zepp_get(
            f"https://api-mifit.zepp.com/users/{user_id}/events",
            {"eventType": event_type, "from_date": date_str, "to_date": date_str},
            app_token,
        )
        return raw.get("data", [])
    except Exception as exc:
        warn(SCRIPT, f"events/{event_type} failed: {exc}")
        return []


def _unwrap_data(ev: dict) -> dict:
    d = ev.get("data") or {}
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except Exception:
            d = {}
    return d


def parse_stress(events: list[dict]) -> dict | None:
    for ev in events:
        d = _unwrap_data(ev)
        avg = d.get("avgStress") or ev.get("avgStress")
        if avg is not None:
            return {
                "stress_avg": float(avg),
                "stress_min": d.get("minStress") or ev.get("minStress"),
                "stress_max": d.get("maxStress") or ev.get("maxStress"),
            }
    return None


def parse_spo2(events: list[dict]) -> dict | None:
    for ev in events:
        val = (ev.get("value") or ev.get("spo2")
               or _unwrap_data(ev).get("value") or _unwrap_data(ev).get("spo2"))
        if val is not None:
            return {"spo2": float(val)}
    return None


def parse_pai(events: list[dict]) -> dict | None:
    for ev in events:
        d = _unwrap_data(ev)
        score = (ev.get("dailyPai") or ev.get("score") or ev.get("value")
                 or d.get("dailyPai") or d.get("score"))
        rhr = ev.get("restingHeartRate") or d.get("restingHeartRate")
        if score is not None:
            return {"pai_score": float(score), "rhr_from_pai": float(rhr) if rhr else None}
    return None


def parse_hrv(events: list[dict]) -> dict | None:
    for ev in events:
        d = _unwrap_data(ev)
        val = (ev.get("rmssd") or ev.get("hrv") or ev.get("value")
               or d.get("rmssd") or d.get("hrv") or d.get("value"))
        if val is not None:
            return {"hrv_rmssd": float(val)}
    return None


# ── Storage ───────────────────────────────────────────────────────────────────

def store_reading(db: sqlite3.Connection, date_str: str, signal_type: str,
                  value: float | None, unit: str, metadata: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        """INSERT INTO biofeedback_readings
           (date, signal_type, value, unit, metadata_json, pulled_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(date, signal_type) DO UPDATE SET
             value=excluded.value,
             metadata_json=excluded.metadata_json,
             pulled_at=excluded.pulled_at""",
        (date_str, signal_type, value, unit, json.dumps(metadata), now),
    )


def store_all(db: sqlite3.Connection, date_str: str,
              signals: dict[str, tuple[float | None, str, dict]],
              dry_run: bool = False) -> None:
    for signal_type, (value, unit, meta) in signals.items():
        if dry_run:
            info(SCRIPT, f"[DRY] {date_str} {signal_type}={value} {unit}")
        else:
            store_reading(db, date_str, signal_type, value, unit, meta)
    if not dry_run:
        db.commit()


# ── IMP observation derivation ────────────────────────────────────────────────

def _clamp(v: float) -> float:
    return min(1.0, max(-1.0, v))


def derive_observations(db: sqlite3.Connection, date_str: str,
                        dry_run: bool = False) -> int:
    rows = db.execute(
        "SELECT signal_type, value FROM biofeedback_readings WHERE date=?",
        (date_str,),
    ).fetchall()

    count = 0
    for row in rows:
        stype = row["signal_type"]
        value = row["value"]
        facet_id = FACET_MAP.get(stype)
        if facet_id is None or value is None:
            continue

        if stype == "hrv_rmssd":
            pos = _clamp((value - HRV_LOW) / (HRV_HIGH - HRV_LOW) * 2 - 1)
            strength = 0.78
            content = f"HRV RMSSD={value:.1f}ms ({date_str}) - biofeedback"
        elif stype == "sleep_deep_pct":
            pos = _clamp((value - 10) / 20 * 2 - 1)
            strength = 0.72
            content = f"Deep sleep {value:.1f}% ({date_str}) - biofeedback"
        elif stype == "sleep_rem_pct":
            pos = _clamp((value - 10) / 20 * 2 - 1)
            strength = 0.72
            content = f"REM sleep {value:.1f}% ({date_str}) - biofeedback"
        elif stype == "pai_score":
            pos = _clamp((value - 50) / 50)
            strength = 0.65
            content = f"PAI score={value:.0f} ({date_str}) - biofeedback"
        elif stype == "stress_avg":
            # high stress → low impulsivity regulation (negative pole)
            pos = _clamp((STRESS_HIGH - value) / (STRESS_HIGH - STRESS_LOW) * 2 - 1)
            strength = 0.68
            content = f"Avg stress={value:.0f} ({date_str}) - biofeedback"
        elif stype == "sleep_onset_ts":
            # proximity to 23:00 local = more structured chronotype
            # wrap 00:00-05:59 → 24-29 so midnight-crossers aren't penalised
            onset_hour = (datetime.fromtimestamp(value).hour
                          + datetime.fromtimestamp(value).minute / 60)
            if onset_hour < 6:
                onset_hour += 24
            deviation = abs(onset_hour - 23.0)
            pos = _clamp(1.0 - deviation / 3.0)
            strength = 0.60
            content = (f"Sleep onset {datetime.fromtimestamp(value).strftime('%H:%M')}"
                       f" ({date_str}) - biofeedback")
        elif stype == "sleep_score":
            # Zepp sleep score 0-100; ≥80 = good recovery
            pos = _clamp((value - 50) / 40)
            strength = 0.65
            content = f"Sleep score={value:.0f}/100 ({date_str}) - biofeedback"
        # ── Fase 1 expansion ─────────────────────────────────────────────────
        elif stype == "sleep_rr":
            # RR notturna: 15 breaths/min = normale; bassa = calma, alta = stress/apnea
            # polo positivo = bassa RR (calmo), polo negativo = alta RR (stress)
            pos = _clamp((15.0 - value) / 5.0)
            strength = 0.65
            content = f"Sleep respiratory rate={value:.1f} br/min ({date_str}) - biofeedback"
        elif stype == "sleep_stages_efficiency":
            # efficienza sonno 0-100%: ≥85% = buona, <70% = problematica
            pos = _clamp((value - 75.0) / 15.0)
            strength = 0.70
            content = f"Sleep efficiency (GB)={value:.1f}% ({date_str}) - biofeedback"
        # ── Computed signals ──────────────────────────────────────────────────
        elif stype == "sleep_efficiency":
            # identica logica a sleep_stages_efficiency
            pos = _clamp((value - 75.0) / 15.0)
            strength = 0.68
            content = f"Sleep efficiency={value:.1f}% ({date_str}) - computed"
        elif stype == "circadian_regularity":
            # score 0-1: 1 = perfettamente regolare, 0 = caotico
            pos = _clamp(value * 2.0 - 1.0)
            strength = 0.65
            content = f"Circadian regularity={value:.3f} ({date_str}) - computed"
        elif stype == "recovery_score":
            # ratio rispetto al baseline: +0.4 = +40% HRV vs baseline (ottima)
            # -0.4 = -40% (scarsa); range tipico [-0.5, +0.5]
            pos = _clamp(value / 0.4)
            strength = 0.72
            content = f"HRV recovery score={value:+.3f} vs baseline ({date_str}) - computed"
        elif stype == "hr_reactivity":
            # std HR diurna in bpm; bassa (<8) = molto stabile, alta (>20) = molto reattivo
            # polo positivo = alta reattività (espressivo), polo negativo = piatto (contenuto)
            pos = _clamp((value - 12.0) / 8.0)
            strength = 0.60
            content = f"HR reactivity (std)={value:.1f} bpm ({date_str}) - computed"
        elif stype == "activity_consistency":
            # score 0-1: 1 = PAI costante, 0 = PAI molto variabile
            pos = _clamp(value * 2.0 - 1.0)
            strength = 0.60
            content = f"Activity consistency={value:.3f} ({date_str}) - computed"
        # ── Sprint 2+3: Muse 2 EEG ───────────────────────────────────────────
        elif stype == "eeg_focus_score":
            # 0-100; 50=neutral → 0.0; 100=high focus → +1.0
            pos = _clamp((value - 50.0) / 50.0)
            strength = 0.72
            content = f"EEG focus score={value:.1f}/100 ({date_str}) - muse"
        elif stype == "eeg_calm_score":
            pos = _clamp((value - 50.0) / 50.0)
            strength = 0.70
            content = f"EEG calm score={value:.1f}/100 ({date_str}) - muse"
        elif stype == "eeg_theta_beta_ratio":
            # low ratio = decisive/fast; neutral ≈ 2.0; high = cognitive load
            pos = _clamp((2.0 - value) / 1.5)
            strength = 0.65
            content = f"EEG theta/beta ratio={value:.2f} ({date_str}) - muse"
        elif stype == "eeg_frontal_asymmetry":
            # log ratio: ±0.3 typical range → maps to ±1.0
            pos = _clamp(value / 0.3)
            strength = 0.70
            content = f"EEG frontal asymmetry={value:+.3f} ({date_str}) - muse"
        elif stype == "eeg_engagement":
            # engagement index; neutral=1.0; range 0-3
            pos = _clamp((value - 1.0) / 1.0)
            strength = 0.65
            content = f"EEG engagement index={value:.2f} ({date_str}) - muse"
        elif stype == "eeg_alpha_variability":
            # std of alpha 0-0.2; low=stable, moderate(0.05-0.15)=reflective
            pos = _clamp((value - 0.05) / 0.10)
            strength = 0.60
            content = f"EEG alpha variability={value:.3f} ({date_str}) - muse"
        elif stype == "eeg_meditation_depth":
            pos = _clamp((value - 50.0) / 50.0)
            strength = 0.68
            content = f"EEG meditation depth={value:.1f}/100 ({date_str}) - muse"
        else:
            continue

        source_ref = f"biofeedback:{date_str}:{stype}"
        signal_strength_with_boost = min(1.0, strength + 0.08)  # +0.08 per whitepaper IMP-28

        if dry_run:
            info(SCRIPT, f"[DRY] obs {facet_id} pos={pos:.2f} str={signal_strength_with_boost}: {content}")
            count += 1
            continue

        try:
            db.execute(
                """INSERT INTO observations
                   (facet_id, source_type, source_ref, content, signal_strength,
                    signal_position, created_at)
                   VALUES (?, 'biofeedback', ?, ?, ?, ?, ?)
                   ON CONFLICT(facet_id, source_ref) DO UPDATE SET
                     signal_strength=excluded.signal_strength,
                     signal_position=excluded.signal_position,
                     content=excluded.content""",
                (facet_id, source_ref, content, signal_strength_with_boost, pos,
                 datetime.now(timezone.utc).isoformat()),
            )
            count += 1
        except sqlite3.IntegrityError as exc:
            warn(SCRIPT, f"obs insert skipped for {facet_id}: {exc}")

    if not dry_run:
        db.commit()
    return count


# ── Computed signals (multi-day derivation) ───────────────────────────────────

def derive_computed_signals(db: sqlite3.Connection, date_str: str,
                            dry_run: bool = False) -> int:
    """Calcola segnali derivati da finestre temporali multi-giorno.

    Produce: circadian_regularity, recovery_score, sleep_efficiency,
             hr_reactivity, activity_consistency.
    Salva in biofeedback_readings con source implicita nei metadata.
    Ritorna il numero di segnali calcolati.
    """
    import statistics as _stats

    count = 0

    def _store(signal_type: str, value: float, unit: str, meta: dict) -> None:
        if dry_run:
            info(SCRIPT, f"[DRY computed] {date_str} {signal_type}={value} {unit}")
        else:
            store_reading(db, date_str, signal_type, value, unit, meta)

    # ── Circadian regularity: std(sleep_onset_ts) su 7gg precedenti ──────────
    onset_rows = db.execute(
        """SELECT value FROM biofeedback_readings
           WHERE signal_type='sleep_onset_ts' AND date < ? AND value IS NOT NULL
           ORDER BY date DESC LIMIT 7""",
        (date_str,),
    ).fetchall()
    if len(onset_rows) >= 3:
        onset_hours = []
        for r in onset_rows:
            from datetime import datetime as _dt
            h = _dt.fromtimestamp(r[0]).hour + _dt.fromtimestamp(r[0]).minute / 60
            if h < 6:
                h += 24  # mezzanotte-5:59 → 24-29 per evitare discontinuità
            onset_hours.append(h)
        std_h = _stats.stdev(onset_hours)
        regularity = max(0.0, 1.0 - std_h / 2.0)  # std=0 → 1.0; std≥2h → 0.0
        _store("circadian_regularity", round(regularity, 3), "score",
               {"window_days": len(onset_rows), "onset_hours": onset_hours,
                "std_hours": round(std_h, 3)})
        count += 1

    # ── Recovery score: HRV oggi vs mediana baseline 14gg ────────────────────
    today_hrv_row = db.execute(
        "SELECT value FROM biofeedback_readings WHERE signal_type='hrv_rmssd' AND date=?",
        (date_str,),
    ).fetchone()
    if today_hrv_row and today_hrv_row[0]:
        baseline_rows = db.execute(
            """SELECT value FROM biofeedback_readings
               WHERE signal_type='hrv_rmssd' AND date < ? AND value IS NOT NULL
               ORDER BY date DESC LIMIT 14""",
            (date_str,),
        ).fetchall()
        if len(baseline_rows) >= 3:
            baseline_hrv = _stats.median([r[0] for r in baseline_rows])
            if baseline_hrv > 0:
                recovery = (today_hrv_row[0] - baseline_hrv) / baseline_hrv
                _store("recovery_score", round(recovery, 4), "ratio",
                       {"today_hrv": today_hrv_row[0], "baseline_hrv": round(baseline_hrv, 1),
                        "baseline_days": len(baseline_rows)})
                count += 1

    # ── Sleep efficiency: sleep_total_min / time_in_bed ──────────────────────
    onset_r  = db.execute(
        "SELECT value FROM biofeedback_readings WHERE signal_type='sleep_onset_ts' AND date=?",
        (date_str,),
    ).fetchone()
    offset_r = db.execute(
        "SELECT value FROM biofeedback_readings WHERE signal_type='sleep_offset_ts' AND date=?",
        (date_str,),
    ).fetchone()
    total_r  = db.execute(
        "SELECT value FROM biofeedback_readings WHERE signal_type='sleep_total_min' AND date=?",
        (date_str,),
    ).fetchone()
    if onset_r and offset_r and total_r and onset_r[0] and offset_r[0] and total_r[0]:
        time_in_bed_min = (offset_r[0] - onset_r[0]) / 60.0
        if time_in_bed_min >= 30:
            efficiency = min(100.0, total_r[0] / time_in_bed_min * 100.0)
            _store("sleep_efficiency", round(efficiency, 1), "%",
                   {"sleep_total_min": total_r[0],
                    "time_in_bed_min": round(time_in_bed_min, 1)})
            count += 1

    # ── HR reactivity: std della HR continua (da metadata) ───────────────────
    hr_cont_r = db.execute(
        "SELECT metadata_json FROM biofeedback_readings "
        "WHERE signal_type='hr_continuous' AND date=?",
        (date_str,),
    ).fetchone()
    if hr_cont_r and hr_cont_r[0]:
        meta = json.loads(hr_cont_r[0])
        hr_std = meta.get("hr_std")
        if hr_std is not None:
            _store("hr_reactivity", hr_std, "bpm_std", meta)
            count += 1

    # ── Activity consistency: CV del PAI su 7gg ──────────────────────────────
    pai_rows = db.execute(
        """SELECT value FROM biofeedback_readings
           WHERE signal_type='pai_score' AND date <= ? AND value IS NOT NULL
           ORDER BY date DESC LIMIT 7""",
        (date_str,),
    ).fetchall()
    if len(pai_rows) >= 3:
        pai_vals = [r[0] for r in pai_rows]
        mean_pai = _stats.mean(pai_vals)
        if mean_pai > 0:
            cv = _stats.stdev(pai_vals) / mean_pai
            consistency = max(0.0, 1.0 - cv)
            _store("activity_consistency", round(consistency, 4), "score",
                   {"pai_values": pai_vals, "cv": round(cv, 4),
                    "window_days": len(pai_vals)})
            count += 1

    if not dry_run:
        db.commit()
    return count


# ── Main pull ─────────────────────────────────────────────────────────────────

def run(date_str: str, dry_run: bool = False) -> None:
    creds = load_token()
    app_token = creds["app_token"]
    user_id = creds["user_id"]

    signals: dict[str, tuple[float | None, str, dict]] = {}

    # ── Band data: sleep + HR ──────────────────────────────────────────────────
    try:
        band = pull_band_data(date_str, app_token, user_id)
    except Exception as exc:
        warn(SCRIPT, f"band_data pull failed: {exc}")
        band = {}

    if "sleep_raw" in band:
        sleep = decode_sleep(band["sleep_raw"])
        total = sleep.get("sleep_total_min")
        if total and total > 0:
            for k, v in sleep.items():
                if v is not None:
                    unit = "%" if "pct" in k else ("ts" if "ts" in k else ("score" if "score" in k else "min"))
                    signals[k] = (float(v), unit, sleep)
            info(SCRIPT,
                 f"sleep: deep={sleep.get('sleep_deep_pct')}% "
                 f"rem={sleep.get('sleep_rem_pct')}% "
                 f"total={total}min score={sleep.get('sleep_score')}")
        else:
            warn(SCRIPT, "sleep data present but total=0 (ring not worn / not synced yet)")
    else:
        warn(SCRIPT, "no band_data for this date")

    if "data_hr_b64" in band:
        hr = decode_heartrate(band["data_hr_b64"])
        if hr:
            signals["rhr"] = (hr["rhr"], "bpm", hr)
            info(SCRIPT, f"HR: rhr={hr['rhr']} mean={hr['hr_mean']}")

    # ── Events ─────────────────────────────────────────────────────────────────
    stress = parse_stress(pull_events("all_day_stress", date_str, app_token, user_id))
    if stress:
        signals["stress_avg"] = (stress["stress_avg"], "score", stress)
        info(SCRIPT, f"stress: avg={stress['stress_avg']}")

    spo2 = parse_spo2(pull_events("blood_oxygen", date_str, app_token, user_id))
    if spo2:
        signals["spo2"] = (spo2["spo2"], "%", spo2)
        info(SCRIPT, f"spo2={spo2['spo2']}%")

    pai = parse_pai(pull_events("PaiHealthInfo", date_str, app_token, user_id))
    if pai:
        signals["pai_score"] = (pai["pai_score"], "score", pai)
        if pai.get("rhr_from_pai") and "rhr" not in signals:
            signals["rhr"] = (pai["rhr_from_pai"], "bpm", {})
        info(SCRIPT, f"PAI={pai['pai_score']}")

    # HRV: specific to Helio Ring - endpoint may not exist, we try anyway
    hrv_events = pull_events("hrv", date_str, app_token, user_id)
    if not hrv_events:
        hrv_events = pull_events("heartRateVariability", date_str, app_token, user_id)
    hrv = parse_hrv(hrv_events)
    if hrv:
        signals["hrv_rmssd"] = (hrv["hrv_rmssd"], "ms", hrv)
        info(SCRIPT, f"HRV RMSSD={hrv['hrv_rmssd']}ms")
    else:
        warn(SCRIPT, "HRV not available via API for this date (Helio Ring may require different endpoint)")

    info(SCRIPT, f"signals collected: {sorted(signals.keys())}")

    db = get_db()
    store_all(db, date_str, signals, dry_run=dry_run)
    n_computed = derive_computed_signals(db, date_str, dry_run=dry_run)
    n_obs = derive_observations(db, date_str, dry_run=dry_run)
    db.close()

    info(SCRIPT,
         f"done: {len(signals)} signals stored, "
         f"{n_computed} computed signals, "
         f"{n_obs} observations derived")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Soulkiller Biofeedback Ingestion")
    parser.add_argument("--date", default=None,
                        help="Date to pull YYYY-MM-DD (default: yesterday)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be stored without writing to DB")
    args = parser.parse_args()

    date_str = args.date or (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")

    info(SCRIPT, f"pulling biofeedback for {date_str}")
    run(date_str, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
