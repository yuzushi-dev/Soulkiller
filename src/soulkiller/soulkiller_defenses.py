#!/usr/bin/env python3
"""Soulkiller Defense Mechanism Detector - Vaillant Hierarchy.

Analizza il corpus di messaggi/episodi per rilevare i meccanismi di difesa
secondo la gerarchia di Vaillant (mature, neurotic, immature).

Memorizza nella tabella `schemas` con schema_domain='defense_mechanism'.

Cron: soulkiller:defenses, monthly 3° del mese 05:30 Europe/Rome

Usage:
  python3 soulkiller_defenses.py [--model ...] [--dry-run] [--sample N]
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

SCRIPT = "soulkiller_defenses"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
CONSENSUS_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 60

DEFENSE_PROMPT = """You are a clinical psychologist applying Vaillant's defense mechanism hierarchy.
Analyze the subject's messages (Italian, 30s, tech worker) to identify which defenses are active.

Vaillant's hierarchy - look for these patterns:

MATURE (adaptive, healthy):
- sublimation: channels distress into productive/creative work
- humor: uses wit to deflect or process difficult emotions
- anticipation: realistic planning to reduce future anxiety
- altruism: helps others as a way to manage own distress
- suppression: consciously sets aside distress to focus on task

NEUROTIC (intermediate, somewhat distorting):
- rationalization: constructs logical explanations to justify actions/feelings
- intellectualization: detaches emotionally by focusing on abstract/technical analysis
- compartmentalization: separates conflicting beliefs/feelings without integrating them
- reaction_formation: expresses opposite of actual feeling (e.g., anger masked as excessive politeness)
- displacement: redirects emotion from original target to safer one
- isolation: separates memory from its emotional content

IMMATURE (maladaptive, more distorting):
- projection: attributes own unwanted feelings/motives to others
- denial: refuses to acknowledge painful reality
- acting_out: expresses internal conflict through impulsive behavior
- passive_aggression: indirect expression of hostility/resentment
- splitting: sees people/situations as all-good or all-bad

Messages (last {n} from the subject):
{messages}

Recent episodes context:
{episodes}

Return a JSON array - only defenses with clear behavioral evidence:
[
  {{
    "defense_name": "intellectualization",
    "maturity_level": "neurotic",
    "activation_level": 0.75,
    "confidence": 0.70,
    "trigger_contexts": ["emotional situations", "interpersonal conflict"],
    "behavioral_signatures": ["analyzes feelings technically", "avoids 'I feel' statements"],
    "evidence": "Quote or paraphrase: 2-3 specific examples from the messages",
    "adaptive_function": "protects from emotional overwhelm by maintaining cognitive control"
  }}
]

