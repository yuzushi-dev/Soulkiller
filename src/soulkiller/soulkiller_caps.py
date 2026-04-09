#!/usr/bin/env python3
"""Soulkiller CAPS — Situation-Behavior Signatures (Mischel & Shoda 1995).

Sintetizza le firme "se-allora" (if-then) di the subject dai cluster contestuali
e dai pattern osservati nel corpus. Salva in caps_signatures.

Cron: soulkiller:caps, monthly 2° del mese 05:30 Europe/Rome

Usage:
  python3 soulkiller_caps.py [--model ...] [--dry-run] [--sample N]
"""
from __future__ import annotations
import os

import json, http.client, re, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn

SCRIPT = "soulkiller_caps"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
SAMPLE_LIMIT = 12

CAPS_PROMPT = """You are a personality scientist applying Mischel & Shoda's CAPS model to the subject (Italian, 30s, tech worker).

Given his personality traits, schemas, and observations, identify his stable "if-then" situation-behavior signatures.
These are patterns of the form: "When the subject encounters situation X, he consistently responds with behavior Y."

Personality traits (facet_id: position on spectrum, where 0=low pole, 1=high pole):
{traits}

Active schemas:
{schemas}

Key observations (high-confidence signals):
{observations}

Identify 6-10 stable "if-then" patterns. For each:
- What SITUATION TYPE triggers it (specific, observable situation)
- What BEHAVIORAL response consistently follows
- What EMOTIONAL response accompanies it
- Which personality facets are implicated
- Your confidence based on evidence strength

Return a JSON array:
[
  {{
    "situation_type": "technical_problem_encountered",
    "situation_cues": "A complex technical problem with no obvious solution appears",
    "behavioral_response": "Engages systematically, tries 2-3 approaches before asking for help",
    "emotional_response": "Mild frustration that transforms into flow state",
    "facet_ids": ["cognitive.analytical_approach", "meta_cognition.growth_mindset"],
    "confidence": 0.85
  }}
]

Consider these situation types:
- criticism_received, dependency_requested, authority_encountered
- technical_problem, social_obligation, intimate_vulnerability_requested
- inefficiency_observed, praise_received, deadline_pressure
- relationship_conflict, novelty_encountered, help_sought_from_him
- failure_experienced, public_performance, unstructured_time

Be specific. Base signatures on the trait data provided."""

