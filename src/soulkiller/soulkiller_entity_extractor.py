#!/usr/bin/env python3
"""Soulkiller Entity & Episode Extractor

Scans inbox messages and extracts:
  - Entities: people, projects, places, objects, organizations
  - Episodes: significant events, decisions, emotional moments
  - Relations: how entities relate to the subject

Cron: soulkiller:entity-extract, daily 04:00 Europe/Rome

State file: soulkiller/entity-extractor-state.json
  {"last_processed_inbox_id": 0}
"""

from __future__ import annotations

import json
import http.client
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import re

from lib.config import load_nanobot_config
from lib.log import info, warn, error

SCRIPT = "soulkiller_entity_extractor"
LLM_TIMEOUT_SECONDS = 150
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
BATCH_SIZE = 10          # inbox messages per LLM call
STATE_FILE = Path(__file__).resolve().parents[1] / "soulkiller" / "entity-extractor-state.json"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_processed_inbox_id": 0}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def upsert_entity(db, entity_type: str, name: str, label: str | None,
                  description: str | None, seen_at: str,
                  metadata: dict | None = None) -> int:
    """Insert or update entity. Returns entity id."""
    existing = db.execute(
        "SELECT id, mention_count FROM entities WHERE entity_type=? AND name=?",
        (entity_type, name)
    ).fetchone()

    if existing:
        db.execute(
            """UPDATE entities SET
               last_seen_at=?, mention_count=mention_count+1,
               description=COALESCE(?, description),
               label=COALESCE(?, label)
               WHERE id=?""",
            (seen_at, description, label, existing["id"])
        )
        return existing["id"]
    else:
        cur = db.execute(
            """INSERT INTO entities
               (entity_type, name, label, description, first_seen_at, last_seen_at, mention_count, metadata)
               VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
            (entity_type, name, label, description, seen_at, seen_at,
             json.dumps(metadata) if metadata else None)
        )
        return cur.lastrowid


def insert_episode(db, episode_type: str, content: str, source_type: str,
                   source_ref: str, confidence: float, occurred_at: str | None,
                   entity_names: list[str], context: str | None) -> int:
    # UNIQUE(episode_type, source_ref) — use numbered suffix to allow multiple per source
    import hashlib
    content_hash = hashlib.md5(content.encode()).hexdigest()[:6]
    unique_ref = f"{source_ref}:{content_hash}"
    cur = db.execute(
        """INSERT OR IGNORE INTO episodes
           (episode_type, content, source_type, source_ref, confidence,
            occurred_at, extracted_at, entity_names, context)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (episode_type, content, source_type, unique_ref, confidence,
         occurred_at, datetime.now(timezone.utc).isoformat(),
         json.dumps(entity_names), context)
    )
    return cur.lastrowid


