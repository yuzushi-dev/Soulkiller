#!/usr/bin/env python3
"""Soulkiller Implicit Motives - PSE-inspired n-Ach/Aff/Pow scoring (McClelland).

Scores implicit motives from spontaneous narrative content in episodes and decisions.
Uses computerized content analysis inspired by PSE (Picture Story Exercise) coding (Smith et al., 1992).

Cron: soulkiller:motives, monthly 10th 06:00 Europe/Rome

Usage:
  python3 soulkiller_motives.py [--model ...] [--dry-run]
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
from soulkiller_run_guard import should_skip, mark_ran

SCRIPT = "soulkiller_motives"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
MIN_SAMPLE = 10


MOTIVES_PROMPT = """You are applying McClelland's implicit motive scoring (PSE method, Smith et al. 1992)
to the subject's spontaneous narrative content. Implicit motives are inferred from unprompted stories,
NOT from stated preferences.

Analyze ONLY spontaneous narrative passages below (events, opinions, habits - not responses to direct questions).

Score three implicit motives (0.0–1.0 each):

**n-Achievement (n-Ach)**: Concern with doing things better, meeting standards of excellence,
unique accomplishment. Look for: competition with self, setting challenging goals, mastery language,
frustration with mediocrity, pride in results.

**n-Affiliation (n-Aff)**: Concern with establishing and maintaining positive relationships,
being liked, belonging. Look for: relationship maintenance language, concern about others' feelings,
preference for friendly interactions, distress at rejection.

**n-Power (n-Pow)**: Concern with having impact on others, feeling strong, prestige, reputation.
Look for: influencing others, building systems that affect many people, concern about status,
decisive/directive language.

Spontaneous narrative content ({n} passages):
{passages}

Return JSON:
{{
  "n_ach": 0.65,
  "n_aff": 0.40,
  "n_pow": 0.55,
  "n_ach_evidence": "2-3 specific quotes supporting the achievement score",
  "n_aff_evidence": "2-3 specific quotes supporting the affiliation score",
  "n_pow_evidence": "2-3 specific quotes supporting the power score",
  "sample_size": {n},
  "scoring_notes": "Any caveats about the scoring (e.g., limited data, cultural context)"
}}

Rules:
- Score based ONLY on what is present in the text, not cultural stereotypes
- If evidence is insufficient for a motive, score 0.0 and explain in notes
- Scores are relative within this subject's narrative profile, not population norms
- Mean score should be ~0.5 if all three motives have roughly equal evidence
"""


def _call_llm(prompt: str, model: str) -> dict[str, Any]:
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
        "max_tokens": 1500,
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
        s = content.strip()
        if s.startswith("```"):
            s = "\n".join(s.split("\n")[1:])
            if s.rstrip().endswith("```"):
                s = s.rstrip()[:-3].strip()
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1:
            raise ValueError(f"No JSON in response: {content[:100]}")
        return json.loads(s[start:end + 1])
    finally:
        conn.close()


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_narrative_passages(db) -> list[str]:
    """Load spontaneous narrative content: events, habits, opinions from episodes + decisions."""
    passages = []

    # Episodes: unprompted facts, events, habits
    rows = db.execute(
        """SELECT content FROM episodes
           WHERE episode_type IN ('event', 'habit', 'opinion', 'preference')
             AND active=1
             AND source_type != 'checkin_reply'
           ORDER BY id DESC LIMIT 60"""
    ).fetchall()
    passages.extend(r["content"] for r in rows)

    # Decisions: free-form, non-checkin
    rows = db.execute(
        """SELECT decision FROM decisions
           WHERE source_type != 'checkin_reply'
           ORDER BY id DESC LIMIT 40"""
    ).fetchall()
    passages.extend(r["decision"] for r in rows)

    return passages


def run(model: str = DEFAULT_MODEL, dry_run: bool = False) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "motives"):
            return
        passages = load_narrative_passages(db)

        if len(passages) < MIN_SAMPLE:
            warn(SCRIPT, "insufficient_passages", count=len(passages), min=MIN_SAMPLE)
            return

        info(SCRIPT, "run_start", passages=len(passages))

        passage_text = "\n".join(f"- {p[:200]}" for p in passages)
        prompt = MOTIVES_PROMPT.format(n=len(passages), passages=passage_text)

        if dry_run:
            print(f"Would score {len(passages)} passages with {model}")
            print(f"Prompt length: {len(prompt)} chars")
            return

        result = _call_llm(prompt, model)

        n_ach = float(result.get("n_ach", 0.0))
        n_aff = float(result.get("n_aff", 0.0))
        n_pow = float(result.get("n_pow", 0.0))
        sample_size = int(result.get("sample_size", len(passages)))
        evidence = json.dumps({
            "n_ach": result.get("n_ach_evidence", ""),
            "n_aff": result.get("n_aff_evidence", ""),
            "n_pow": result.get("n_pow_evidence", ""),
            "notes": result.get("scoring_notes", ""),
        }, ensure_ascii=False)

        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO implicit_motives (n_ach, n_aff, n_pow, sample_size, evidence, computed_at) "
            "VALUES (?,?,?,?,?,?)",
            (n_ach, n_aff, n_pow, sample_size, evidence, now),
        )
        mark_ran(db, "motives")
        db.commit()

        info(SCRIPT, "motives_scored",
             n_ach=round(n_ach, 3), n_aff=round(n_aff, 3), n_pow=round(n_pow, 3),
             sample_size=sample_size)

    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Implicit Motives (McClelland PSE)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
