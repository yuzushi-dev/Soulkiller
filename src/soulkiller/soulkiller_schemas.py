#!/usr/bin/env python3
"""Soulkiller Schema Detector — Early Maladaptive Schemas (Young 1990).

Analizza il corpus inbox per rilevare i 18 schemi di Young.

Cron: soulkiller:schemas, monthly 1° del mese 05:00 Europe/Rome

Usage:
  python3 soulkiller_schemas.py [--model ...] [--dry-run] [--sample N]
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

SCRIPT = "soulkiller_schemas"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
CONSENSUS_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 200


SCHEMA_PROMPT = """You are a clinical psychologist analyzing the subject's messages (Italian, 30s, tech worker).
Identify which of Young's Early Maladaptive Schemas are active, based on evidence in the messages.

The 18 schemas and their behavioral/linguistic indicators:
- abandonment: fear of losing people, reassurance-seeking, intense reactions to perceived rejection
- mistrust_abuse: default suspicion of others, tests people before trusting, sees betrayal in ambiguity
- emotional_deprivation: feels nobody truly understands him, disappointment that needs aren't met
- defectiveness: shame about perceived flaws, fear of being "found out"
- social_isolation: feels fundamentally different from others, not belonging
- dependence: feels unable to manage without others' input/validation
- vulnerability: hypervigilance about threats, worries about catastrophe, health anxiety
- enmeshment: identity fused with another person
- failure: believes he can't succeed as well as peers
- entitlement: believes rules don't apply to him, impatient when others don't meet his standards
- insufficient_self_control: difficulty delaying gratification, avoids discomfort
- subjugation: suppresses needs to avoid conflict
- self_sacrifice: prioritizes others' needs, guilt when not helping
- approval_seeking: decisions based on others' approval
- negativity: focuses on negative, minimizes positive, pessimistic
- emotional_inhibition: suppresses emotions, discomfort with emotional expression
- unrelenting_standards: unrealistically high standards for self/others, perfectionism
- punitiveness: harsh self-criticism, difficulty forgiving self/others

Messages (last {n} from the subject):
{messages}

Return a JSON array — only schemas with clear evidence (skip absent/unclear ones):
[
  {{
    "schema_name": "mistrust_abuse",
    "schema_domain": "disconnection",
    "activation_level": 0.8,
    "confidence": 0.75,
    "trigger_contexts": ["receiving feedback", "new collaborations"],
    "behavioral_signatures": ["tests people before trusting", "suspicious of positive intentions"],
    "evidence": "Quote or paraphrase: 2-3 specific examples from messages"
  }}
]