PREDICTION_PROMPT = """Given this CAPS if-then signature for the subject:

Situation: {situation_type}
Cues: {situation_cues}
Behavioral response: {behavioral_response}
Emotional response: {emotional_response}

Generate 2-3 TESTABLE behavioral predictions — specific observable behaviors that should appear
in the subject's messages/sessions if this signature is active.

Return JSON array:
[
  {{
    "prediction_text": "When discussing technical problems, the subject tries multiple solutions before asking for help",
    "pattern_regex": "provo|sto cercando|ho già provato|non funziona"
  }}
]

Rules:
- Each prediction must be checkable against text content
- pattern_regex: Italian keywords/phrases (pipe-separated) that would confirm the prediction
- Be specific and falsifiable
- 2-3 predictions max
"""


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
    candidates = []
    if end != -1:
        candidates += [s[start:end+1], _fix_json(s[start:end+1])]
    # Partial recovery: collect complete objects from a truncated array
    fragment = s[start:]
    objects: list[Any] = []
    depth = 0
    obj_start = None
    for i, ch in enumerate(fragment):
        if ch == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    objects.append(json.loads(fragment[obj_start:i+1]))
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
        "temperature": 0.15,
        "max_tokens": 4096,
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
        return _parse_json(json.loads(body)["choices"][0]["message"].get("content", ""))
    finally:
        conn.close()


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def run(model: str = DEFAULT_MODEL, dry_run: bool = False, sample: int = SAMPLE_LIMIT) -> None:
    db = get_db()
    try:
        trait_limit = max(sample, 8)
        schema_limit = max(min(sample, 10), 6)
        obs_limit = max(sample * 2, 12)
        traits = db.execute(
            """SELECT t.facet_id, t.value_position, t.confidence, f.spectrum_low, f.spectrum_high
               FROM traits t JOIN facets f ON t.facet_id=f.id
               WHERE t.observation_count >= 2 ORDER BY t.observation_count DESC LIMIT ?""",
            (trait_limit,)
        ).fetchall()
        schemas = db.execute(
            "SELECT schema_name, activation_level, trigger_contexts, behavioral_signatures FROM schemas ORDER BY activation_level DESC LIMIT ?",
            (schema_limit,)
        ).fetchall()
        obs = db.execute(
            """SELECT facet_id, signal_position, content FROM observations
               WHERE signal_strength > 0.6 ORDER BY created_at DESC LIMIT ?""",
            (obs_limit,)
        ).fetchall()

        traits_text = "\n".join(
            f"- {r['facet_id']}: {r['value_position']:.2f} "
            f"({r['spectrum_low']} ← → {r['spectrum_high']})"
            for r in traits
            if r['value_position'] is not None
        )
        schemas_text = "\n".join(
            f"- {r['schema_name']} (activation={r['activation_level']:.2f}): "
            f"triggers={r['trigger_contexts']}, behaviors={r['behavioral_signatures']}"
            for r in schemas
        ) or "None detected yet"
        obs_text = "\n".join(
            f"- {r['facet_id']} pos={r['signal_position']:.2f}: {r['content'][:80]}"
            for r in obs
            if r['signal_position'] is not None
        )

        prompt = CAPS_PROMPT.format(
            traits=traits_text, schemas=schemas_text, observations=obs_text
        )

        if dry_run:
            print(f"Would analyze: {len(traits)} traits, {len(schemas)} schemas, {len(obs)} observations")
            print(f"Model: {model}")
            return

        info(SCRIPT, "run_start", traits=len(traits), schemas=len(schemas))
        results = _call_llm(prompt, model)
        if not isinstance(results, list):
            warn(SCRIPT, "unexpected_format", got=type(results).__name__)
            return

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        for sig in results:
            if float(sig.get("confidence", 0)) < 0.5:
                continue
            db.execute("""
                INSERT INTO caps_signatures
                    (situation_type, situation_cues, behavioral_response, emotional_response,
                     facet_ids, confidence, first_detected_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(situation_type) DO UPDATE SET
                    situation_cues=excluded.situation_cues,
                    behavioral_response=excluded.behavioral_response,
                    emotional_response=excluded.emotional_response,
                    facet_ids=excluded.facet_ids,
                    confidence=excluded.confidence,
                    evidence_count=evidence_count+1,
                    updated_at=excluded.updated_at
            """, (
                sig["situation_type"], sig["situation_cues"],
                sig["behavioral_response"], sig.get("emotional_response"),
                json.dumps(sig.get("facet_ids", [])),
                float(sig["confidence"]), now, now
            ))
            inserted += 1
            info(SCRIPT, "signature_stored",
                 situation=sig["situation_type"],
                 behavior=sig["behavioral_response"][:60],
                 confidence=round(float(sig["confidence"]), 2))

        db.commit()
        info(SCRIPT, "run_complete", signatures=inserted)

        # IMP-07: generate predictions for stored signatures
        if not dry_run and inserted > 0:
            _generate_predictions(db, model)

    finally:
        db.close()


def _generate_predictions(db, model: str) -> None:
    """Generate testable behavioral predictions for each CAPS signature (IMP-07)."""
    sigs = db.execute(
        "SELECT id, situation_type, situation_cues, behavioral_response, emotional_response "
        "FROM caps_signatures"
    ).fetchall()

    total = 0
    for sig in sigs:
        # Skip if predictions already exist for this signature
        existing = db.execute(
            "SELECT COUNT(*) FROM caps_predictions WHERE signature_id=?", (sig["id"],)
        ).fetchone()[0]
        if existing >= 2:
            continue

        prompt = PREDICTION_PROMPT.format(
            situation_type=sig["situation_type"],
            situation_cues=sig["situation_cues"] or "",
            behavioral_response=sig["behavioral_response"],
            emotional_response=sig["emotional_response"] or "",
        )
        try:
            preds = _call_llm(prompt, model)
        except Exception as e:
            warn(SCRIPT, "prediction_llm_error", situation=sig["situation_type"], error=str(e))
            continue

        if not isinstance(preds, list):
            continue

        now = datetime.now(timezone.utc).isoformat()
        for p in preds[:3]:
            text = p.get("prediction_text", "").strip()
            if not text:
                continue
            db.execute(
                "INSERT OR IGNORE INTO caps_predictions "
                "(signature_id, prediction_text, pattern_regex, created_at) VALUES (?,?,?,?)",
                (sig["id"], text, p.get("pattern_regex"), now),
            )
            total += 1

    db.commit()
    info(SCRIPT, "predictions_generated", total=total)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller CAPS Signatures")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_LIMIT)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
