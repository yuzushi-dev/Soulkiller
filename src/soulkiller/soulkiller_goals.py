#!/usr/bin/env python3
"""Soulkiller Goals — Estrae gerarchia goal e conflitti da inbox + decisioni.

Cron: soulkiller:goals, monthly 1° del mese 05:30 Europe/Rome

Usage:
  python3 soulkiller_goals.py [--model ...] [--dry-run] [--sample N]
"""
from __future__ import annotations

import json, http.client, re, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn
from soulkiller_run_guard import should_skip, mark_ran

SCRIPT = "soulkiller_goals"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 600
SUBJECT_FROM_ID = "demo-subject"

GOALS_PROMPT = """You are extracting the subject's active personal goals from his messages and decisions.
the subject is Italian, 30s, tech worker.

Context data:
=== Recent messages (last 150) ===
{messages}

=== Recent decisions ===
{decisions}

=== Known episodes/facts ===
{episodes}

Extract his ACTIVE goals — things he's currently working toward, planning, or investing effort in.

Return JSON:
{{
  "goals": [
    {{
      "goal_text": "Completare OpenClaw come piattaforma autonoma",
      "domain": "tech",
      "horizon": "long",
      "progress": 0.5,
      "priority_rank": 1,
      "supporting_evidence": "Menziona OpenClaw in ogni sessione di lavoro"
    }}
  ],
  "goal_conflicts": [
    {{
      "goal_a": "Completare OpenClaw",
      "goal_b": "Passare più tempo con a close contact",
      "conflict_type": "time_energy",
      "severity": 0.7,
      "description": "Entrambi richiedono tempo serale"
    }}
  ]
}}

Rules:
- domain: tech, lavoro, relazioni, salute, finanza, apprendimento, lifestyle
- horizon: short (<3mo), medium (3-12mo), long (>1yr)
- progress: 0.0-1.0 (stima soggettiva)
- priority_rank: 1=più importante
- goal_conflicts: only when two goals genuinely compete for same resource (time, money, energy, identity)
- conflict severity: 0.0-1.0
- Max 8 goals, max 5 conflicts
- Goals must be SPECIFIC (not "be happy" but "imparare il giapponese")
- Write goals in Italian
"""


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


def run(model: str = DEFAULT_MODEL, dry_run: bool = False, sample: int | None = None) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "goals"):
            return
        # IMP-08: adaptive cadence — boost sample under sustained stress
        stress_triggered = _check_stress_trigger(db)
        if stress_triggered and sample is None:
            sample = 20
            info(SCRIPT, "stress_triggered_cadence", sample=sample)

        message_limit = max(sample or 150, 10)
        decision_limit = min(max(sample or 40, 10), 40)
        episode_limit = min(max(sample or 30, 10), 30)
        msgs = db.execute(
            "SELECT content FROM inbox WHERE from_id=? ORDER BY id DESC LIMIT ?",
            (SUBJECT_FROM_ID, message_limit)
        ).fetchall()
        decisions = db.execute(
            "SELECT decision, domain, decided_at FROM decisions ORDER BY id DESC LIMIT ?",
            (decision_limit,)
        ).fetchall()
        episodes = db.execute(
            "SELECT content, episode_type FROM episodes WHERE active=1 LIMIT ?",
            (episode_limit,)
        ).fetchall()

        msg_text = "\n".join(f"- {r['content'][:100]}" for r in reversed(msgs))
        dec_text  = "\n".join(f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions)
        ep_text   = "\n".join(f"- [{r['episode_type']}] {r['content'][:80]}" for r in episodes)

        prompt = GOALS_PROMPT.format(
            messages=msg_text, decisions=dec_text, episodes=ep_text
        )

        if dry_run:
            print(f"Context: {len(msgs)} msgs, {len(decisions)} decisions, {len(episodes)} episodes")
            print(f"Model: {model}")
            return

        info(SCRIPT, "run_start")
        result = _call_llm(prompt, model)

        now = datetime.now(timezone.utc).isoformat()
        inserted = 0

        for g in result.get("goals", []):
            goal_text = g.get("goal_text", "").strip()
            if not goal_text:
                continue

            conflicts_with = []
            for c in result.get("goal_conflicts", []):
                if c.get("goal_a") == goal_text or c.get("goal_b") == goal_text:
                    other = c["goal_b"] if c["goal_a"] == goal_text else c["goal_a"]
                    conflicts_with.append(other)

            db.execute("""
                INSERT INTO goals
                    (goal_text, domain, priority_rank, horizon, progress,
                     conflicts_with, status, source_ref, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(goal_text, domain) DO UPDATE SET
                    priority_rank=excluded.priority_rank,
                    horizon=excluded.horizon,
                    progress=excluded.progress,
                    conflicts_with=excluded.conflicts_with,
                    updated_at=excluded.updated_at
            """, (
                goal_text, g.get("domain", "altro"),
                g.get("priority_rank"), g.get("horizon", "medium"),
                float(g.get("progress", 0.5)),
                json.dumps(conflicts_with), "active",
                f"goals_extraction:{now[:10]}", now, now
            ))
            inserted += 1
            info(SCRIPT, "goal_stored",
                 domain=g.get("domain"), text=goal_text[:60],
                 horizon=g.get("horizon"), conflicts=len(conflicts_with))

        mark_ran(db, "goals")
        db.commit()
        total = db.execute("SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
        info(SCRIPT, "run_complete", inserted=inserted, total_active=total)

    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Goals Extractor")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, help="Limit recent context for manual smoke tests")
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
