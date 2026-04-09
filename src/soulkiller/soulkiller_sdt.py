#!/usr/bin/env python3
"""Soulkiller SDT — Self-Determination Theory Need Satisfaction.

Traccia autonomy/competence/relatedness satisfaction per dominio
(lavoro, relazioni, personale) da inbox recente.

Cron: soulkiller:sdt, monthly 1° del mese 06:00 Europe/Rome

Usage:
  python3 soulkiller_sdt.py [--model ...] [--period YYYY-MM] [--dry-run]
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

SCRIPT = "soulkiller_sdt"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 300
SUBJECT_FROM_ID = "demo-subject"

SDT_PROMPT = """You are assessing the subject's Basic Psychological Needs satisfaction for the period {period}.
the subject is Italian, 30s, tech worker building an AI automation platform (OpenClaw).

Based on his recent messages and decisions, assess his NEED SATISFACTION in each domain.
Scale: -1.0 (completely frustrated/thwarted) to +1.0 (fully satisfied).

=== Messages (last 100) ===
{messages}

=== Recent decisions ===
{decisions}

Domains to assess: work, relationships, personal

For each domain assess:
- autonomy_satisfaction: Does he feel volitional choice and self-direction? (-1 to +1)
- competence_satisfaction: Does he feel effective, growing, mastering skills? (-1 to +1)
- relatedness_satisfaction: Does he feel genuine connection and belonging? (-1 to +1)

Return a JSON array with exactly 3 objects (one per domain):
[
  {{
    "domain": "work",
    "autonomy_satisfaction": 0.7,
    "competence_satisfaction": 0.8,
    "relatedness_satisfaction": -0.2,
    "autonomy_evidence": "Building OpenClaw independently, makes all technical decisions",
    "competence_evidence": "Solving complex distributed systems problems",
    "relatedness_evidence": "Mentions working alone, limited collaboration"
  }},
  {{
    "domain": "relationships",
    "autonomy_satisfaction": 0.5,
    "competence_satisfaction": 0.4,
    "relatedness_satisfaction": 0.6,
    "autonomy_evidence": "...",
    "competence_evidence": "...",
    "relatedness_evidence": "..."
  }},
  {{
    "domain": "personal",
    "autonomy_satisfaction": 0.6,
    "competence_satisfaction": 0.5,
    "relatedness_satisfaction": 0.3,
    "autonomy_evidence": "...",
    "competence_evidence": "...",
    "relatedness_evidence": "..."
  }}
]

If insufficient data for a domain, set all scores to 0.0 and set evidence to "insufficient data".
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
        "max_tokens": 2500,
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


def _check_stress_trigger(db) -> bool:
    """Return True if stress has been elevated/high for 2+ consecutive recent weeks (IMP-08)."""
    rows = db.execute(
        "SELECT stress_level FROM stress_snapshots ORDER BY period DESC LIMIT 2"
    ).fetchall()
    if len(rows) < 2:
        return False
    return all(r["stress_level"] in ("elevated", "high") for r in rows)


def run(model: str = DEFAULT_MODEL, period: str | None = None,
        dry_run: bool = False) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "sdt"):
            return
        if period is None:
            now = datetime.now(timezone.utc)
            period = f"{now.year:04d}-{now.month:02d}"

        # IMP-08: adaptive cadence — boost message sample under sustained stress
        msg_limit = 100
        if _check_stress_trigger(db):
            msg_limit = 20
            info(SCRIPT, "stress_triggered_cadence", sample=msg_limit)

        msgs = db.execute(
            "SELECT content FROM inbox WHERE from_id=? ORDER BY id DESC LIMIT ?",
            (SUBJECT_FROM_ID, msg_limit)
        ).fetchall()
        decisions = db.execute(
            "SELECT decision, domain FROM decisions ORDER BY id DESC LIMIT 30"
        ).fetchall()

        msg_text = "\n".join(f"- {r['content'][:100]}" for r in reversed(msgs))
        dec_text  = "\n".join(f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions)

        prompt = SDT_PROMPT.format(period=period, messages=msg_text, decisions=dec_text)

        if dry_run:
            print(f"Period: {period}, {len(msgs)} msgs, {len(decisions)} decisions")
            print(f"Model: {model}")
            return

        info(SCRIPT, "run_start", period=period)
        results = _call_llm(prompt, model)
        if not isinstance(results, list):
            warn(SCRIPT, "unexpected_format", got=type(results).__name__)
            return

        now_str = datetime.now(timezone.utc).isoformat()
        for r in results:
            domain = r.get("domain")
            if not domain:
                continue
            db.execute("""
                INSERT INTO sdt_satisfaction
                    (period, domain, autonomy_satisfaction, competence_satisfaction,
                     relatedness_satisfaction, autonomy_evidence, competence_evidence,
                     relatedness_evidence, computed_at)
                VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(period, domain) DO UPDATE SET
                    autonomy_satisfaction=excluded.autonomy_satisfaction,
                    competence_satisfaction=excluded.competence_satisfaction,
                    relatedness_satisfaction=excluded.relatedness_satisfaction,
                    autonomy_evidence=excluded.autonomy_evidence,
                    competence_evidence=excluded.competence_evidence,
                    relatedness_evidence=excluded.relatedness_evidence,
                    computed_at=excluded.computed_at
            """, (
                period, domain,
                r.get("autonomy_satisfaction"), r.get("competence_satisfaction"),
                r.get("relatedness_satisfaction"),
                r.get("autonomy_evidence"), r.get("competence_evidence"),
                r.get("relatedness_evidence"), now_str
            ))
            info(SCRIPT, "domain_stored",
                 domain=domain,
                 aut=round(float(r.get("autonomy_satisfaction") or 0), 2),
                 comp=round(float(r.get("competence_satisfaction") or 0), 2),
                 rel=round(float(r.get("relatedness_satisfaction") or 0), 2))

        mark_ran(db, "sdt")
        db.commit()
        info(SCRIPT, "run_complete", period=period, domains=len(results))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller SDT Need Satisfaction")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--period", help="YYYY-MM")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(model=args.model, period=args.period, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