def upsert_relation(db, entity_id: int, relation_type: str, dynamic: str | None,
                    sentiment: float | None, evidence: str, source_ref: str) -> None:
    existing = db.execute(
        "SELECT id FROM entity_relations WHERE entity_id=? AND relation_type=?",
        (entity_id, relation_type)
    ).fetchone()
    now = datetime.now(timezone.utc).isoformat()
    if existing:
        db.execute(
            """UPDATE entity_relations SET dynamic=COALESCE(?, dynamic),
               sentiment=COALESCE(?, sentiment), evidence=?, updated_at=?
               WHERE id=?""",
            (dynamic, sentiment, evidence, now, existing["id"])
        )
    else:
        db.execute(
            """INSERT OR IGNORE INTO entity_relations
               (entity_id, relation_type, dynamic, sentiment, evidence, source_ref, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entity_id, relation_type, dynamic, sentiment, evidence, source_ref, now)
        )


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _fix_json(s: str) -> str:
    """Fix common glm4.7 JSON issues: missing commas between array/object items."""
    # Missing comma between } and { in arrays:  }\n  { -> },\n  {
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    # Missing comma between ] and {
    s = re.sub(r'(\])\s*\n(\s*\{)', r'\1,\n\2', s)
    # Missing comma between } and [
    s = re.sub(r'(\})\s*\n(\s*\[)', r'\1,\n\2', s)
    # Missing comma between " and " on separate lines (string values followed by key)
    s = re.sub(r'(")\s*\n(\s*")', r'\1,\n\2', s)
    return s


def _parse_json_robust(content: str) -> dict[str, Any]:
    """Parse JSON with fallbacks for glm4.7 quirks."""
    s = content.strip()

    # Strip markdown fences
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()

    # Find JSON boundaries
    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON object: {content[:100]}")

    # Try progressively: raw, fixed, truncation-recovery
    end = s.rfind("}")
    candidates = []
    if end > start:
        candidates.append(s[start:end + 1])
        candidates.append(_fix_json(s[start:end + 1]))

    # Truncation recovery: scan backwards for each } to find last complete object
    for i in range(len(s) - 1, start, -1):
        if s[i] == '}':
            attempt = _fix_json(s[start:i + 1])
            # Try to close open arrays/objects
            for suffix in [']}', ']}', ']}}', '}}']:
                try:
                    return json.loads(attempt + suffix)
                except json.JSONDecodeError:
                    pass
            candidates.append(attempt)
            break

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Unparseable JSON after all fixes: {content[:200]}")


def _call_llm(prompt: str, model: str) -> dict[str, Any]:
    from lib.llm_resilience import chat_completion_content

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": (
                "You are an expert at extracting structured personal context from conversations. "
                "Return STRICT JSON only. No reasoning, no markdown, just JSON."
            )},
            {"role": "user", "content": prompt},
        ],
        max_tokens=3000,
        temperature=0.1,
        timeout=LLM_TIMEOUT_SECONDS,
        title="Soulkiller Entity Extractor",
    )
    return _parse_json_robust(content)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """Analyze these messages from the subject (Italian user, 30s, tech worker).
Extract entities and episodes relevant to understanding his life context.

Messages (each has [date] prefix):
{messages}

Return JSON with this exact structure:
{{
  "entities": [
    {{
      "type": "person|project|place|organization|object",
      "name": "exact name as used",
      "label": "short role/description (e.g. 'compagna', 'robot aspirapolvere', 'progetto AI')",
      "description": "1 sentence about this entity in context",
      "relation_to_daniele": "partner|colleague|friend|project|tool|place|other",
      "sentiment": 0.0-1.0,
      "dynamic": "brief note on the relationship dynamic"
    }}
  ],
  "episodes": [
    {{
      "type": "event|decision|emotional_moment|project_milestone|relationship_update",
      "content": "what happened, 1-2 sentences",
      "occurred_at": "YYYY-MM-DD or null if unknown",
      "confidence": 0.0-1.0,
      "entity_names": ["name1", "name2"],
      "context": "why this matters for understanding the subject"
    }}
  ]
}}

Rules:
- Only extract entities mentioned with enough context to be meaningful
- People: include only real people (not AI/bots)
- Episodes: only significant moments (decisions, problems solved, emotions expressed, relationship dynamics)
- Skip generic technical discussions unless they reveal character
- If nothing significant: return {{"entities": [], "episodes": []}}
"""


def build_prompt(messages: list[dict]) -> str:
    lines = []
    for m in messages:
        date = m["received_at"][:10]
        lines.append(f"[{date}] {m['content']}")
    return EXTRACTION_PROMPT.format(messages="\n".join(lines))


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_batch(messages: list[dict], model: str, db) -> tuple[int, int]:
    """Process one batch. Returns (entities_added, episodes_added)."""
    prompt = build_prompt(messages)

    try:
        result = _call_llm(prompt, model)
    except Exception as e:
        warn(SCRIPT, "llm_error", error=str(e))
        return 0, 0

    entities_added = 0
    episodes_added = 0

    # Reference date from batch
    ref_date = messages[-1]["received_at"]
    source_ref = f"inbox:{messages[0]['id']}-{messages[-1]['id']}"

    # Process entities
    entity_id_map: dict[str, int] = {}
    for ent in result.get("entities", []):
        name = ent.get("name", "").strip()
        etype = ent.get("type", "other")
        if not name:
            continue

        eid = upsert_entity(
            db,
            entity_type=etype,
            name=name,
            label=ent.get("label"),
            description=ent.get("description"),
            seen_at=ref_date,
        )
        entity_id_map[name] = eid

        # Upsert relation to the subject
        rel = ent.get("relation_to_daniele", "other")
        dynamic = ent.get("dynamic")
        sentiment = ent.get("sentiment")
        evidence = ent.get("description", "")
        upsert_relation(db, eid, rel, dynamic, sentiment, evidence, source_ref)
        entities_added += 1

    # Process episodes
    for ep in result.get("episodes", []):
        content = ep.get("content", "").strip()
        if not content:
            continue
        ep_entity_names = ep.get("entity_names", [])
        insert_episode(
            db,
            episode_type=ep.get("type", "event"),
            content=content,
            source_type="inbox_batch",
            source_ref=source_ref,
            confidence=float(ep.get("confidence", 0.7)),
            occurred_at=ep.get("occurred_at"),
            entity_names=ep_entity_names,
            context=ep.get("context"),
        )
        episodes_added += 1

    db.commit()
    return entities_added, episodes_added


def run(model: str = DEFAULT_MODEL, full_backfill: bool = False) -> None:
    state = load_state()
    last_id = 0 if full_backfill else state.get("last_processed_inbox_id", 0)

    db = get_db()
    try:
        # Load unprocessed inbox messages with enough content
        rows = db.execute(
            """SELECT id, content, received_at FROM inbox
               WHERE id > ? AND length(content) > 15
               ORDER BY id ASC""",
            (last_id,)
        ).fetchall()

        messages = [dict(r) for r in rows]
        total = len(messages)

        if total == 0:
            info(SCRIPT, "no_new_messages")
            return

        info(SCRIPT, "run_start", messages=total, from_id=last_id)

        total_entities = 0
        total_episodes = 0
        processed = 0

        for i in range(0, total, BATCH_SIZE):
            batch = messages[i:i + BATCH_SIZE]
            ents, eps = process_batch(batch, model, db)
            total_entities += ents
            total_episodes += eps
            processed += len(batch)
            last_processed_id = batch[-1]["id"]

            info(SCRIPT, "batch_done",
                 batch=i // BATCH_SIZE + 1,
                 entities=ents, episodes=eps,
                 last_id=last_processed_id)

            # Save state after each batch in case of crash
            save_state({"last_processed_inbox_id": last_processed_id})

        info(SCRIPT, "run_complete",
             messages_processed=processed,
             entities_total=total_entities,
             episodes_total=total_episodes)

    finally:
        db.close()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Soulkiller Entity & Episode Extractor")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="LLM model (e.g., openrouter/meta-llama/llama-3.3-70b-instruct:free)")
    parser.add_argument("--backfill", action="store_true",
                        help="Reprocess all inbox messages from the beginning")
    args = parser.parse_args()

    run(model=args.model, full_backfill=args.backfill)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
