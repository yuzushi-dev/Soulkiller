#!/usr/bin/env python3
"""Soulkiller Question Engine - facet-aware check-in question selection.

Scores 46 personality facets using the Facet Gap Score (FGS) formula:

FGS = 0.40 * knowledge_gap
    + 0.25 * facet_importance
    + 0.15 * temporal_readiness
    - 0.20 * intrusion_cost
    - 0.10 * asked_recently

Backward-compatible output format with additive fields (selected_facet, question_hint).
Falls back to legacy 5-topic scorer if Soulkiller DB is unavailable.
"""

from __future__ import annotations
import os

import argparse
import json
import math
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from lib.log import info, warn, error

SCRIPT = "soulkiller_question_engine"
TZ = ZoneInfo("Europe/Rome")

# Category importance weights (cognitive/values > aesthetic)
CATEGORY_IMPORTANCE: dict[str, float] = {
    "cognitive": 0.85,
    "emotional": 0.80,
    "communication": 0.65,
    "relational": 0.75,
    "values": 0.85,
    "temporal": 0.55,
    "aesthetic": 0.40,
    "meta_cognition": 0.70,
}

# Time-of-day readiness: deep/sensitive facets score higher in evening
DEEP_CATEGORIES = {"emotional", "relational", "values", "meta_cognition"}
LIGHT_CATEGORIES = {"aesthetic", "communication", "temporal", "cognitive"}

# Sensitivity → intrusion base
SENSITIVITY_BASE: dict[str, float] = {
    "bassa": 0.25,
    "media": 0.45,
    "alta": 0.65,
}

