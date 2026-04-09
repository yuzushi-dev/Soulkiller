#!/usr/bin/env python3
"""Soulkiller Attachment Analyzer — ECR-R inspired attachment pattern detection.

Analyzes relationship data (episodes, entity_relations, checkin_exchanges, decisions)
to identify attachment anxiety/avoidance patterns per relationship context.

Populates: attachment_signals table + observations for relational.attachment_anxiety/avoidance

Cron: soulkiller:attachment, monthly 3° del mese 05:00 Europe/Rome

Usage:
  python3 soulkiller_attachment.py [--model ...] [--dry-run]
"""
from __future__ import annotations
import os

import json, http.client, re, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn
from soulkiller_run_guard import should_skip, mark_ran

SCRIPT = "soulkiller_attachment"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600

ATTACHMENT_PROMPT = """You are a clinical psychologist assessing the subject's attachment patterns (Italian, 30s, tech worker).

Using ECR-R (Experiences in Close Relationships), assess his attachment on two dimensions:
- anxiety: fear of abandonment, need for reassurance, intense reactions to perceived rejection (0=secure, 1=highly anxious)
- avoidance: discomfort with intimacy, preference for self-reliance, emotional distance (0=comfortable with closeness, 1=highly avoidant)

Assess SEPARATELY for each relationship context present in the data.

=== Episodes (relationship-related) ===
{episodes}

=== Entity relations (how he relates to people) ===
{entity_relations}

=== Checkin exchanges (what he said about relationships) ===
{checkin}

=== Decisions (relational choices) ===
{decisions}

Return JSON array — one object per relationship context:
[
  {{
    "relationship_context": "romantic",
    "anxiety_level": 0.3,
    "avoidance_level": 0.5,
    "secure_behaviors": ["expresses concern for partner's wellbeing", "shares personal changes openly"],
    "anxious_behaviors": ["may seek reassurance indirectly"],
    "avoidant_behaviors": ["focuses on technical topics when emotional proximity increases"],
    "evidence": "a close contact noticed positive changes in the subject; he shares progress with her but indirectly"
  }},
  {{
    "relationship_context": "family",
    ...
  }}
]

Relationship contexts to assess (only if there's meaningful evidence):
- romantic (partner)
- family (genitori, etc.)
- friends
- work/professional

Rules:
- anxiety_level: 0.0 (secure, no abandonment fear) to 1.0 (highly anxious/clingy)
- avoidance_level: 0.0 (comfortable with intimacy) to 1.0 (highly avoidant/self-reliant)
- Secure attachment = low anxiety + low avoidance
- If insufficient data for a context: skip it (don't include in array)
- Base assessment ONLY on the evidence provided
- secure_behaviors: list 1-3 observed behaviors pointing toward security
- anxious_behaviors: list 0-3 observed behaviors pointing toward anxiety
- avoidant_behaviors: list 0-3 observed behaviors pointing toward avoidance
"""

ATTACHMENT_FACET_MAP = {
    # anxiety → attachment_anxiety facet (1.0 = fully anxious pole)
    "anxiety": "relational.attachment_anxiety",
    # avoidance → attachment_avoidance facet (1.0 = fully avoidant pole)
    "avoidance": "relational.attachment_avoidance",
}


def _fix_json(s: str) -> str:
    return re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)


