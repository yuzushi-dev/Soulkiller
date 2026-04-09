#!/usr/bin/env python3
"""Soulkiller LIWC — Analisi psicolinguistica sul corpus inbox.

Calcola per ogni settimana (YYYY-WW) i marker linguistici dei messaggi di the subject.
Nessuna chiamata LLM — tutto regex + word lists italiane.

Cron: soulkiller:liwc, settimanale domenica 03:00 Europe/Rome

Usage:
  python3 soulkiller_liwc.py [--period YYYY-WW] [--all] [--dry-run]
"""
from __future__ import annotations
import os
import re
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn

SCRIPT = "soulkiller_liwc"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
SUBJECT_FROM_ID = "demo-subject"


# ---------------------------------------------------------------------------
# Italian word lists (per 100 parole)
# ---------------------------------------------------------------------------

PRONOMI_IO   = re.compile(r'\b(io|mi|me|mio|mia|miei|mie)\b', re.I)
PRONOMI_NOI  = re.compile(r'\b(noi|ci|ce|nostro|nostra|nostri|nostre)\b', re.I)
PRONOMI_TU   = re.compile(r'\b(tu|ti|te|tuo|tua|tuoi|tue|voi|vi)\b', re.I)

INSIGHT      = re.compile(r'\b(penso|pensi|pensa|so|sai|sa|capisco|capisci|capisce|credo|credi|crede|immagino|ricordo|noto|vedo|sembra|sembrerebbe|mi rendo conto)\b', re.I)
CAUSAZIONE   = re.compile(r'\b(perch[eé]|quindi|dunque|allora|poich[eé]|siccome|dato che|visto che|causa|effetto|motivo|ragione|conseguenza)\b', re.I)
DISCREPANZA  = re.compile(r'\b(dovrei|dovresti|dovrebbe|potrei|potresti|potrebbe|vorrei|vorresti|vorrebbe|avrei|se fossi|se avessi|se potessi|idealmente|bisognerebbe)\b', re.I)
CERTEZZA     = re.compile(r'\b(sempre|mai|certamente|sicuramente|ovviamente|assolutamente|definitivamente|di certo|senza dubbio|chiaramente|evidentemente)\b', re.I)
TENTATIVO    = re.compile(r'\b(forse|magari|probabilmente|sembra|pare|non so|non sono sicuro|boh|vedremo|dipende)\b', re.I)

POSITIVO     = re.compile(r'\b(bene|ottimo|ottima|felice|bravo|brava|bello|bella|perfetto|perfetta|giusto|buono|buona|fantastico|fantastica|eccellente|top)\b', re.I)
NEGATIVO     = re.compile(r'\b(male|problema|difficile|sbagliato|sbagliata|cattivo|cattiva|brutto|brutta|pessimo|pessima|orribile|terribile|schifo|schifoso|rotto|fallito|fallita)\b', re.I)
ANSIA        = re.compile(r'\b(preoccupato|preoccupata|ansioso|ansiosa|nervoso|nervosa|paura|stress|stressato|stressata|agitato|agitata|teso|tesa|angoscia)\b', re.I)
RABBIA       = re.compile(r'\b(arrabbiato|arrabbiata|incazzato|incazzata|frustrato|frustrata|odio|detesto|stufo|stufa)\b', re.I)

SOCIALE      = re.compile(r'\b(amico|amica|amici|famiglia|partner|compagna|compagno|persona|gente|persone|collega|colleghi|lei|lui|loro)\b', re.I)

PASSATO      = re.compile(r'\b(ieri|scorso|scors[ao]|fa|era|aveva|ho fatto|sono andato|sono andata|abbiamo|avevamo|eravamo|prima)\b', re.I)
PRESENTE     = re.compile(r'\b(oggi|adesso|ora|attualmente|in questo momento)\b', re.I)
FUTURO       = re.compile(r'\b(domani|dopodomani|prossimo|prossima|andr[oò]|far[oò]|sar[oò]|verr[oò]|voglio|devo|penso di|ho intenzione|in futuro|presto)\b', re.I)


def _count_per_100(pattern: re.Pattern, text: str, word_count: int) -> float:
    if word_count == 0:
        return 0.0
    matches = len(pattern.findall(text))
    return round(matches / word_count * 100, 2)


def _word_count(text: str) -> int:
    return len(text.split())


