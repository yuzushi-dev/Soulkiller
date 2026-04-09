#!/usr/bin/env python3
"""Soulkiller Mental Models — Johnson-Laird Framework.

Analyzes the subject's reasoning patterns to identify how he constructs
internal representations: spatial vs propositional, exhaustive vs minimal,
counterfactual thinking, analogical reasoning.

Populates: mental_model_patterns table + observations for
           cognitive.mental_model_complexity

Cron: soulkiller:mental-models, monthly 5th 05:00 Europe/Rome

Usage:
  python3 soulkiller_mental_models.py [--model ...] [--dry-run] [--sample N]
"""
from __future__ import annotations
import os

import json, http.client, re, urllib.parse, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure we can import from scripts/lib
sys.path.insert(0, str(Path(__file__).parent))
from lib.config import load_nanobot_config
from lib.log import info, warn
from soulkiller_run_guard import should_skip, mark_ran

SCRIPT = "soulkiller_mental_models"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
LLM_TIMEOUT = 300
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 80

MENTAL_MODEL_PROMPT = """You are a cognitive psychologist analyzing the subject's (Italian, 30s, tech worker) reasoning patterns using Johnson-Laird's Mental Models framework.

Identify how the subject constructs internal representations when reasoning about different domains:

1. **Representation style**: How does he internally represent problems?
   - "spatial": uses spatial metaphors, diagrams, physical analogies
   - "propositional": uses logical rules, if-then chains, abstract principles
   - "narrative": uses stories, scenarios, timelines to reason
   - "mixed": varies by context

2. **Model complexity**: How many alternative scenarios does he consider?
   - "minimal": first-fit reasoning — takes the first model that works
   - "moderate": considers 2-3 alternatives before deciding
   - "exhaustive": systematically considers many possibilities

3. **Counterfactual frequency**: How often does he think "what if...?" (0=never, 1=constantly)

4. **Analogy preference**: How often does he reason by analogy? (0=never, 1=constantly)
   e.g., "it's like when...", "think of it as...", comparing to known patterns

=== the subject's messages (reasoning/problem-solving content) ===
{messages}

=== Episodes (reasoning-relevant) ===
{episodes}

=== CAPS behavioral signatures ===
{caps}

Return JSON:
{{
  "domains": [
    {{
      "domain": "tech",
      "representation_style": "propositional",
      "model_complexity": "moderate",
      "counterfactual_frequency": 0.4,
      "analogy_preference": 0.6,
      "default_assumptions": ["Systems can be understood by decomposing into components"],
      "evidence": "Specific quotes showing reasoning style"
    }}
  ]
}}

Domains: tech, lavoro, relazioni, finanza, salute, lifestyle
Rules:
- Only include domains with clear reasoning evidence (not just statements)
- Look for HOW he arrives at conclusions, not just the conclusions themselves
- Counterfactual markers: "se avessi...", "e se...", "immagina se...", "sarebbe stato..."
- Analogy markers: "e' come...", "tipo quando...", "pensa a...", "come se fosse..."
- Propositional markers: "quindi...", "se X allora Y", "perche'..."
- Spatial markers: physical metaphors, "sopra/sotto", "vicino/lontano", structural language
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
    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON: {content[:80]}")
    end = s.rfind("}")
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
            if text and "{" in text:
                try:
                    return _parse_json(text)
                except ValueError:
                    continue
        combined = (msg.get("content") or "") + (msg.get("reasoning") or "")
        return _parse_json(combined)
    finally:
        conn.close()


def store_mental_model(db, d: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    domain = d["domain"]
    # Ensure default_assumptions is a list of strings
    default_assumptions = d.get("default_assumptions", [])
    if not isinstance(default_assumptions, list):
        default_assumptions = [str(default_assumptions)]
    else:
        default_assumptions = [str(item) for item in default_assumptions]
    # Ensure evidence is a string
    evidence = d.get("evidence", "")
    if isinstance(evidence, list):
        evidence = " ".join(str(item) for item in evidence)
    db.execute("""
        INSERT INTO mental_model_patterns
            (domain, representation_style, model_complexity,
             counterfactual_frequency, analogy_preference,
             default_assumptions, evidence, sample_size, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(domain) DO UPDATE SET
            representation_style=excluded.representation_style,
            model_complexity=excluded.model_complexity,
            counterfactual_frequency=excluded.counterfactual_frequency,
            analogy_preference=excluded.analogy_preference,
            default_assumptions=excluded.default_assumptions,
            evidence=excluded.evidence,
            computed_at=excluded.computed_at
    """, (
        domain,
        d.get("representation_style", "mixed"),
        d.get("model_complexity", "moderate"),
        float(d.get("counterfactual_frequency", 0.5)),
        float(d.get("analogy_preference", 0.5)),
        json.dumps(default_assumptions),
        evidence,
        d.get("sample_size", 0),
        now
    ))

    # Observation: cognitive.mental_model_complexity
    source_ref = f"mental_model:{domain}"
    complexity_map = {"minimal": 0.2, "moderate": 0.5, "exhaustive": 0.85}
    complexity = complexity_map.get(d.get("model_complexity", "moderate"), 0.5)
    cf = float(d.get("counterfactual_frequency", 0.5))
    signal_pos = complexity * 0.7 + cf * 0.3

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
        "cognitive.mental_model_complexity", round(signal_pos, 3), 0.50,
        f"Mental model [{domain}]: style={d.get('representation_style')}, "
        f"complexity={d.get('model_complexity')}, cf={cf:.2f}",
        "mental_model_analysis", source_ref, now
    ))


def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "mental_models"):
            return
        messages = db.execute(
            """SELECT content FROM inbox WHERE from_id=? AND length(content) > 20
               ORDER BY id DESC LIMIT ?""",
            (SUBJECT_FROM_ID, sample)
        ).fetchall()

        episodes = db.execute(
            """SELECT episode_type, content, context FROM episodes
               WHERE active=1 ORDER BY confidence DESC LIMIT 25"""
        ).fetchall()

        caps = db.execute(
            """SELECT situation_type, behavioral_response, emotional_response
               FROM caps_signatures ORDER BY confidence DESC LIMIT 8"""
        ).fetchall()

        msg_text = "\n".join(f"- {r['content'][:120]}" for r in reversed(list(messages)))
        ep_text = "\n".join(
            f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes
        ) or "(none)"
        caps_text = "\n".join(
            f"- IF {r['situation_type']} -> {r['behavioral_response'][:70]}" for r in caps
        ) or "(none)"

        prompt = MENTAL_MODEL_PROMPT.format(
            messages=msg_text, episodes=ep_text, caps=caps_text
        )

        if dry_run:
            print(f"Messages: {len(messages)}, Episodes: {len(episodes)}, CAPS: {len(caps)}")
            print(f"Prompt: {len(prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start", messages=len(messages), episodes=len(episodes))

        try:
            result = _call_llm(prompt, model)
        except Exception as e:
            warn(SCRIPT, "llm_error", error=str(e))
            return

        domains = result.get("domains", [])
        for d in domains:
            if "domain" not in d:
                continue
            store_mental_model(db, d)
            info(SCRIPT, "domain_stored",
                 domain=d["domain"],
                 style=d.get("representation_style"),
                 complexity=d.get("model_complexity"))

        mark_ran(db, "mental_models")
        db.commit()
        info(SCRIPT, "run_complete", domains=len(domains))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Mental Models Analyzer")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