# Question hints per facet - concrete situations to explore naturally.
# IMPORTANT: These are NOT templates for questions. They describe a real-life
# scenario or angle to probe. The cron agent must translate them into a casual,
# human message - never a binary "A or B?" quiz.
QUESTION_HINTS: dict[str, str] = {
    "cognitive.decision_speed": "Chiedi di una decisione recente che ha dovuto prendere in fretta - cosa ha fatto, come si è sentito dopo",
    "cognitive.risk_tolerance": "Chiedi di un momento in cui ha scommesso su qualcosa di incerto - un progetto, un cambio, un acquisto",
    "cognitive.abstraction_level": "Chiedi come ragiona su un problema tecnico attuale - parte dai dettagli o dal quadro generale",
    "cognitive.information_gathering": "Chiedi di un acquisto o scelta recente - quanto ha cercato prima di decidere",
    "cognitive.analytical_approach": "Chiedi di una situazione dove ha seguito l'istinto invece di analizzare, o viceversa - come è andata",
    "cognitive.learning_style": "Chiedi come ha imparato qualcosa di nuovo di recente - ha letto prima o si è buttato a provare",
    "emotional.stress_response": "Chiedi della giornata di oggi/ieri - se c'è stato un momento di stress, cos'ha fatto",
    "emotional.emotional_granularity": "Chiedi come sta, in modo che possa descrivere il suo stato con le sue parole",
    "emotional.resilience_pattern": "Chiedi di un momento difficile recente - quanto ci ha messo a riprendersi, cosa l'ha aiutato",
    "emotional.frustration_triggers": "Chiedi di qualcosa che l'ha fatto incazzare di recente - al lavoro, con la tecnologia, con le persone",
    "emotional.joy_sources": "Chiedi dell'ultima cosa che l'ha reso genuinamente contento - anche piccola",
    "emotional.emotional_expression": "Chiedi se le persone intorno a lui capiscono come sta, o se tende a non farlo vedere",
    "communication.verbosity": "Chiedi del suo stile nei messaggi - se scrive tanto o poco, se dipende dalla persona",
    "communication.directness": "Chiedi di una volta in cui ha detto qualcosa di scomodo a qualcuno - come l'ha gestita",
    "communication.humor_type": "Chiedi di qualcosa che l'ha fatto ridere di recente",
    "communication.conflict_style": "Chiedi di un disaccordo recente - l'ha affrontato o ha lasciato perdere",
    "communication.storytelling_tendency": "Chiedi come spiegherebbe un problema a qualcuno - con un esempio concreto o con i dati",
    "communication.formality_range": "Chiedi se cambia modo di parlare a seconda del contesto - lavoro vs amici vs sconosciuti",
    "relational.trust_formation": "Chiedi quanto tempo ci mette a fidarsi di una persona nuova - cosa lo convince",
    "relational.boundary_style": "Chiedi di un momento in cui qualcuno ha oltrepassato un suo limite - cosa ha fatto",
    "relational.loyalty_pattern": "Chiedi di un rapporto importante - cosa lo tiene saldo, cosa lo romperebbe",
    "relational.social_energy": "Chiedi del weekend - ha visto gente o ha preferito stare per i fatti suoi",
    "relational.help_seeking": "Chiedi di un problema recente che ha risolto - l'ha fatto da solo o ha chiesto a qualcuno",
    "relational.feedback_preference": "Chiedi come preferisce ricevere critiche - diretto e brutale o con un po' di contesto",
    "values.core_values": "Chiedi di una cosa su cui non farebbe mai compromessi, a prescindere dalle circostanze",
    "values.fairness_model": "Chiedi di una situazione dove ha visto un'ingiustizia - cosa l'ha infastidito di più",
    "values.authority_stance": "Chiedi di un momento in cui ha dovuto seguire una regola che trovava stupida - cos'ha fatto",
    "values.autonomy_importance": "Chiedi se preferisce lavorare in team o in autonomia - e perché",
    "values.aesthetic_values": "Chiedi di un oggetto/tool che ha scelto di recente - l'ha scelto per funzionalità o perché gli piaceva",
    "values.work_ethic": "Chiedi di un progetto recente - cos'è più importante per lui, il risultato o come ci è arrivato",
    "temporal.planning_horizon": "Chiedi se ha piani per il prossimo mese, o se tende a vivere giorno per giorno",
    "temporal.routine_attachment": "Chiedi della sua mattina tipo - se ha una routine fissa o cambia sempre",
    "temporal.deadline_behavior": "Chiedi di una deadline recente - l'ha chiusa in anticipo o all'ultimo",
    "temporal.nostalgia_tendency": "Chiedi se ogni tanto gli manca un periodo passato - quale, perché",
    "temporal.patience_threshold": "Chiedi di qualcosa che lo fa impazientire - attese, lentezze, burocrazia",
    "aesthetic.design_sensibility": "Chiedi del suo setup - scrivania, desktop, come organizza lo spazio digitale/fisico",
    "aesthetic.music_taste": "Chiedi cosa sta ascoltando in questo periodo - genere, artista, playlist",
    "aesthetic.media_consumption": "Chiedi se sta guardando/leggendo qualcosa di interessante - serie, libro, canale YouTube",
    "aesthetic.food_preferences": "Chiedi di cosa ha mangiato oggi/ieri - se cucina, se prova cose nuove",
    "aesthetic.environment_preference": "Chiedi dove lavora meglio - se ha bisogno di silenzio o se gli piace un po' di casino",
    "meta_cognition.self_awareness": "Chiedi se ha notato un suo pattern recente - un'abitudine, una reazione ricorrente",
    "meta_cognition.growth_mindset": "Chiedi di qualcosa in cui è migliorato nell'ultimo anno - cosa l'ha fatto crescere",
    "meta_cognition.cognitive_biases": "Chiedi di una decisione recente dove a posteriori si è reso conto di aver sbagliato valutazione",
    "meta_cognition.reflection_habit": "Chiedi se ogni tanto si ferma a pensare a come sta andando - o se va sempre avanti senza guardarsi indietro",
    "meta_cognition.change_readiness": "Chiedi di un cambiamento recente nella sua vita/lavoro - come l'ha preso",
    "meta_cognition.uncertainty_tolerance": "Chiedi di una situazione attuale dove non sa come andrà a finire - come la vive",
    "relational.attachment_anxiety": "Quando qualcuno di importante per te è silenzioso o distante per un po', cosa senti di solito?",
    "relational.attachment_avoidance": "Quanto ti viene naturale chiedere supporto emotivo a chi ti vuole bene quando sei in difficoltà?",
    "emotional.emotion_clarity": "Quando stai male emotivamente, riesci di solito a capire esattamente cosa provi, o è più una sensazione generica?",
    "emotional.distress_tolerance": "Quando sei in uno stato emotivo intenso, riesci comunque a portare avanti le cose che devi fare?",
    "temporal.delay_discounting": "Se potessi scegliere tra 100€ oggi e 150€ tra un mese, cosa sceglieresti? E tra un anno?",
    "meta_cognition.narrative_agency": "Guardando gli ultimi 5 anni della tua vita, ti sembra di aver guidato tu le scelte principali o di aver più reagito a quello che capitava?",
    "relational.vulnerability_capacity": "Quando attraversi qualcosa di difficile emotivamente, quanto ti viene naturale parlarne con qualcuno di cui ti fidi?",
    "values.schwartz_self_enhancement": "Cosa conta di più per te: riuscire, emergere, avere successo - oppure contribuire al benessere degli altri e della comunità?",
}