def _parse_json(content: str) -> Any:
    s = content.strip()
    if s.startswith("```"):
        s = "\n".join(s.split("\n")[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    start = s.find("[")
    if start == -1:
        raise ValueError(f"No JSON array: {content[:80]}")
    end = s.rfind("]")
    for c in [s[start:end+1], _fix_json(s[start:end+1])]:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            pass
    raise ValueError(f"Unparseable: {content[:200]}")


def _call_llm(prompt: str, model: str) -> Any:
    provider, model_id = model.split("/", 1)
    config = load_nanobot_config()
    cfg = (config.get("providers") or {}).get(provider)
    if not cfg:
        raise ValueError(f"Provider {provider} not found")
    parsed = urllib.parse.urlparse(cfg["apiBase"])
    use_https = parsed.scheme.lower() == "https"
    conn_cls = http.client.HTTPSConnection if use_https else http.client.HTTPConnection
    conn = conn_cls(parsed.netloc, timeout=LLM_TIMEOUT)
    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": "Return STRICT JSON only. No reasoning, no markdown."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }
    headers = {"Content-Type": "application/json"}
    if cfg.get("apiKey"):
        headers["Authorization"] = f"Bearer {cfg['apiKey']}"
    try:
        conn.request("POST", f"/{parsed.path.lstrip('/')}/chat/completions",
                     json.dumps(payload), headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(f"API {resp.status}: {body[:300]}")
        msg = json.loads(body)["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning") or ""
        return _parse_json(content)
    finally:
        conn.close()


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def store_attachment(db, r: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    ctx = r["relationship_context"]
    db.execute("""
        INSERT INTO attachment_signals
            (relationship_context, anxiety_level, avoidance_level,
             secure_behaviors, anxious_behaviors, avoidant_behaviors,
             evidence, source_refs, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(relationship_context) DO UPDATE SET
            anxiety_level=excluded.anxiety_level,
            avoidance_level=excluded.avoidance_level,
            secure_behaviors=excluded.secure_behaviors,
            anxious_behaviors=excluded.anxious_behaviors,
            avoidant_behaviors=excluded.avoidant_behaviors,
            evidence=excluded.evidence,
            updated_at=excluded.updated_at
    """, (
        ctx,
        float(r.get("anxiety_level", 0.5)),
        float(r.get("avoidance_level", 0.5)),
        json.dumps(r.get("secure_behaviors", [])),
        json.dumps(r.get("anxious_behaviors", [])),
        json.dumps(r.get("avoidant_behaviors", [])),
        r.get("evidence", ""),
        json.dumps(["attachment_analysis"]),
        now
    ))

    # Generate facet observations — use average across contexts for each dimension
    source_ref = f"attachment:{ctx}"
    anxiety = float(r.get("anxiety_level", 0.5))
    avoidance = float(r.get("avoidance_level", 0.5))

    # Confidence lower for single context — will be averaged across contexts at trait level
    conf = 0.55

    for dim_val, facet_id in [
        (anxiety,   "relational.attachment_anxiety"),
        (avoidance, "relational.attachment_avoidance"),
    ]:
        db.execute("""
            INSERT INTO observations
                (facet_id, signal_position, signal_strength, content, source_type, source_ref, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(facet_id, source_ref) DO UPDATE SET
                signal_position=excluded.signal_position,
                signal_strength=excluded.signal_strength,
                content=excluded.content
        """, (
            facet_id, dim_val, conf,
            f"Attachment [{ctx}]: {r.get('evidence', '')[:100]}",
            "attachment_analysis", source_ref,
            datetime.now(timezone.utc).isoformat()
        ))


def run(model: str = DEFAULT_MODEL, dry_run: bool = False) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "attachment"):
            return
        # Load relational data
        episodes = db.execute("""
            SELECT episode_type, content, occurred_at, context FROM episodes
            WHERE active=1 AND (
                content LIKE '%a close contact%' OR content LIKE '%a close contact%' OR
                content LIKE '%genitor%' OR content LIKE '%mamma%' OR
                content LIKE '%papà%' OR content LIKE '%relaz%' OR
                content LIKE '%amore%' OR content LIKE '%partner%' OR
                content LIKE '%amico%' OR content LIKE '%amici%' OR
                episode_type IN ('relationship_update', 'emotional_moment')
            )
            ORDER BY confidence DESC LIMIT 30
        """).fetchall()

        entity_rels = db.execute("""
            SELECT e.name, e.entity_type, e.label, er.relation_type,
                   er.dynamic, er.sentiment, er.evidence
            FROM entity_relations er
            JOIN entities e ON er.entity_id=e.id
            WHERE e.entity_type='person'
            ORDER BY e.mention_count DESC
        """).fetchall()

        checkin = db.execute("""
            SELECT facet_id, question_text, reply_text FROM checkin_exchanges
            WHERE reply_text IS NOT NULL
              AND (facet_id LIKE 'relational.%' OR facet_id LIKE 'emotional.%'
                   OR reply_text LIKE '%a close contact%' OR reply_text LIKE '%a close contact%'
                   OR reply_text LIKE '%genitor%')
            ORDER BY id DESC LIMIT 20
        """).fetchall()

        decisions = db.execute("""
            SELECT decision, domain, context FROM decisions
            WHERE domain IN ('relazioni', 'salute')
            ORDER BY id DESC LIMIT 20
        """).fetchall()

        ep_text  = "\n".join(f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes) or "(none)"
        er_text  = "\n".join(
            f"- {r['name']} [{r['relation_type'] or r['label']}]: dynamic={r['dynamic']} sent={r['sentiment']} | {(r['evidence'] or '')[:80]}"
            for r in entity_rels
        ) or "(none)"
        ck_text  = "\n".join(
            f"- [{r['facet_id']}] Q: {r['question_text'][:60]} | A: {r['reply_text'][:80]}"
            for r in checkin
        ) or "(none)"
        dec_text = "\n".join(f"- {r['decision'][:80]}" for r in decisions) or "(none)"

        prompt = ATTACHMENT_PROMPT.format(
            episodes=ep_text, entity_relations=er_text,
            checkin=ck_text, decisions=dec_text
        )

        if dry_run:
            print(f"Episodes: {len(episodes)}, EntityRels: {len(entity_rels)}, Checkin: {len(checkin)}, Decisions: {len(decisions)}")
            print(f"Prompt: {len(prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start", episodes=len(episodes), entity_rels=len(entity_rels))
        results = _call_llm(prompt, model)

        if not isinstance(results, list):
            warn(SCRIPT, "unexpected_format", got=type(results).__name__)
            return

        for r in results:
            if "relationship_context" not in r:
                continue
            store_attachment(db, r)
            info(SCRIPT, "context_stored",
                 context=r["relationship_context"],
                 anxiety=round(float(r.get("anxiety_level", 0.5)), 2),
                 avoidance=round(float(r.get("avoidance_level", 0.5)), 2))

        mark_ran(db, "attachment")
        db.commit()
        info(SCRIPT, "run_complete", contexts=len(results))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Attachment Analyzer")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