def compute_liwc(messages: list[str]) -> dict[str, Any]:
    """Compute LIWC-style metrics for a list of message strings."""
    full_text = " ".join(messages)
    words = _word_count(full_text)
    if words == 0:
        return {}

    i_r   = _count_per_100(PRONOMI_IO,  full_text, words)
    we_r  = _count_per_100(PRONOMI_NOI, full_text, words)
    you_r = _count_per_100(PRONOMI_TU,  full_text, words)

    ins   = _count_per_100(INSIGHT,     full_text, words)
    caus  = _count_per_100(CAUSAZIONE,  full_text, words)
    disc  = _count_per_100(DISCREPANZA, full_text, words)
    cert  = _count_per_100(CERTEZZA,    full_text, words)
    tent  = _count_per_100(TENTATIVO,   full_text, words)

    pos   = _count_per_100(POSITIVO,    full_text, words)
    neg   = _count_per_100(NEGATIVO,    full_text, words)
    anx   = _count_per_100(ANSIA,       full_text, words)
    ang   = _count_per_100(RABBIA,      full_text, words)

    soc   = _count_per_100(SOCIALE,     full_text, words)

    past  = _count_per_100(PASSATO,     full_text, words)
    pres  = _count_per_100(PRESENTE,    full_text, words)
    fut   = _count_per_100(FUTURO,      full_text, words)

    # Cognitive complexity: high insight + causation + discrepancy + tentative (nuanced)
    cogn  = round((ins + caus + disc + tent) / 4, 2)

    return {
        "message_count": len(messages),
        "i_ratio": i_r, "we_ratio": we_r, "you_ratio": you_r,
        "insight_ratio": ins, "causation_ratio": caus,
        "discrepancy_ratio": disc, "certainty_ratio": cert, "tentative_ratio": tent,
        "positive_affect": pos, "negative_affect": neg,
        "anxiety_words": anx, "anger_words": ang,
        "social_ratio": soc,
        "past_focus": past, "present_focus": pres, "future_focus": fut,
        "cognitive_complexity": cogn,
    }


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def iso_week(dt_str: str) -> str:
    """Convert ISO datetime string to YYYY-WW."""
    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    return f"{dt.isocalendar()[0]:04d}-{dt.isocalendar()[1]:02d}"


def load_messages_by_week(db, from_id: str = SUBJECT_FROM_ID) -> dict[str, list[str]]:
    rows = db.execute(
        "SELECT content, received_at FROM inbox WHERE from_id=? ORDER BY received_at ASC",
        (from_id,)
    ).fetchall()
    by_week: dict[str, list[str]] = {}
    for r in rows:
        week = iso_week(r["received_at"])
        by_week.setdefault(week, []).append(r["content"])
    return by_week