def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_date_maybe(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(value + "T00:00:00").replace(tzinfo=TZ)
    except ValueError:
        return None


# ── Gate check functions (reused from legacy topic gap scorer) ──────────────

def parse_hhmm(raw: str, fallback: str) -> time:
    source = (raw or fallback).strip()
    try:
        hh, mm = source.split(":", 1)
        return time(hour=int(hh), minute=int(mm))
    except Exception:
        hh, mm = fallback.split(":", 1)
        return time(hour=int(hh), minute=int(mm))


def in_range_wrapped(value: time, start: time, end: time) -> bool:
    if start <= end:
        return start <= value <= end
    return value >= start or value <= end


def in_any_window(current: time, windows: list[str]) -> bool:
    for window in windows:
        if "-" not in window:
            continue
        raw_start, raw_end = window.split("-", 1)
        start = parse_hhmm(raw_start, "09:30")
        end = parse_hhmm(raw_end, "12:30")
        if in_range_wrapped(current, start, end):
            return True
    return False


def quiet_hours_active(current: time, quiet_cfg: dict[str, Any]) -> bool:
    start = parse_hhmm(str(quiet_cfg.get("start", "23:00")), "23:00")
    end = parse_hhmm(str(quiet_cfg.get("end", "08:00")), "08:00")
    return in_range_wrapped(current, start, end)


def ensure_personal_checkins(state: dict[str, Any], today: str) -> dict[str, Any]:
    pc = state.setdefault("personal_checkins", {})
    pc.setdefault("enabled", True)
    pc.setdefault("target_min_per_day", 1)
    pc.setdefault("target_max_per_day", 3)
    pc.setdefault("min_spacing_minutes", 240)
    pc.setdefault("windows", ["09:30-12:30", "15:00-19:30", "21:00-22:30"])
    pc.setdefault("quiet_hours", {"start": "23:00", "end": "08:00"})
    pc.setdefault("sent_today", 0)
    pc.setdefault("sent_today_date", today)
    pc.setdefault("last_sent_at", None)
    pc.setdefault("history", [])

    if str(pc.get("sent_today_date")) != today:
        pc["sent_today"] = 0
        pc["sent_today_date"] = today
    return pc


def compute_due(pc: dict[str, Any], now: datetime) -> tuple[bool, bool, str]:
    if not bool(pc.get("enabled", True)):
        return False, False, "disabled"

    current = now.timetz().replace(tzinfo=None)

    if quiet_hours_active(current, dict(pc.get("quiet_hours", {}))):
        return False, False, "quiet_hours"

    sent_today = int(pc.get("sent_today", 0) or 0)
    max_per_day = int(pc.get("target_max_per_day", 3) or 3)
    if sent_today >= max_per_day:
        return False, False, "daily_max_reached"

    spacing_minutes = int(pc.get("min_spacing_minutes", 240) or 240)
    last_sent = parse_date_maybe(str(pc.get("last_sent_at", "")))
    if last_sent:
        elapsed = (now - last_sent).total_seconds() / 60.0
        if elapsed < spacing_minutes:
            return False, False, "min_spacing_not_met"

    min_per_day = int(pc.get("target_min_per_day", 1) or 1)
    mandatory_after = time(hour=20, minute=30)
    mandatory = sent_today < min_per_day and current >= mandatory_after

    in_window = in_any_window(current, list(pc.get("windows", [])))
    if mandatory:
        return True, True, "mandatory_min_target"
    if in_window:
        return True, False, "window_due"
    return False, False, "outside_windows"


# ── FGS scoring ─────────────────────────────────────────────────────────────

def compute_knowledge_gap(trait: dict[str, Any]) -> float:
    """1.0 - confidence: how little we know."""
    return 1.0 - float(trait.get("confidence", 0.0) or 0.0)


def compute_facet_importance(facet: dict[str, Any]) -> float:
    """Category-based importance weight."""
    cat = facet.get("category", "")
    return CATEGORY_IMPORTANCE.get(cat, 0.50)


def compute_temporal_readiness(facet: dict[str, Any], now: datetime) -> float:
    """Time-of-day fit: deep questions in evening, light in morning."""
    hour = now.hour
    cat = facet.get("category", "")

    if cat in DEEP_CATEGORIES:
        # Deep topics: better in evening (18-22) or late afternoon (15-18)
        if 18 <= hour <= 22:
            return 0.90
        elif 15 <= hour < 18:
            return 0.70
        elif 9 <= hour < 15:
            return 0.45
        else:
            return 0.30
    else:
        # Light topics: fine anytime during waking hours
        if 9 <= hour <= 22:
            return 0.75
        elif 8 <= hour < 9:
            return 0.60
        else:
            return 0.30


def compute_intrusion_cost(facet: dict[str, Any], mode: str, now: datetime) -> float:
    """Facet sensitivity + presence_mode penalty."""
    sensitivity = facet.get("sensitivity", "media")
    intrusion = SENSITIVITY_BASE.get(sensitivity, 0.45)

    if mode == "anchor":
        intrusion -= 0.15  # anchor mode = more emotional availability
    elif mode == "ambient":
        intrusion -= 0.05
    elif mode == "spotlight" and sensitivity == "alta":
        intrusion += 0.10

    if now.hour < 9 or now.hour >= 22:
        intrusion += 0.10

    return clamp(intrusion)


def compute_asked_recently(facet_id: str, checkin_history: list[dict[str, Any]],
                           now: datetime) -> float:
    """Decay based on checkin_exchanges timestamps."""
    # Check both legacy topic history and facet-based history
    relevant = [h for h in checkin_history
                if h.get("facet") == facet_id or h.get("topic") == facet_id]
    if not relevant:
        return 0.0

    latest = None
    for item in relevant:
        at_str = str(item.get("at", item.get("asked_at", "")))
        parsed = parse_date_maybe(at_str)
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    if not latest:
        return 0.0

    hours = (now - latest).total_seconds() / 3600.0
    if hours <= 72:
        return 1.0
    if hours <= 168:
        return 0.8
    if hours <= 336:
        return 0.45
    return 0.0


def hours_since_last_facet(facet_id: str, checkin_history: list[dict[str, Any]],
                           now: datetime) -> float | None:
    latest: datetime | None = None
    for item in checkin_history:
        if item.get("facet") != facet_id and item.get("topic") != facet_id:
            continue
        parsed = parse_date_maybe(str(item.get("at", item.get("asked_at", ""))))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    if latest is None:
        return None
    return (now - latest).total_seconds() / 3600.0


def hours_since_last_category(category: str, checkin_history: list[dict[str, Any]],
                              now: datetime) -> float | None:
    latest: datetime | None = None
    for item in checkin_history:
        facet = str(item.get("facet") or item.get("topic") or "")
        if not facet.startswith(f"{category}."):
            continue
        parsed = parse_date_maybe(str(item.get("at", item.get("asked_at", ""))))
        if parsed and (latest is None or parsed > latest):
            latest = parsed
    if latest is None:
        return None
    return (now - latest).total_seconds() / 3600.0


def score_facet(
    facet: dict[str, Any],
    trait: dict[str, Any],
    mode: str,
    now: datetime,
    checkin_history: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute FGS for a single facet."""
    knowledge_gap = compute_knowledge_gap(trait)
    importance = compute_facet_importance(facet)
    temporal = compute_temporal_readiness(facet, now)
    intrusion = compute_intrusion_cost(facet, mode, now)
    asked = compute_asked_recently(facet["id"], checkin_history, now)

    fgs = (0.40 * knowledge_gap
           + 0.25 * importance
           + 0.15 * temporal
           - 0.20 * intrusion
           - 0.10 * asked)
    fgs = clamp(fgs)

    return {
        "facet": facet["id"],
        "category": facet["category"],
        "score": round(fgs, 4),
        "knowledge_gap": round(knowledge_gap, 4),
        "importance": round(importance, 4),
        "temporal_readiness": round(temporal, 4),
        "intrusion_cost": round(intrusion, 4),
        "asked_recently": round(asked, 4),
        "confidence": round(float(trait.get("confidence", 0.0) or 0.0), 4),
        "observation_count": int(trait.get("observation_count", 0) or 0),
    }


def run_soulkiller_scoring(state: dict[str, Any], now: datetime, apply: bool = False,
                           state_path: Path | None = None) -> dict[str, Any]:
    """Main scoring path using Soulkiller DB."""
    from soulkiller_db import get_db, get_all_facets, get_all_traits

    today = now.date().isoformat()
    pc = ensure_personal_checkins(state, today)
    mode = str(state.get("presence_mode", "spotlight"))

    # Gate check
    due, mandatory, due_reason = compute_due(pc, now)

    # Build checkin history from state + DB
    history = list(pc.get("history", []))

    # Get DB checkin exchanges for asked_recently
    try:
        conn = get_db()
        rows = conn.execute(
            "SELECT facet_id, asked_at FROM checkin_exchanges ORDER BY asked_at DESC LIMIT 100"
        ).fetchall()
        for row in rows:
            history.append({"facet": row["facet_id"], "at": row["asked_at"]})
        conn.close()
    except Exception:
        pass

    # Score all facets
    facets = get_all_facets()
    traits_list = get_all_traits()
    traits_by_id = {t["facet_id"]: t for t in traits_list}

    ranking: list[dict[str, Any]] = []
    for facet in facets:
        trait = traits_by_id.get(facet["id"], {"confidence": 0.0, "observation_count": 0})
        scored = score_facet(facet, trait, mode, now, history)
        ranking.append(scored)

    ranking.sort(key=lambda x: x["score"], reverse=True)

    # Select candidate
    threshold = 0.15  # Lower than TGS (0.29) because we have 46 facets
    selected_item: dict[str, Any] | None = None

    if due:
        for item in ranking:
            facet_age = hours_since_last_facet(item["facet"], history, now)
            if facet_age is not None and facet_age < 168:
                continue
            category_age = hours_since_last_category(item["category"], history, now)
            if category_age is not None and category_age < 36:
                continue
            if item["score"] >= threshold:
                selected_item = item
                break
        if not selected_item:
            for item in ranking:
                facet_age = hours_since_last_facet(item["facet"], history, now)
                if facet_age is not None and facet_age < 96:
                    continue
                category_age = hours_since_last_category(item["category"], history, now)
                if category_age is not None and category_age < 18:
                    continue
                if item["score"] >= threshold:
                    selected_item = item
                    break
        if not selected_item and mandatory and ranking:
            selected_item = ranking[0]

    # Determine status
    status = "not_due"
    reason = due_reason

    if not bool(pc.get("enabled", True)):
        status = "disabled"
        reason = "personal_checkins_disabled"
    elif due and selected_item:
        status = "ask_now"
        reason = "mandatory" if mandatory else "due"
    elif due and not selected_item:
        status = "no_candidate"
        reason = "no_facet_above_threshold"

    # Build question hint
    question_hint = ""
    if selected_item:
        question_hint = QUESTION_HINTS.get(selected_item["facet"], "")

    # Update state if applying
    if apply and state_path:
        # Update soulkiller-specific state
        sk_cfg = state.setdefault("soulkiller", {})
        sk_cfg["last_run_at"] = now.isoformat()
        sk_cfg["last_ranking"] = ranking[:5]

        if status == "ask_now" and selected_item:
            sk_cfg["last_selected"] = {
                "facet": selected_item["facet"],
                "score": selected_item["score"],
                "at": now.isoformat(),
                "reason": reason,
            }
            pc["sent_today"] = int(pc.get("sent_today", 0) or 0) + 1
            pc["sent_today_date"] = today
            pc["last_sent_at"] = now.isoformat()
            history_entry = {
                "at": now.isoformat(),
                "facet": selected_item["facet"],
                "topic": selected_item["facet"],  # backward compat
                "score": selected_item["score"],
            }
            pc_history = list(pc.get("history", []))
            pc_history.append(history_entry)
            pc["history"] = pc_history[-40:]
        else:
            sk_cfg["last_selected"] = None

        state["updated_at"] = today
        save_json(state_path, state)

    # Output (backward-compatible)
    output: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "now": now.isoformat(),
        "due": due,
        "mandatory": mandatory,
        "presence_mode": mode,
        "threshold": threshold,
        "engine": "soulkiller",
        # Backward compatible
        "selected_topic": selected_item["facet"] if selected_item else None,
        "selected_score": selected_item["score"] if selected_item else None,
        # New fields
        "selected_facet": selected_item["facet"] if selected_item else None,
        "question_hint": question_hint,
        "ranking_top3": ranking[:3],
        "ranking_top5": ranking[:5],
    }

    return output


def run_legacy_fallback(state: dict[str, Any], now: datetime, apply: bool = False,
                        state_path: Path | None = None) -> dict[str, Any]:
    """Fallback to legacy 5-topic scorer."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "soulkiller_topic_gap_score",
        Path(__file__).parent / "soulkiller_topic_gap_score.py",
    )
    if spec is None or spec.loader is None:
        return {"status": "error", "reason": "legacy_scorer_not_found", "engine": "fallback_failed"}

    mod = importlib.util.module_from_spec(spec)
    # Don't run main(), just load definitions
    sys.modules["soulkiller_topic_gap_score"] = mod
    spec.loader.exec_module(mod)

    import os as _os
    _data_dir = Path(_os.environ.get("SOULKILLER_DATA_DIR", "")) if _os.environ.get("SOULKILLER_DATA_DIR") else Path(__file__).resolve().parents[3] / "runtime"
    profile_path = _data_dir / "subject_profile.json"
    profile = load_json(profile_path)
    records = list(profile.get("records", []))

    today = now.date().isoformat()
    pc = ensure_personal_checkins(state, today)
    mode = str(state.get("presence_mode", "spotlight"))
    due, mandatory, due_reason = compute_due(pc, now)
    history = list(pc.get("history", []))

    ranking: list[dict[str, Any]] = []
    for topic in mod.TOPICS:
        relevant = mod.records_for_topic(records, topic)
        unknownness = mod.compute_unknownness(relevant, now)
        impact = mod.compute_impact(topic, relevant)
        timeliness = mod.compute_timeliness(topic, relevant, mode, now)
        intrusion = mod.compute_intrusion(topic, mode, now)
        asked_recently = mod.compute_asked_recently(topic, history, now)
        score = clamp(0.35 * unknownness + 0.25 * impact + 0.20 * timeliness
                      - 0.20 * intrusion - 0.15 * asked_recently)
        ranking.append({
            "topic": topic.topic_id,
            "facet": topic.topic_id,
            "score": round(score, 4),
        })

    ranking.sort(key=lambda x: x["score"], reverse=True)
    threshold = float(state.get("topic_gap", {}).get("threshold", 0.29) or 0.29)
    selected_item = None

    if due:
        for item in ranking:
            if item["score"] >= threshold:
                selected_item = item
                break
        if not selected_item and mandatory and ranking:
            selected_item = ranking[0]

    status = "not_due"
    reason = due_reason
    if not bool(pc.get("enabled", True)):
        status = "disabled"
        reason = "personal_checkins_disabled"
    elif due and selected_item:
        status = "ask_now"
        reason = "mandatory" if mandatory else "due"
    elif due and not selected_item:
        status = "no_candidate"
        reason = "no_topic_above_threshold"

    if apply and state_path:
        if status == "ask_now" and selected_item:
            pc["sent_today"] = int(pc.get("sent_today", 0) or 0) + 1
            pc["sent_today_date"] = today
            pc["last_sent_at"] = now.isoformat()
            pc_history = list(pc.get("history", []))
            pc_history.append({"at": now.isoformat(), "topic": selected_item["topic"], "score": selected_item["score"]})
            pc["history"] = pc_history[-40:]
        state["updated_at"] = today
        save_json(state_path, state)

    return {
        "status": status,
        "reason": reason,
        "now": now.isoformat(),
        "due": due,
        "mandatory": mandatory,
        "engine": "legacy_fallback",
        "selected_topic": selected_item["topic"] if selected_item else None,
        "selected_facet": selected_item.get("facet") if selected_item else None,
        "selected_score": selected_item["score"] if selected_item else None,
        "question_hint": "",
        "ranking_top3": ranking[:3],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Soulkiller Question Engine")
    parser.add_argument("--state", required=True, help="Path to operational state JSON file")
    parser.add_argument("--now", default=None, help="Optional ISO datetime override")
    parser.add_argument("--apply", action="store_true", help="Persist state updates")
    args = parser.parse_args()

    state_path = Path(args.state)
    state = load_json(state_path)

    if args.now:
        parsed = parse_date_maybe(args.now)
        now = parsed if parsed else datetime.now(tz=TZ)
    else:
        now = datetime.now(tz=TZ)

    # Try Soulkiller engine, fall back to legacy
    try:
        output = run_soulkiller_scoring(state, now, apply=args.apply, state_path=state_path)
        # Log to stderr to keep stdout clean JSON
        import sys as _sys
        print(json.dumps({"level": "info", "script": SCRIPT, "event": "scoring_complete",
                          "engine": "soulkiller", "status": output["status"],
                          "selected": output.get("selected_facet")}), file=_sys.stderr)
    except Exception as e:
        warn(SCRIPT, "soulkiller_fallback", error=str(e))
        try:
            output = run_legacy_fallback(state, now, apply=args.apply, state_path=state_path)
        except Exception as e2:
            error(SCRIPT, "scoring_failed", error=str(e2))
            output = {"status": "error", "reason": str(e2), "engine": "failed"}

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