Rules:
- activation_level: 0.0-1.0 (how strongly activated in daily behavior)
- confidence: 0.0-1.0 (how certain you are from evidence)
- Only include schemas with confidence >= 0.55
- Maximum 6 schemas (the most active ones)
- Evidence must quote actual message content
- schema_domain must be one of: disconnection, impaired_autonomy, impaired_limits, other_directedness, overvigilance
"""


def _fix_json(s: str) -> str:
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    s = re.sub(r'(\])\s*\n(\s*\{)', r'\1,\n\2', s)
    return s


def _parse_json(content: str) -> Any:
    s = content.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(lines[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()
    start = s.find("[")
    if start == -1:
        raise ValueError(f"No JSON array: {content[:80]}")
    end = s.rfind("]")
    candidates = [s[start:end+1], _fix_json(s[start:end+1])]
    objects = []
    depth = 0
    obj_start = None
    for i, ch in enumerate(s[start:end + 1], start):
        if ch == "{":
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == "}":
            depth = max(depth - 1, 0)
            if depth == 0 and obj_start is not None:
                fragment = _fix_json(s[obj_start:i + 1])
                try:
                    objects.append(json.loads(fragment))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    if objects:
        candidates.append(objects)
    for c in candidates:
        if isinstance(c, list):
            return c
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
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


def load_recent_messages(db, n: int) -> list[str]:
    rows = db.execute(
        """SELECT content FROM inbox WHERE from_id=? AND length(content) > 10
           ORDER BY id DESC LIMIT ?""",
        (SUBJECT_FROM_ID, n)
    ).fetchall()
    return [r["content"] for r in reversed(rows)]


SCHEMA_FACET_MAP = {
    "mistrust_abuse":          ("relational.trust_formation",         0.0),
    "abandonment":             ("relational.attachment_anxiety",      1.0),
    "emotional_inhibition":    ("emotional.emotional_expression",     0.1),
    "unrelenting_standards":   ("meta_cognition.uncertainty_tolerance", 0.1),
    "vulnerability":           ("cognitive.risk_tolerance",           0.1),
    "entitlement":             ("values.authority_stance",            0.0),
    "approval_seeking":        ("relational.help_seeking",            0.9),
    "emotional_deprivation":   ("relational.vulnerability_capacity",  0.1),
    "defectiveness":           ("meta_cognition.self_awareness",      0.3),
    "failure":                 ("meta_cognition.growth_mindset",      0.1),
    "insufficient_self_control": ("temporal.delay_discounting",       0.8),
}


def store_schema(db, s: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    consensus = 1 if s.get("_consensus", True) else 0
    db.execute("""
        INSERT INTO schemas
            (schema_name, schema_domain, activation_level, confidence,
             trigger_contexts, behavioral_signatures, evidence,
             first_detected_at, updated_at, consensus)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(schema_name) DO UPDATE SET
            activation_level=excluded.activation_level,
            confidence=excluded.confidence,
            trigger_contexts=excluded.trigger_contexts,
            behavioral_signatures=excluded.behavioral_signatures,
            evidence=excluded.evidence,
            updated_at=excluded.updated_at,
            consensus=excluded.consensus
    """, (
        s["schema_name"], s.get("schema_domain", "unknown"),
        float(s["activation_level"]), float(s["confidence"]),
        json.dumps(s.get("trigger_contexts", [])),
        json.dumps(s.get("behavioral_signatures", [])),
        s.get("evidence", ""),
        now, now, consensus
    ))

    if s["schema_name"] in SCHEMA_FACET_MAP:
        facet_id, signal_pos = SCHEMA_FACET_MAP[s["schema_name"]]
        db.execute("""
            INSERT INTO observations
                (facet_id, signal_position, signal_strength, content, source_type, source_ref, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(facet_id, source_ref) DO UPDATE SET
                signal_position=excluded.signal_position,
                signal_strength=excluded.signal_strength,
                content=excluded.content
        """, (
            facet_id, signal_pos, float(s["confidence"]) * 0.6,
            f"Schema {s['schema_name']} (activation={float(s['activation_level']):.2f}): {s.get('evidence', '')[:100]}",
            "schema_detection", f"schema:{s['schema_name']}",
            datetime.now(timezone.utc).isoformat()
        ))


def _consensus_merge(primary: list, secondary: list, threshold: float = 0.55) -> list:
    """Merge two model outputs: keep items where both agree (IMP-10).

    A schema is accepted if:
    - Both models detected it with confidence >= threshold
    - The averaged confidence is used for storage
    Items detected by only one model are stored with consensus=False (lowered confidence).
    """
    primary_map = {s["schema_name"]: s for s in primary if float(s.get("confidence", 0)) >= threshold}
    secondary_map = {s["schema_name"]: s for s in secondary if float(s.get("confidence", 0)) >= threshold}

    accepted = []
    for name, s in primary_map.items():
        s2 = secondary_map.get(name)
        if s2:
            # Both agree — average confidence
            s = dict(s)
            s["confidence"] = (float(s["confidence"]) + float(s2["confidence"])) / 2
            s["_consensus"] = True
        else:
            # Only primary detected it — lower confidence
            s = dict(s)
            s["confidence"] = float(s["confidence"]) * 0.7
            s["_consensus"] = False
        accepted.append(s)

    # Also add secondary-only detections (lowered confidence)
    for name, s2 in secondary_map.items():
        if name not in primary_map:
            s2 = dict(s2)
            s2["confidence"] = float(s2["confidence"]) * 0.7
            s2["_consensus"] = False
            accepted.append(s2)

    return accepted


def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "schemas"):
            return
        messages = load_recent_messages(db, sample)
        if len(messages) < 20:
            warn(SCRIPT, "insufficient_messages", count=len(messages))
            return

        info(SCRIPT, "run_start", sample=len(messages))

        # Cap to 60 messages to keep prompt within model's reasoning budget
        capped = messages[-60:] if len(messages) > 60 else messages
        sample_text = "\n".join(f"- {m[:120]}" for m in capped)
        prompt = SCHEMA_PROMPT.format(n=len(capped), messages=sample_text)

        if dry_run:
            print(f"Would analyze {len(messages)} messages with {model}")
            print(f"Prompt length: {len(prompt)} chars")
            return

        results = _call_llm(prompt, model)
        if not isinstance(results, list):
            warn(SCRIPT, "unexpected_format", got=type(results).__name__)
            return

        # IMP-10: second model consensus check
        try:
            results2 = _call_llm(prompt, CONSENSUS_MODEL)
            if isinstance(results2, list):
                results = _consensus_merge(results, results2)
                info(SCRIPT, "consensus_applied", primary=len(results), secondary=len(results2))
        except Exception as e:
            warn(SCRIPT, "consensus_model_error", error=str(e))
            # Fall through with primary results only

        for s in results:
            if float(s.get("confidence", 0)) < 0.55:
                continue
            store_schema(db, s)
            info(SCRIPT, "schema_detected",
                 name=s["schema_name"], domain=s.get("schema_domain"),
                 activation=round(float(s["activation_level"]), 2),
                 confidence=round(float(s["confidence"]), 2),
                 consensus=s.get("_consensus", True))

        mark_ran(db, "schemas")
        db.commit()
        info(SCRIPT, "run_complete", schemas_detected=len(results))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Schema Detector")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