def store_liwc(db, period: str, metrics: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO liwc_metrics
            (period, message_count,
             i_ratio, we_ratio, you_ratio,
             insight_ratio, causation_ratio, discrepancy_ratio, certainty_ratio, tentative_ratio,
             positive_affect, negative_affect, anxiety_words, anger_words,
             social_ratio, past_focus, present_focus, future_focus,
             cognitive_complexity, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(period) DO UPDATE SET
            message_count=excluded.message_count,
            i_ratio=excluded.i_ratio, we_ratio=excluded.we_ratio, you_ratio=excluded.you_ratio,
            insight_ratio=excluded.insight_ratio, causation_ratio=excluded.causation_ratio,
            discrepancy_ratio=excluded.discrepancy_ratio, certainty_ratio=excluded.certainty_ratio,
            tentative_ratio=excluded.tentative_ratio, positive_affect=excluded.positive_affect,
            negative_affect=excluded.negative_affect, anxiety_words=excluded.anxiety_words,
            anger_words=excluded.anger_words, social_ratio=excluded.social_ratio,
            past_focus=excluded.past_focus, present_focus=excluded.present_focus,
            future_focus=excluded.future_focus, cognitive_complexity=excluded.cognitive_complexity,
            computed_at=excluded.computed_at
    """, (
        period, metrics["message_count"],
        metrics["i_ratio"], metrics["we_ratio"], metrics["you_ratio"],
        metrics["insight_ratio"], metrics["causation_ratio"], metrics["discrepancy_ratio"],
        metrics["certainty_ratio"], metrics["tentative_ratio"],
        metrics["positive_affect"], metrics["negative_affect"],
        metrics["anxiety_words"], metrics["anger_words"],
        metrics["social_ratio"],
        metrics["past_focus"], metrics["present_focus"], metrics["future_focus"],
        metrics["cognitive_complexity"],
        now
    ))


def derive_observations(db, period: str, metrics: dict) -> None:
    """Derive facet observations from LIWC metrics."""
    now = datetime.now(timezone.utc).isoformat()
    source_ref = f"liwc:{period}"
    observations = []

    # temporal.planning_horizon: future_focus vs past_focus
    total_temporal = metrics["past_focus"] + metrics["future_focus"] + metrics["present_focus"]
    if total_temporal > 0.5:
        future_ratio = metrics["future_focus"] / total_temporal
        observations.append((
            "temporal.planning_horizon", future_ratio, 0.55,
            f"LIWC {period}: future_focus={metrics['future_focus']:.1f}/100, "
            f"past={metrics['past_focus']:.1f}/100"
        ))

    # temporal.delay_discounting: high future_focus + discrepancy = plans ahead (low discounting)
    if total_temporal > 0.5 and metrics["discrepancy_ratio"] > 0:
        dd_pos = (metrics["future_focus"] / (total_temporal + 0.1)) * 0.7 + \
                 min(1.0, metrics["discrepancy_ratio"] / 5.0) * 0.3
        observations.append((
            "temporal.delay_discounting", 1.0 - dd_pos, 0.45,
            f"LIWC {period}: future={metrics['future_focus']:.1f}, discrepancy={metrics['discrepancy_ratio']:.1f}"
        ))

    # emotional.emotion_clarity
    if metrics["insight_ratio"] > 0:
        clarity = min(1.0, metrics["insight_ratio"] / 10.0)
        clarity_adj = clarity * (1 - min(1.0, metrics["anxiety_words"] / 5.0) * 0.3)
        observations.append((
            "emotional.emotion_clarity", clarity_adj, 0.40,
            f"LIWC {period}: insight={metrics['insight_ratio']:.1f}/100, anxiety={metrics['anxiety_words']:.1f}/100"
        ))

    # cognitive.analytical_approach
    if metrics["causation_ratio"] + metrics["discrepancy_ratio"] > 0.5:
        anal = min(1.0, (metrics["causation_ratio"] + metrics["discrepancy_ratio"]) / 10.0)
        observations.append((
            "cognitive.analytical_approach", anal, 0.45,
            f"LIWC {period}: causation={metrics['causation_ratio']:.1f}, "
            f"discrepancy={metrics['discrepancy_ratio']:.1f}"
        ))

    for facet_id, signal_pos, signal_str, content in observations:
        db.execute("""
            INSERT INTO observations
                (facet_id, signal_position, signal_strength, content,
                 source_type, source_ref, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(facet_id, source_ref) DO UPDATE SET
                signal_position=excluded.signal_position,
                signal_strength=excluded.signal_strength,
                content=excluded.content
        """, (facet_id, signal_pos, signal_str, content, "liwc", source_ref, now))


def run(period: str | None = None, all_periods: bool = False,
        dry_run: bool = False) -> None:
    db = get_db()
    try:
        by_week = load_messages_by_week(db)
        if not by_week:
            info(SCRIPT, "no_messages")
            return

        if period:
            periods_to_process = [period] if period in by_week else []
        elif all_periods:
            periods_to_process = sorted(by_week.keys())
        else:
            # Default: process last 4 weeks
            all_sorted = sorted(by_week.keys())
            periods_to_process = all_sorted[-4:]

        info(SCRIPT, "run_start", periods=len(periods_to_process))

        for p in periods_to_process:
            msgs = by_week[p]
            metrics = compute_liwc(msgs)
            if not metrics:
                continue

            if dry_run:
                print(f"\n[{p}] {metrics['message_count']} msgs | "
                      f"i={metrics['i_ratio']} we={metrics['we_ratio']} | "
                      f"pos={metrics['positive_affect']} neg={metrics['negative_affect']} | "
                      f"future={metrics['future_focus']} past={metrics['past_focus']} | "
                      f"complexity={metrics['cognitive_complexity']}")
                continue

            store_liwc(db, p, metrics)
            derive_observations(db, p, metrics)
            info(SCRIPT, "period_computed", period=p, msgs=metrics["message_count"],
                 neg_affect=metrics["negative_affect"],
                 future_focus=metrics["future_focus"],
                 complexity=metrics["cognitive_complexity"])

        if not dry_run:
            db.commit()
            info(SCRIPT, "run_complete", periods_processed=len(periods_to_process))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller LIWC Psycholinguistics")
    p.add_argument("--period", help="Specific YYYY-WW period")
    p.add_argument("--all", action="store_true", dest="all_periods")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(period=args.period, all_periods=args.all_periods, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
