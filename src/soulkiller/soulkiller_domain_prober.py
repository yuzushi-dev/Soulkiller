#!/usr/bin/env python3
"""Soulkiller Domain-Aware Probing Scheduler (IMP-16)

Targets facet domains that are chronically under-observed due to low
spontaneous occurrence in the primary communication corpus.

Cron: soulkiller:domain-prober, biweekly Wednesday 10:00 Europe/Rome

Algorithm:
  1. Load domain_probe_schedule - domains where last_probe_at > probe_interval_days
     AND mean observation count across domain facets < MIN_OBS_THRESHOLD
  2. Generate 2-3 targeted questions for the selected domain
  3. Schedule delivery across consecutive days via question_engine

Usage:
  python3 soulkiller_domain_prober.py [--dry-run] [--model ...]
"""
from __future__ import annotations
import os

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn

SCRIPT = "soulkiller_domain_prober"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
MIN_OBS_THRESHOLD = 8  # facets below this trigger probing
LLM_TIMEOUT = 300

# Default domain schedule (seeded by init)
DEFAULT_SCHEDULE: list[dict[str, Any]] = [
    {
        "domain": "relational_depth",
        "facet_ids": ["relational.loyalty_pattern", "relational.trust_formation",
                      "relational.boundary_style"],
        "probe_interval_days": 45,
    },
    {
        "domain": "aesthetic_offline",
        "facet_ids": ["aesthetic.music_taste"],
        "probe_interval_days": 60,
    },
    {
        "domain": "temporal_behavioral",
        "facet_ids": ["temporal.deadline_behavior"],
        "probe_interval_days": 30,
    },
    {
        "domain": "emotional_relational",
        "facet_ids": ["emotional.resilience_pattern", "emotional.stress_response",
                      "emotional.frustration_triggers"],
        "probe_interval_days": 60,
    },
]

PROBE_PROMPT = """You are generating targeted check-in questions for a personal AI system.

The goal is to obtain SPECIFIC behavioral evidence for facets in the domain: {domain}

Target facets and their spectra:
{facets_info}

Generate 2-3 concrete, natural-sounding Italian questions that would reveal the subject's
position on these facets. Questions should feel like genuine curiosity, not a personality test.

Return JSON:
{{
  "questions": [
    {{
      "facet_id": "facet.id",
      "question": "Domanda in italiano (max 120 chars)"
    }}
  ]
}}

Rules:
- Each question must target exactly one facet_id from the list above
- Questions must be situational ("Quando... come...") not abstract ("Sei...?")
- No more than 3 questions total
- Italian only
"""


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def seed_schedule(db: sqlite3.Connection) -> None:
    """Insert default domains into domain_probe_schedule if not present."""
    for entry in DEFAULT_SCHEDULE:
        db.execute(
            """INSERT OR IGNORE INTO domain_probe_schedule
               (domain, facet_ids_json, probe_interval_days)
               VALUES (?, ?, ?)""",
            (entry["domain"], json.dumps(entry["facet_ids"]), entry["probe_interval_days"]),
        )
    db.commit()


