#!/usr/bin/env python3
"""Soulkiller Decision Extractor

Extracts the subject's decisions from inbox messages and promotes
decision-type episodes into the decisions table.

Decisions = explicit choices, preferences stated, plans confirmed.
Domains: tech, lavoro, relazioni, finanza, salute, lifestyle, altro

State: soulkiller/decisions-state.json
  {"last_processed_inbox_id": 0}

Cron: soulkiller:decisions, daily 04:15 Europe/Rome

Usage:
  python3 soulkiller_decisions.py [--model ...] [--dry-run] [--backfill]
"""

from __future__ import annotations
import os

import json
import http.client
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn

SCRIPT = "soulkiller_decisions"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
STATE_FILE = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "decisions-state.json"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT_SECONDS = 90
BATCH_SIZE = 15


# ---------------------------------------------------------------------------
# DB / state
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_processed_inbox_id": 0}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def insert_decision(db, decision: str, domain: str | None, facet_ids: list,
                    direction: str | None, source_type: str, source_ref: str,
                    decided_at: str | None, context: str | None) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.execute(
            """INSERT OR IGNORE INTO decisions
               (decision, domain, facet_ids, direction, source_type, source_ref,
                decided_at, extracted_at, context)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (decision, domain, json.dumps(facet_ids) if facet_ids else None,
             direction, source_type, source_ref, decided_at, now, context)
        )
        return db.execute("SELECT changes()").fetchone()[0] > 0
    except Exception as e:
        warn(SCRIPT, "insert_error", error=str(e))
        return False


# ---------------------------------------------------------------------------
# Migrate existing decision episodes
# ---------------------------------------------------------------------------

def migrate_decision_episodes(db) -> int:
    """Promote episode_type='decision' rows to decisions table."""
    rows = db.execute(
        """SELECT id, content, occurred_at, context, source_ref
           FROM episodes WHERE episode_type='decision'"""
    ).fetchall()

    inserted = 0
    for r in rows:
        source_ref = f"episode:{r['id']}"
        ok = insert_decision(
            db,
            decision=r['content'],
            domain=None,
            facet_ids=[],
            direction=None,
            source_type="episode_migration",
            source_ref=source_ref,
            decided_at=r['occurred_at'],
            context=r['context'],
        )
        if ok:
            inserted += 1

    db.commit()
    return inserted


# ---------------------------------------------------------------------------
# LLM extraction
# ---------------------------------------------------------------------------

DECISION_PROMPT = """Analyze these messages from the subject (Italian, 30s, tech worker).
Extract explicit decisions, choices, or stated preferences - moments where he
commits to something, rejects something, or states how he wants things to be.

Messages (each has [date] prefix):
{messages}

Return JSON:
{{
  "decisions": [
    {{
      "decision": "brief description of what he decided (1 sentence, Italian)",
      "domain": "tech|lavoro|relazioni|finanza|salute|lifestyle|altro",
      "direction": "low|high|ambiguous",
      "direction_confidence": 0.0-1.0,
      "decided_at": "YYYY-MM-DD or null",
      "context": "why this matters (1 sentence)"
    }}
  ]
}}

Rules:
- Only extract REAL decisions (not questions, not hypotheticals)
- Skip decisions already obvious from context (e.g., "asks for help" is not a decision)
- Include: technical choices, relationship boundaries, preferences stated as rules, plans confirmed
- direction: "low" = conservative/avoiding/reducing, "high" = proactive/ambitious/expanding,
  "ambiguous" = unclear or context-dependent
- direction_confidence: how certain you are of the direction classification (0.0-1.0)
- If nothing clear: return {{"decisions": []}}
"""


def _fix_json(s: str) -> str:
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    return s


def _parse_json(content: str) -> Any:
    s = content.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(lines[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON: {content[:80]}")
    end = s.rfind("}")
    for candidate in [s[start:end + 1], _fix_json(s[start:end + 1])]:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Unparseable: {content[:100]}")


def _call_llm(prompt: str, model: str) -> Any:
    from lib.llm_resilience import chat_completion_content

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": "Return STRICT JSON only."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=1500,
        temperature=0.1,
        timeout=LLM_TIMEOUT_SECONDS,
        title="Soulkiller Decisions",
    )
    return _parse_json(content)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        backfill: bool = False) -> None:
    db = get_db()
    try:
        # 1. Migrate existing decision episodes
        migrated = migrate_decision_episodes(db)
        if migrated:
            info(SCRIPT, "episodes_migrated", count=migrated)

        # 2. Extract from inbox
        state = load_state()
        last_id = 0 if backfill else state.get("last_processed_inbox_id", 0)

        rows = db.execute(
            """SELECT id, content, received_at FROM inbox
               WHERE id > ? AND length(content) > 20
               ORDER BY id ASC""",
            (last_id,)
        ).fetchall()

        messages = [dict(r) for r in rows]
        total = len(messages)

        if total == 0:
            info(SCRIPT, "no_new_messages")
            return

        info(SCRIPT, "run_start", messages=total, from_id=last_id)

        if dry_run:
            print(f"Would process {total} messages in {(total + BATCH_SIZE - 1) // BATCH_SIZE} batches")
            return

        total_decisions = 0
        for i in range(0, total, BATCH_SIZE):
            batch = messages[i:i + BATCH_SIZE]
            n = process_batch(
                batch, model, db,
                source_ref=f"inbox:{batch[0]['id']}-{batch[-1]['id']}"
            )
            total_decisions += n
            last_processed_id = batch[-1]["id"]
            save_state({"last_processed_inbox_id": last_processed_id})

        total_in_db = db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
        info(SCRIPT, "run_complete",
             new_decisions=total_decisions, total_in_db=total_in_db)

    finally:
        db.close()


def process_batch(messages: list[dict], model: str, db, source_ref: str) -> int:
    lines = [f"[{m['received_at'][:10]}] {m['content']}" for m in messages]
    prompt = DECISION_PROMPT.format(messages="\n".join(lines))
    try:
        result = _call_llm(prompt, model)
    except Exception as e:
        warn(SCRIPT, "llm_error", error=str(e))
        return 0

    inserted = 0
    for d in result.get("decisions", []):
        decision_text = d.get("decision", "").strip()
        if not decision_text:
            continue
        # Only store structured direction when confidence meets threshold (IMP-09)
        direction = d.get("direction")
        dir_conf = float(d.get("direction_confidence") or 0.0)
        if direction not in ("low", "high") or dir_conf < 0.6:
            direction = None
        ref = f"{source_ref}:{hash(decision_text) & 0xFFFFFF:06x}"
        ok = insert_decision(
            db, decision_text, d.get("domain"), [],
            direction, "inbox_batch", ref,
            d.get("decided_at"), d.get("context"),
        )
        if ok:
            inserted += 1
            info(SCRIPT, "decision_extracted",
                 domain=d.get("domain"), text=decision_text[:80])
    db.commit()
    return inserted


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Soulkiller Decision Extractor")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()
    run(model=args.model, dry_run=args.dry_run, backfill=args.backfill)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
