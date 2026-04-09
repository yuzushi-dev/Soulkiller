#!/usr/bin/env python3
"""Soulkiller Personal Constructs — Kelly's Repertory Grid Framework.

Identifies the subject's unique bipolar evaluative dimensions (personal constructs)
from how he evaluates people, situations, and experiences.

Populates: personal_constructs table + observations for
           cognitive.construct_complexity

Cron: soulkiller:constructs, monthly 5th 06:00 Europe/Rome

Usage:
  python3 soulkiller_constructs.py [--model ...] [--dry-run] [--sample N]
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

SCRIPT = "soulkiller_constructs"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 300
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 80

CONSTRUCT_PROMPT = """You are a psychologist using George Kelly's Personal Construct Theory to analyze the subject's (Italian, 30s, tech worker) evaluative framework.

Personal constructs are the BIPOLAR DIMENSIONS a person uses to construe their world. They are like the axes of their personal coordinate system for understanding everything. Examples: "competente—incompetente", "autentico—falso", "libero—costretto".

Identify the subject's personal constructs from how he evaluates people, situations, projects, and experiences in his messages.

=== the subject's messages ===
{messages}

=== How he relates to people (entity relations) ===
{entity_relations}

=== His decisions (reveal evaluative criteria) ===
{decisions}

=== His episodes (reveal what matters) ===
{episodes}

Return JSON array of personal constructs:
[
  {{
    "construct_name": "competente-incompetente",
    "pole_positive": "competente",
    "pole_negative": "incompetente",
    "superordinate": true,
    "range_of_convenience": ["tech", "lavoro", "persone"],
    "permeability": 0.7,
    "usage_frequency": 0.8,
    "evidence": "Frequently evaluates tools/people by competence: 'questo tool e' fatto bene', 'non sa cosa sta facendo'"
  }}
]

Guidelines:
- Extract 5-10 constructs (the most prominent)
- superordinate: true if this is a PRIMARY organizing dimension (max 2-3)
- range_of_convenience: which domains this construct applies to
- permeability: 0 = rigid (only applies to known things), 1 = open (applies to new things too)
- usage_frequency: 0-1 how often this construct appears in his evaluations
- pole_positive / pole_negative: in Italian
- Evidence must quote or closely paraphrase his words
- Look for: comparisons ("X e' piu'... di Y"), evaluations ("questo e'..."), preferences ("preferisco... non mi piace quando...")
- Constructs are PERSONAL — they may not match standard psychological dimensions
"""


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _fix_json(s: str) -> str:
    return re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)


def _parse_json(content: str) -> Any:
    s = content.strip()
    if s.startswith("```"):
        s = "\n".join(s.split("\n")[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    # Try array first, then object
    for start_char, end_char in [("[", "]"), ("{", "}")]:
        start = s.find(start_char)
        if start == -1:
            continue
        end = s.rfind(end_char)
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
        for field in ["content", "reasoning"]:
            text = msg.get(field, "")
            if text and ("[" in text or "{" in text):
                try:
                    return _parse_json(text)
                except ValueError:
                    continue
        combined = (msg.get("content") or "") + (msg.get("reasoning") or "")
        return _parse_json(combined)
    finally:
        conn.close()


def store_construct(db, c: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO personal_constructs
            (construct_name, pole_positive, pole_negative, superordinate,
             range_of_convenience, permeability, usage_frequency,
             evidence, first_detected_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(construct_name) DO UPDATE SET
            pole_positive=excluded.pole_positive,
            pole_negative=excluded.pole_negative,
            superordinate=excluded.superordinate,
            range_of_convenience=excluded.range_of_convenience,
            permeability=excluded.permeability,
            usage_frequency=excluded.usage_frequency,
            evidence=excluded.evidence,
            updated_at=excluded.updated_at
    """, (
        c["construct_name"],
        c["pole_positive"],
        c["pole_negative"],
        1 if c.get("superordinate") else 0,
        json.dumps(c.get("range_of_convenience", [])),
        float(c.get("permeability", 0.5)),
        float(c.get("usage_frequency", 0.5)),
        c.get("evidence", ""),
        now, now
    ))