def domains_due(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return domains due for probing based on interval and observation deficit."""
    rows = db.execute("SELECT * FROM domain_probe_schedule").fetchall()
    now = datetime.now(timezone.utc)
    due = []
    for row in rows:
        last = row["last_probe_at"]
        interval = row["probe_interval_days"]
        if last:
            last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if (now - last_dt).days < interval:
                continue  # not yet due by interval

        # Check mean observation count across domain facets
        facet_ids = json.loads(row["facet_ids_json"])
        if not facet_ids:
            continue

        placeholders = ",".join("?" * len(facet_ids))
        obs_rows = db.execute(
            f"SELECT observation_count FROM traits WHERE facet_id IN ({placeholders})",
            facet_ids,
        ).fetchall()
        if not obs_rows:
            mean_obs = 0
        else:
            mean_obs = sum(r["observation_count"] for r in obs_rows) / len(obs_rows)

        if mean_obs >= MIN_OBS_THRESHOLD:
            continue  # sufficient data already

        due.append({
            "domain": row["domain"],
            "facet_ids": facet_ids,
            "probe_interval_days": interval,
            "mean_obs": round(mean_obs, 1),
        })

    return due


def build_facets_info(db: sqlite3.Connection, facet_ids: list[str]) -> str:
    placeholders = ",".join("?" * len(facet_ids))
    rows = db.execute(
        f"SELECT id, spectrum_low, spectrum_high, description FROM facets WHERE id IN ({placeholders})",
        facet_ids,
    ).fetchall()
    lines = []
    for r in rows:
        lines.append(
            f"- {r['id']}: {r['description']} "
            f"({r['spectrum_low'] or '?'} ↔ {r['spectrum_high'] or '?'})"
        )
    return "\n".join(lines)


def _call_llm(prompt: str, model: str) -> dict[str, Any]:
    from lib.llm_resilience import chat_completion_content
    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": "Return STRICT JSON only. No markdown."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=600,
        temperature=0.3,
        timeout=LLM_TIMEOUT,
        title="Soulkiller Domain Prober",
    )
    import re
    s = content.strip()
    if s.startswith("```"):
        s = "\n".join(s.split("\n")[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON in response: {content[:100]}")
    return json.loads(s[start:end + 1])


def schedule_questions(db: sqlite3.Connection, questions: list[dict]) -> int:
    """Insert questions into checkin_exchanges for delivery, spaced 1 day apart."""
    now = datetime.now(timezone.utc)
    scheduled = 0
    for i, q in enumerate(questions):
        facet_id = q.get("facet_id", "")
        question_text = q.get("question", "").strip()
        if not facet_id or not question_text:
            continue
        # Check that the facet exists
        facet = db.execute("SELECT id FROM facets WHERE id=?", (facet_id,)).fetchone()
        if not facet:
            warn(SCRIPT, "unknown_facet", facet_id=facet_id)
            continue
        # Schedule delivery offset by i days
        ask_at = (now + timedelta(days=i)).isoformat()
        db.execute(
            """INSERT INTO checkin_exchanges (facet_id, question_text, asked_at)
               VALUES (?, ?, ?)""",
            (facet_id, question_text, ask_at),
        )
        scheduled += 1
    return scheduled


def run(model: str = DEFAULT_MODEL, dry_run: bool = False) -> None:
    db = get_db()
    try:
        seed_schedule(db)
        due = domains_due(db)

        if not due:
            info(SCRIPT, "no_domains_due")
            return

        info(SCRIPT, "domains_due", count=len(due), domains=[d["domain"] for d in due])

        total_scheduled = 0
        for domain_entry in due:
            domain = domain_entry["domain"]
            facet_ids = domain_entry["facet_ids"]
            facets_info = build_facets_info(db, facet_ids)

            prompt = PROBE_PROMPT.format(domain=domain, facets_info=facets_info)

            try:
                result = _call_llm(prompt, model)
            except Exception as e:
                warn(SCRIPT, "llm_error", domain=domain, error=str(e))
                continue

            questions = result.get("questions", [])

            if dry_run:
                print(f"\n[{domain}] mean_obs={domain_entry['mean_obs']}")
                for q in questions:
                    print(f"  [{q.get('facet_id')}] {q.get('question')}")
                continue

            n = schedule_questions(db, questions)
            total_scheduled += n

            # Update last_probe_at
            db.execute(
                "UPDATE domain_probe_schedule SET last_probe_at=? WHERE domain=?",
                (datetime.now(timezone.utc).isoformat(), domain),
            )
            info(SCRIPT, "domain_probed", domain=domain, questions_scheduled=n)

        if not dry_run:
            db.commit()
            info(SCRIPT, "run_complete", total_scheduled=total_scheduled)

    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Domain-Aware Probing Scheduler")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
