#!/usr/bin/env python3
"""
Soulkiller Adaptive Layer

Four mechanisms adapted from Tem Anima (temm1e-labs/temm1e):

1. Confidence decay      — facets not observed recently lose confidence
                           using their per-category half_life_days
2. Adaptive N            — extraction interval grows when profile is stable,
                           shrinks when profile is volatile (delta-based)
3. Trust asymmetry       — relational.trust_formation degrades 3x faster than it builds
4. Relationship phase    — 4-state machine: Discovery → Calibration → Partnership → Deep Partnership

Design principle: pure functions for all logic that doesn't touch the DB,
so they're trivially testable. DB-touching functions are thin wrappers.

State file: workspace/memory/soulkiller_adaptive_state.json
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────────

CONFIDENCE_FLOOR = 0.05      # Minimum confidence — facet never fully disappears
TRUST_FACET = "relational.trust_formation"
TRUST_DAMPEN_RISE = 0.3      # Trust-building signals applied at 30% strength

INTERVAL_MIN_H = 2.0         # Never run less often than this
INTERVAL_MAX_H = 8.0         # Never wait longer than this
DELTA_HIGH = 0.10            # Above this → volatile profile, stay at minimum interval
DELTA_LOW  = 0.04            # Below this → stable profile, can grow toward maximum

# Relationship phase transition thresholds
PHASE_ORDER = ["Discovery", "Calibration", "Partnership", "Deep Partnership"]

_PHASE_CRITERIA = {
    # (current_phase) → conditions that must ALL be true to advance
    "Discovery": {
        "obs_count_min": 30,
        "high_conf_facets_min": 5,   # facets with confidence > 0.5
    },
    "Calibration": {
        "obs_count_min": 80,
        "avg_confidence_min": 0.60,
    },
    "Partnership": {
        "obs_count_min": 200,
        "trust_value_min": 0.65,
        "delta_max": 0.04,
    },
}


# ── State I/O ──────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return Path.home() / ".openclaw/workspace/memory/soulkiller_adaptive_state.json"


def _default_state() -> dict[str, Any]:
    return {
        "last_run_ts": None,
        "last_delta": 1.0,           # High on first run → no skip
        "next_interval_h": INTERVAL_MIN_H,
        "relationship_phase": "Discovery",
        "phase_changed_at": None,
        "eval_count": 0,
    }


def load_state(path: Path | None = None) -> dict[str, Any]:
    p = path or _state_path()
    defaults = _default_state()
    if not p.exists():
        return defaults
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        defaults.update(data)
        return defaults
    except (json.JSONDecodeError, OSError):
        return defaults


def save_state(state: dict[str, Any], path: Path | None = None) -> None:
    p = path or _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ── Adaptive N — pure functions ────────────────────────────────────────────────

def compute_next_interval(delta: float) -> float:
    """
    Returns the next extraction interval in hours based on profile delta.

    delta >= DELTA_HIGH (0.10) → INTERVAL_MIN_H (2h)   — volatile, extract often
    delta <= DELTA_LOW  (0.04) → INTERVAL_MAX_H (8h)   — stable, save LLM calls
    Between → linear interpolation
    """
    if delta >= DELTA_HIGH:
        return INTERVAL_MIN_H
    if delta <= DELTA_LOW:
        return INTERVAL_MAX_H
    # Linear interpolation between [DELTA_LOW, DELTA_HIGH] → [INTERVAL_MAX_H, INTERVAL_MIN_H]
    t = (delta - DELTA_LOW) / (DELTA_HIGH - DELTA_LOW)
    return INTERVAL_MAX_H + t * (INTERVAL_MIN_H - INTERVAL_MAX_H)


def should_skip_run(state: dict[str, Any], now: datetime | None = None) -> bool:
    """
    Returns True if not enough time has elapsed since the last run.
    Always returns False if last_run_ts is None (first run).
    """
    last_ts = state.get("last_run_ts")
    if not last_ts:
        return False
    next_interval_h = float(state.get("next_interval_h", INTERVAL_MIN_H))
    now = now or datetime.now(timezone.utc)
    try:
        last_dt = datetime.fromisoformat(last_ts)
        elapsed_h = (now - last_dt).total_seconds() / 3600.0
        return elapsed_h < next_interval_h
    except (ValueError, TypeError):
        return False


# ── Confidence decay — pure + DB ──────────────────────────────────────────────

def decay_confidence(
    confidence: float,
    days_elapsed: float,
    half_life_days: int,
) -> float:
    """
    Computes decayed confidence using exponential half-life decay.
    Returns the new confidence, clamped to CONFIDENCE_FLOOR from below
    and the original value from above (decay only, never increases).

    Formula: confidence * 0.5^(days_elapsed / half_life_days)
    """
    if days_elapsed <= 0:
        return confidence
    hl = max(1, half_life_days)
    new_conf = confidence * (0.5 ** (days_elapsed / hl))
    return max(CONFIDENCE_FLOOR, min(confidence, new_conf))


def apply_confidence_decay(
    conn: Any,
    now: datetime | None = None,
) -> dict[str, float]:
    """
    Applies half-life confidence decay to all traits not observed today.
    Updates the DB directly (does NOT commit — caller must commit).
    Returns {facet_id: new_confidence} for every trait that changed.
    """
    now = now or datetime.now(timezone.utc)
    changed: dict[str, float] = {}

    rows = conn.execute(
        """SELECT t.facet_id, t.confidence, t.last_observation_at,
                  COALESCE(f.half_life_days, 14) AS half_life_days
           FROM traits t
           JOIN facets f ON t.facet_id = f.id
           WHERE t.confidence > ?""",
        (CONFIDENCE_FLOOR,),
    ).fetchall()

    for row in rows:
        facet_id = row[0]
        confidence = float(row[1] or 0.0)
        last_obs = row[2]
        half_life = int(row[3])

        if not last_obs:
            continue

        try:
            last_dt = datetime.fromisoformat(last_obs.replace("Z", "+00:00"))
            days_elapsed = (now - last_dt).total_seconds() / 86400.0
        except (ValueError, TypeError):
            continue

        if days_elapsed < 1.0:
            continue  # Observed today — no decay

        new_conf = decay_confidence(confidence, days_elapsed, half_life)
        if abs(new_conf - confidence) < 0.001:
            continue

        conn.execute(
            "UPDATE traits SET confidence = ? WHERE facet_id = ?",
            (new_conf, facet_id),
        )
        changed[facet_id] = new_conf

    return changed


# ── Delta computation ──────────────────────────────────────────────────────────

def snapshot_confidences(conn: Any) -> dict[str, float]:
    """Returns {facet_id: confidence} for all traits."""
    rows = conn.execute("SELECT facet_id, confidence FROM traits").fetchall()
    return {row[0]: float(row[1] or 0.0) for row in rows}


def compute_delta(
    before: dict[str, float],
    after: dict[str, float],
) -> float:
    """
    Average absolute change in confidence across all traits present in both snapshots.
    Returns 1.0 if no traits exist (treats empty profile as maximally volatile).
    """
    common = set(before) & set(after)
    if not common:
        return 1.0
    return sum(abs(after[f] - before[f]) for f in common) / len(common)


# ── Trust asymmetry — pure ────────────────────────────────────────────────────

def adjust_trust_signal_strength(
    signal_position: float,
    signal_strength: float,
    current_position: float | None,
) -> float:
    """
    Applies trust asymmetry: trust builds slowly, breaks fast.

    If the new signal_position is HIGHER than the current position (trust rising),
    dampen signal_strength by TRUST_DAMPEN_RISE.
    If signal_position is LOWER (trust dropping), apply at full strength.

    current_position: current value_position from traits table, or None if not yet synthesized.
    Falls back to 0.5 (neutral) when current_position is unknown.
    """
    baseline = current_position if current_position is not None else 0.5
    if signal_position > baseline:
        return signal_strength * TRUST_DAMPEN_RISE
    return signal_strength


# ── Relationship phase — pure ──────────────────────────────────────────────────

def compute_phase(
    current_phase: str,
    obs_count: int,
    high_conf_facets: int,
    avg_confidence: float,
    trust_value: float | None,
    delta: float,
) -> str:
    """
    Returns the new relationship phase given current metrics.
    Phases can only advance, never regress.

    current_phase: one of PHASE_ORDER
    obs_count: total observations across all facets
    high_conf_facets: count of facets with confidence > 0.5
    avg_confidence: mean confidence across all traits
    trust_value: value_position of relational.trust_formation, or None if unset
    delta: last extraction delta (profile volatility)
    """
    if current_phase not in PHASE_ORDER:
        current_phase = "Discovery"

    current_idx = PHASE_ORDER.index(current_phase)
    # Already at maximum phase
    if current_idx >= len(PHASE_ORDER) - 1:
        return current_phase

    criteria = _PHASE_CRITERIA.get(current_phase, {})

    if current_phase == "Discovery":
        if (obs_count >= criteria["obs_count_min"] and
                high_conf_facets >= criteria["high_conf_facets_min"]):
            return "Calibration"

    elif current_phase == "Calibration":
        if (obs_count >= criteria["obs_count_min"] and
                avg_confidence >= criteria["avg_confidence_min"]):
            return "Partnership"

    elif current_phase == "Partnership":
        if (obs_count >= criteria["obs_count_min"] and
                (trust_value is not None and trust_value >= criteria["trust_value_min"]) and
                delta <= criteria["delta_max"]):
            return "Deep Partnership"

    return current_phase


def get_phase_metrics(conn: Any) -> dict[str, Any]:
    """
    Queries the DB for all metrics needed by compute_phase.
    Returns a dict ready to unpack into compute_phase (minus current_phase and delta).
    """
    # Total observations
    obs_count = conn.execute(
        "SELECT COALESCE(SUM(observation_count), 0) FROM traits"
    ).fetchone()[0]

    # Facets with confidence > 0.5
    high_conf_facets = conn.execute(
        "SELECT COUNT(*) FROM traits WHERE confidence > 0.5"
    ).fetchone()[0]

    # Average confidence
    avg_conf_row = conn.execute(
        "SELECT AVG(confidence) FROM traits WHERE observation_count > 0"
    ).fetchone()
    avg_confidence = float(avg_conf_row[0] or 0.0)

    # Trust value position
    trust_row = conn.execute(
        "SELECT value_position FROM traits WHERE facet_id = ?",
        (TRUST_FACET,),
    ).fetchone()
    trust_value = float(trust_row[0]) if (trust_row and trust_row[0] is not None) else None

    return {
        "obs_count": obs_count,
        "high_conf_facets": high_conf_facets,
        "avg_confidence": avg_confidence,
        "trust_value": trust_value,
    }


def advance_phase(
    state: dict[str, Any],
    conn: Any,
) -> tuple[str, bool]:
    """
    Reads phase metrics from DB, computes new phase, returns (new_phase, changed).
    Does not mutate state — caller updates state.
    """
    current_phase = state.get("relationship_phase", "Discovery")
    delta = float(state.get("last_delta", 1.0))
    metrics = get_phase_metrics(conn)
    new_phase = compute_phase(current_phase=current_phase, delta=delta, **metrics)
    return new_phase, (new_phase != current_phase)