def derive_construct_observations(db, constructs: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    source_ref = "constructs:overall"

    n = len(constructs)
    if n == 0:
        return

    # Complexity: number of constructs (more = more complex)
    # + average permeability (more permeable = more flexible)
    count_norm = min(1.0, n / 10.0)
    avg_perm = sum(float(c.get("permeability", 0.5)) for c in constructs) / n
    complexity = count_norm * 0.6 + avg_perm * 0.4

    db.execute("""
        INSERT INTO observations
            (facet_id, signal_position, signal_strength, content,
             source_type, source_ref, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(facet_id, source_ref) DO UPDATE SET
            signal_position=excluded.signal_position,
            signal_strength=excluded.signal_strength,
            content=excluded.content
    """, (
        "cognitive.construct_complexity", round(complexity, 3), 0.50,
        f"Personal constructs: {n} identified, avg_permeability={avg_perm:.2f}, "
        f"superordinate={sum(1 for c in constructs if c.get('superordinate'))}",
        "construct_analysis", source_ref, now
    ))


def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "constructs"):
            return
        messages = db.execute(
            """SELECT content FROM inbox WHERE from_id=? AND length(content) > 20
               ORDER BY id DESC LIMIT ?""",
            (SUBJECT_FROM_ID, sample)
        ).fetchall()

        entity_rels = db.execute(
            """SELECT e.name, er.relation_type, er.dynamic, er.sentiment, er.evidence
               FROM entity_relations er JOIN entities e ON er.entity_id=e.id
               WHERE e.entity_type='person'
               ORDER BY e.mention_count DESC LIMIT 15"""
        ).fetchall()

        decisions = db.execute(
            """SELECT decision, domain, context FROM decisions
               ORDER BY id DESC LIMIT 20"""
        ).fetchall()

        episodes = db.execute(
            """SELECT episode_type, content FROM episodes
               WHERE active=1 ORDER BY confidence DESC LIMIT 20"""
        ).fetchall()

        msg_text = "\n".join(f"- {r['content'][:120]}" for r in reversed(list(messages)))
        er_text = "\n".join(
            f"- {r['name']} [{r['relation_type']}]: {r['dynamic']} (sent={r['sentiment']})"
            for r in entity_rels
        ) or "(none)"
        dec_text = "\n".join(
            f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions
        ) or "(none)"
        ep_text = "\n".join(
            f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes
        ) or "(none)"

        prompt = CONSTRUCT_PROMPT.format(
            messages=msg_text, entity_relations=er_text,
            decisions=dec_text, episodes=ep_text
        )

        if dry_run:
            print(f"Messages: {len(messages)}, EntityRels: {len(entity_rels)}, "
                  f"Decisions: {len(decisions)}, Episodes: {len(episodes)}")
            print(f"Prompt: {len(prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start", messages=len(messages))

        try:
            results = _call_llm(prompt, model)
        except Exception as e:
            warn(SCRIPT, "llm_error", error=str(e))
            return

        if not isinstance(results, list):
            warn(SCRIPT, "unexpected_format", got=type(results).__name__)
            return

        stored = 0
        for c in results:
            if not c.get("construct_name") or not c.get("pole_positive"):
                continue
            store_construct(db, c)
            stored += 1
            info(SCRIPT, "construct_stored",
                 name=c["construct_name"],
                 superordinate=bool(c.get("superordinate")),
                 frequency=round(float(c.get("usage_frequency", 0.5)), 2))

        derive_construct_observations(db, results)
        mark_ran(db, "constructs")
        db.commit()
        info(SCRIPT, "run_complete", constructs=stored)
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Personal Constructs Analyzer")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