Rules:
- activation_level: 0.0-1.0 (how frequently/intensely this defense appears)
- confidence: 0.0-1.0 (strength of evidence)
- Only include defenses with confidence >= 0.55
- Maximum 5 defenses (the most prominent ones)
- Evidence must quote or closely paraphrase actual message content
- maturity_level must be: mature, neurotic, or immature
"""


# ---------------------------------------------------------------------------
# Facet mapping: which facets do defenses signal
# ---------------------------------------------------------------------------

DEFENSE_FACET_MAP = {
    # defense_name: (facet_id, signal_position)
    # Mature defenses → high distress tolerance, good regulation
    "sublimation":         ("emotional.distress_tolerance",   0.8),
    "humor":               ("emotional.emotional_expression", 0.6),
    "anticipation":        ("temporal.planning_horizon",      0.8),
    "suppression":         ("emotional.distress_tolerance",   0.7),
    "altruism":            ("relational.vulnerability_capacity", 0.6),
    # Neurotic → intellectualization signals analytical approach + low emotional expression
    "rationalization":     ("cognitive.analytical_approach",  0.85),
    "intellectualization": ("emotional.emotion_clarity",      0.2),
    "compartmentalization": ("emotional.emotion_clarity",     0.15),
    "reaction_formation":  ("emotional.emotional_expression", 0.1),
    "displacement":        ("emotional.distress_tolerance",   0.35),
    "isolation":           ("emotional.emotional_expression", 0.1),
    # Immature → low trust, anxiety, dysregulation
    "projection":          ("relational.trust_formation",     0.1),
    "denial":              ("meta_cognition.self_awareness",  0.2),
    "acting_out":          ("emotional.distress_tolerance",   0.15),
    "passive_aggression":  ("emotional.emotional_expression", 0.1),
    "splitting":           ("relational.attachment_anxiety",  0.85),
}


# ---------------------------------------------------------------------------
# DB / LLM helpers
# ---------------------------------------------------------------------------

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
        lines = s.split("\n")
        s = "\n".join(lines[1:])
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
        "max_tokens": 3000,
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


def load_messages(db, n: int) -> list[str]:
    rows = db.execute(
        """SELECT content FROM inbox WHERE from_id=? AND length(content) > 10
           ORDER BY id DESC LIMIT ?""",
        (SUBJECT_FROM_ID, n)
    ).fetchall()
    return [r["content"] for r in reversed(rows)]


def load_episodes(db, n: int = 20) -> list[str]:
    rows = db.execute(
        """SELECT content, episode_type, occurred_at FROM episodes
           WHERE episode_type IN ('fact', 'event', 'habit', 'preference', 'opinion')
             AND active=1
           ORDER BY id DESC LIMIT ?""",
        (n,)
    ).fetchall()
    result = []
    for r in rows:
        date = (r["occurred_at"] or "")[:10]
        result.append(f"[{r['episode_type']} {date}] {r['content'][:120]}")
    return list(reversed(result))


def store_defense(db, d: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    consensus = 1 if d.get("_consensus", True) else 0
    # Store in schemas table with schema_domain='defense_mechanism'
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
        d["defense_name"], "defense_mechanism",
        float(d["activation_level"]), float(d["confidence"]),
        json.dumps(d.get("trigger_contexts", [])),
        json.dumps(d.get("behavioral_signatures", [])),
        d.get("evidence", ""),
        now, now, consensus
    ))

    # Generate observation for mapped facet
    defense_name = d["defense_name"]
    if defense_name in DEFENSE_FACET_MAP:
        facet_id, signal_pos = DEFENSE_FACET_MAP[defense_name]
        maturity = d.get("maturity_level", "neurotic")
        # Scale signal strength: mature defenses get higher weight
        maturity_weight = {"mature": 0.8, "neurotic": 0.6, "immature": 0.4}.get(maturity, 0.5)
        signal_strength = float(d["confidence"]) * maturity_weight

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
            facet_id, signal_pos, signal_strength,
            f"Defense {defense_name} ({maturity}, activation={float(d['activation_level']):.2f}): {d.get('evidence', '')[:100]}",
            "defense_detection", f"defense:{defense_name}",
            now
        ))


# ---------------------------------------------------------------------------
# Consensus merge (IMP-10)
# ---------------------------------------------------------------------------

def _consensus_merge(primary: list, secondary: list, threshold: float = 0.55) -> list:
    """Keep defenses detected by both models; lower confidence for single-model detections."""
    primary_map = {d["defense_name"]: d for d in primary if float(d.get("confidence", 0)) >= threshold}
    secondary_map = {d["defense_name"]: d for d in secondary if float(d.get("confidence", 0)) >= threshold}

    accepted = []
    for name, d in primary_map.items():
        d2 = secondary_map.get(name)
        d = dict(d)
        if d2:
            d["confidence"] = (float(d["confidence"]) + float(d2["confidence"])) / 2
            d["_consensus"] = True
        else:
            d["confidence"] = float(d["confidence"]) * 0.7
            d["_consensus"] = False
        accepted.append(d)

    for name, d2 in secondary_map.items():
        if name not in primary_map:
            d2 = dict(d2)
            d2["confidence"] = float(d2["confidence"]) * 0.7
            d2["_consensus"] = False
            accepted.append(d2)

    return accepted


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "defenses"):
            return
        messages = load_messages(db, sample)
        episodes = load_episodes(db, 20)

        if len(messages) < 10 and len(episodes) < 5:
            warn(SCRIPT, "insufficient_data", messages=len(messages), episodes=len(episodes))
            return

        info(SCRIPT, "run_start", messages=len(messages), episodes=len(episodes))

        msg_text = "\n".join(f"- {m[:120]}" for m in messages)
        ep_text = "\n".join(f"- {e}" for e in episodes) if episodes else "(none available)"
        prompt = DEFENSE_PROMPT.format(
            n=len(messages),
            messages=msg_text,
            episodes=ep_text
        )

        if dry_run:
            print(f"Would analyze {len(messages)} messages + {len(episodes)} episodes")
            print(f"Prompt length: {len(prompt)} chars")
            return

        try:
            results = _call_llm(prompt, model)
        except Exception as e:
            warn(SCRIPT, "llm_error", error=str(e))
            return

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

        stored = 0
        for d in results:
            try:
                if float(d.get("confidence", 0)) < 0.55:
                    continue
                store_defense(db, d)
                stored += 1
                info(SCRIPT, "defense_detected",
                     name=d["defense_name"],
                     maturity=d.get("maturity_level"),
                     activation=round(float(d["activation_level"]), 2),
                     confidence=round(float(d["confidence"]), 2),
                     consensus=d.get("_consensus", True))
            except Exception as e:
                warn(SCRIPT, "store_error", defense=d.get("defense_name"), error=str(e))

        mark_ran(db, "defenses")
        db.commit()
        info(SCRIPT, "run_complete", defenses_stored=stored)
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Defense Mechanism Detector")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
