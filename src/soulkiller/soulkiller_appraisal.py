#!/usr/bin/env python3
"""Soulkiller Appraisal Theory Profiler — Lazarus/Scherer Framework.

Analyzes the subject's emotional episodes to build per-domain appraisal profiles:
how he evaluates situations along dimensions of novelty, goal relevance,
coping potential, agency attribution, and norm compatibility.

Populates: appraisal_patterns table + observations for emotional.appraisal_agency
           and emotional.coping_appraisal

Cron: soulkiller:appraisal, monthly 5th 04:30 Europe/Rome

Usage:
  python3 soulkiller_appraisal.py [--model ...] [--dry-run] [--sample N]
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

SCRIPT = "soulkiller_appraisal"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 60

APPRAISAL_PROMPT = """You are a clinical psychologist analyzing the subject's (Italian, 30s, tech worker) emotional appraisal patterns using the Lazarus/Scherer framework.

Analyze only this domain: {domain}.

Identify how the subject TYPICALLY appraises situations along these dimensions (Scherer's Component Process Model):
1. **Novelty sensitivity**: Does he react strongly to unexpected events or take them in stride? (0=unfazed, 1=highly reactive to novelty)
2. **Goal relevance weight**: How much does "is this relevant to MY goals?" drive his emotional response? (0=low, 1=highly goal-focused)
3. **Coping potential default**: His default sense of control — does he feel capable of handling situations? (0=helpless, 1=highly capable)
4. **Agency attribution**: When something emotional happens, who/what does he blame? ("self", "other", "situation", "mixed")
5. **Norm compatibility weight**: How much do social norms/expectations influence his appraisal? (0=ignores norms, 1=highly norm-sensitive)

=== the subject's messages (emotional content) ===
{messages}

=== Emotional episodes ===
{episodes}

=== Decisions (show values/priorities) ===
{decisions}

Return exactly one JSON object:
{{
  "domain": "{domain}",
  "has_evidence": true,
  "novelty_sensitivity": 0.3,
  "goal_relevance_weight": 0.8,
  "coping_potential_default": 0.7,
  "agency_attribution": "self",
  "norm_compatibility_weight": 0.2,
  "typical_appraisals": ["goal-relevant and manageable"],
  "emotional_outcomes": ["frustration then confidence"],
  "evidence": "Short evidence"
}}

Rules:
- All dimensions 0.0-1.0 except agency_attribution (string)
- If there is not enough evidence, return:
  {{"domain":"{domain}","has_evidence":false}}
- Evidence must reference actual message content
- typical_appraisals: 1-2 short items per domain
- emotional_outcomes: 1-2 short items per domain
- Keep evidence under 140 characters
"""


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _fix_json(s: str) -> str:
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    s = re.sub(r'(\])\s*\n(\s*\{)', r'\1,\n\2', s)
    return s


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
    candidates = [s[start:end+1], _fix_json(s[start:end+1])]
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
                candidates.append(_fix_json(s[obj_start:i + 1]))
                break
    for c in candidates:
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
        "temperature": 0.0,
        "max_tokens": 2600,
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
        # Try both fields — nemotron sometimes puts JSON in one and reasoning in other
        for field in ["content", "reasoning"]:
            text = msg.get(field, "")
            if text and "{" in text:
                try:
                    return _parse_json(text)
                except ValueError:
                    continue
        # Last resort: concatenate both
        combined = (msg.get("content") or "") + (msg.get("reasoning") or "")
        return _parse_json(combined)
    finally:
        conn.close()


def store_appraisal(db, d: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    domain = d["domain"]
    db.execute("""
        INSERT INTO appraisal_patterns
            (domain, novelty_sensitivity, goal_relevance_weight,
             coping_potential_default, agency_attribution,
             norm_compatibility_weight, typical_appraisals,
             emotional_outcomes, evidence, sample_size, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(domain) DO UPDATE SET
            novelty_sensitivity=excluded.novelty_sensitivity,
            goal_relevance_weight=excluded.goal_relevance_weight,
            coping_potential_default=excluded.coping_potential_default,
            agency_attribution=excluded.agency_attribution,
            norm_compatibility_weight=excluded.norm_compatibility_weight,
            typical_appraisals=excluded.typical_appraisals,
            emotional_outcomes=excluded.emotional_outcomes,
            evidence=excluded.evidence,
            computed_at=excluded.computed_at
    """, (
        domain,
        float(d.get("novelty_sensitivity", 0.5)),
        float(d.get("goal_relevance_weight", 0.5)),
        float(d.get("coping_potential_default", 0.5)),
        d.get("agency_attribution", "mixed"),
        float(d.get("norm_compatibility_weight", 0.5)),
        json.dumps(d.get("typical_appraisals", [])),
        json.dumps(d.get("emotional_outcomes", [])),
        d.get("evidence", ""),
        d.get("sample_size", 0),
        now
    ))

    # Generate facet observations
    source_ref = f"appraisal:{domain}"

    # emotional.appraisal_agency: self=1.0 (internal), situation=0.0 (external)
    agency = d.get("agency_attribution", "mixed")
    agency_pos = {"self": 0.85, "mixed": 0.5, "other": 0.25, "situation": 0.15}.get(agency, 0.5)
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
        "emotional.appraisal_agency", agency_pos, 0.50,
        f"Appraisal [{domain}]: agency={agency}, evidence: {d.get('evidence', '')[:80]}",
        "appraisal_analysis", source_ref, now
    ))

    # emotional.coping_appraisal
    coping = float(d.get("coping_potential_default", 0.5))
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
        "emotional.coping_appraisal", coping, 0.50,
        f"Appraisal [{domain}]: coping_potential={coping:.2f}",
        "appraisal_analysis", source_ref, now
    ))


def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "appraisal"):
            return
        domains = ["tech", "lavoro", "relazioni", "finanza", "salute"]
        messages = db.execute(
            """SELECT content FROM inbox WHERE from_id=? AND length(content) > 20
               ORDER BY id DESC LIMIT ?""",
            (SUBJECT_FROM_ID, sample)
        ).fetchall()

        episodes = db.execute(
            """SELECT episode_type, content, occurred_at, context FROM episodes
               WHERE active=1 ORDER BY confidence DESC LIMIT ?""",
            (max(min(sample, 12), 6),)
        ).fetchall()

        decisions = db.execute(
            """SELECT decision, domain, context FROM decisions
               ORDER BY id DESC LIMIT ?""",
            (max(min(sample, 10), 6),)
        ).fetchall()

        msg_text = "\n".join(f"- {r['content'][:120]}" for r in reversed(list(messages)))
        ep_text = "\n".join(
            f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes
        ) or "(none)"
        dec_text = "\n".join(
            f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions
        ) or "(none)"

        if dry_run:
            print(f"Messages: {len(messages)}, Episodes: {len(episodes)}, Decisions: {len(decisions)}")
            preview_prompt = APPRAISAL_PROMPT.format(
                domain="tech", messages=msg_text, episodes=ep_text, decisions=dec_text
            )
            print(f"Prompt: {len(preview_prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start", messages=len(messages), episodes=len(episodes))
        stored = 0
        for domain in domains:
            prompt = APPRAISAL_PROMPT.format(
                domain=domain, messages=msg_text, episodes=ep_text, decisions=dec_text
            )
            try:
                result = _call_llm(prompt, model)
            except Exception as e:
                warn(SCRIPT, "llm_error", domain=domain, error=str(e))
                continue
            if not isinstance(result, dict):
                warn(SCRIPT, "unexpected_format", domain=domain, got=type(result).__name__)
                continue
            if not result.get("has_evidence", True):
                info(SCRIPT, "domain_skipped", domain=domain)
                continue
            if "domain" not in result:
                result["domain"] = domain
            store_appraisal(db, result)
            stored += 1
            info(SCRIPT, "domain_stored",
                 domain=result["domain"],
                 agency=result.get("agency_attribution"),
                 coping=round(float(result.get("coping_potential_default", 0.5)), 2))

        mark_ran(db, "appraisal")
        db.commit()
        info(SCRIPT, "run_complete", domains=stored)
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Appraisal Theory Profiler")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
