#!/usr/bin/env python3
"""Soulkiller Dual Process Profiler — Kahneman S1/S2 Framework.

Hybrid analysis: programmatic detection of dual-process markers
(self-corrections, deliberation markers, snap judgments) +
LLM analysis of per-domain System 1/System 2 balance.

Populates: dual_process_profile table + observations for
           cognitive.system1_dominance

Cron: soulkiller:dual-process, monthly 5th 05:30 Europe/Rome

Usage:
  python3 soulkiller_dual_process.py [--model ...] [--dry-run] [--sample N]
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

SCRIPT = "soulkiller_dual_process"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT = 300
SUBJECT_FROM_ID = "demo-subject"
SAMPLE_MESSAGES = 100

# Programmatic markers
SELF_CORRECTION_RE = re.compile(
    r'\b(anzi|no aspetta|wait|no scusa|volevo dire|cioè no|pardon|intendevo|'
    r'mi correggo|scusa ho sbagliato|actually|no wait|ah no)\b', re.I
)
DELIBERATION_RE = re.compile(
    r'\b(fammi pensare|devo pensare|ci penso|let me think|vediamo|'
    r'hmm|mmm|uhm|allora vediamo|ragionandoci|pensandoci bene|'
    r'a pensarci|se ci penso|devo riflettere|riflettiamoci)\b', re.I
)
SNAP_JUDGMENT_RE = re.compile(
    r'\b(ovvio|è chiaro|basta|sicuramente|senza dubbio|decisamente|'
    r'subito|fatto|ok va bene|si facciamo|via|andiamo)\b', re.I
)

DUAL_PROCESS_PROMPT = """You are a cognitive psychologist analyzing the subject's (Italian, 30s, tech worker) System 1/System 2 processing balance (Kahneman's dual-process theory).

System 1: Fast, automatic, intuitive, effortless
System 2: Slow, deliberate, analytical, effortful

Programmatic markers detected in his messages:
{markers}

For each domain, assess:
1. **system1_dominance**: 0 = fully deliberate/analytical, 1 = fully intuitive/automatic
2. **switching_triggers**: What makes him shift from S1 to S2? (list of triggers)

=== the subject's messages ===
{messages}

=== Episodes ===
{episodes}

=== Decisions ===
{decisions}

Return JSON:
{{
  "domains": [
    {{
      "domain": "tech",
      "system1_dominance": 0.3,
      "switching_triggers": ["encountering unfamiliar technology", "debugging complex issues"],
      "evidence": "Quote or paraphrase showing S1/S2 patterns"
    }}
  ]
}}

Domains: tech, lavoro, relazioni, finanza, salute, lifestyle
Look for:
- Quick, confident responses = S1 dominant
- "Let me think...", self-corrections, careful hedging = S2 engagement
- Domain-specific differences (may be S1 in tech but S2 in finance)
- What TRIGGERS the switch from S1 to S2 (anxiety? stakes? novelty?)
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


def compute_programmatic_markers(messages: list[str]) -> dict:
    total_words = sum(len(m.split()) for m in messages)
    if total_words == 0:
        return {"self_correction_rate": 0, "deliberation_rate": 0, "snap_judgment_rate": 0}

    sc_count = sum(len(SELF_CORRECTION_RE.findall(m)) for m in messages)
    dl_count = sum(len(DELIBERATION_RE.findall(m)) for m in messages)
    sj_count = sum(len(SNAP_JUDGMENT_RE.findall(m)) for m in messages)

    return {
        "self_correction_rate": round(sc_count / total_words * 1000, 3),
        "deliberation_rate": round(dl_count / total_words * 1000, 3),
        "snap_judgment_rate": round(sj_count / total_words * 1000, 3),
    }


def store_dual_process(db, d: dict, markers: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    domain = d["domain"]
    db.execute("""
        INSERT INTO dual_process_profile
            (domain, system1_dominance, switching_triggers,
             self_correction_rate, deliberation_marker_rate,
             snap_judgment_rate, evidence, sample_size, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        ON CONFLICT(domain) DO UPDATE SET
            system1_dominance=excluded.system1_dominance,
            switching_triggers=excluded.switching_triggers,
            self_correction_rate=excluded.self_correction_rate,
            deliberation_marker_rate=excluded.deliberation_marker_rate,
            snap_judgment_rate=excluded.snap_judgment_rate,
            evidence=excluded.evidence,
            computed_at=excluded.computed_at
    """, (
        domain,
        float(d.get("system1_dominance", 0.5)),
        json.dumps(d.get("switching_triggers", [])),
        markers["self_correction_rate"],
        markers["deliberation_rate"],
        markers["snap_judgment_rate"],
        d.get("evidence", ""),
        d.get("sample_size", 0),
        now
    ))

    # Observation: cognitive.system1_dominance
    source_ref = f"dual_process:{domain}"
    s1_dom = float(d.get("system1_dominance", 0.5))

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
        "cognitive.system1_dominance", s1_dom, 0.50,
        f"Dual process [{domain}]: S1_dom={s1_dom:.2f}, "
        f"triggers: {', '.join(d.get('switching_triggers', [])[:3])}",
        "dual_process_analysis", source_ref, now
    ))


def run(model: str = DEFAULT_MODEL, dry_run: bool = False,
        sample: int = SAMPLE_MESSAGES) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "dual_process"):
            return
        messages_rows = db.execute(
            """SELECT content FROM inbox WHERE from_id=? AND length(content) > 10
               ORDER BY id DESC LIMIT ?""",
            (SUBJECT_FROM_ID, sample)
        ).fetchall()
        messages = [r["content"] for r in reversed(list(messages_rows))]

        episodes = db.execute(
            """SELECT episode_type, content FROM episodes
               WHERE active=1 ORDER BY confidence DESC LIMIT 20"""
        ).fetchall()

        decisions = db.execute(
            """SELECT decision, domain, context FROM decisions
               ORDER BY id DESC LIMIT 15"""
        ).fetchall()

        # Programmatic analysis
        markers = compute_programmatic_markers(messages)

        msg_text = "\n".join(f"- {m[:120]}" for m in messages)
        ep_text = "\n".join(
            f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes
        ) or "(none)"
        dec_text = "\n".join(
            f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions
        ) or "(none)"
        markers_text = (
            f"Self-corrections: {markers['self_correction_rate']:.1f}/1000 words\n"
            f"Deliberation markers: {markers['deliberation_rate']:.1f}/1000 words\n"
            f"Snap judgments: {markers['snap_judgment_rate']:.1f}/1000 words"
        )

        prompt = DUAL_PROCESS_PROMPT.format(
            markers=markers_text, messages=msg_text,
            episodes=ep_text, decisions=dec_text
        )

        if dry_run:
            print(f"Messages: {len(messages)}")
            print(f"Markers: {markers}")
            print(f"Prompt: {len(prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start", messages=len(messages), markers=markers)

        try:
            result = _call_llm(prompt, model)
        except Exception as e:
            warn(SCRIPT, "llm_error", error=str(e))
            return

        domains = result.get("domains", [])
        for d in domains:
            if "domain" not in d:
                continue
            store_dual_process(db, d, markers)
            info(SCRIPT, "domain_stored",
                 domain=d["domain"],
                 s1_dominance=round(float(d.get("system1_dominance", 0.5)), 2))

        mark_ran(db, "dual_process")
        db.commit()
        info(SCRIPT, "run_complete", domains=len(domains))
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Dual Process Profiler")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--sample", type=int, default=SAMPLE_MESSAGES)
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run, sample=args.sample)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
