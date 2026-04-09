#!/usr/bin/env python3
"""Soulkiller Narrative Identity — McAdams narrative structure analysis.

Analyzes the subject's life episodes, decisions and goals to assess:
- Narrative agency (protagonist vs. reactive framing)
- Redemptive meaning (finds growth in adversity)
- Narrative coherence (past-present-future as connected story)
- Nuclear episodes: peak experiences, nadir experiences, turning points

Generates observation for meta_cognition.narrative_agency.
Stores narrative nodes as episodes of type 'narrative_node'.

Cron: soulkiller:narrative, monthly 3° del mese 06:00 Europe/Rome

Usage:
  python3 soulkiller_narrative.py [--model ...] [--dry-run]
"""
from __future__ import annotations

import json, http.client, re, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import load_nanobot_config
from lib.log import info, warn
from soulkiller_run_guard import should_skip, mark_ran

SCRIPT = "soulkiller_narrative"
DB_PATH = Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db"
DEFAULT_MODEL = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
LLM_TIMEOUT = 300

NARRATIVE_PROMPT = """You are a narrative psychologist applying Dan McAdams' Life Story Model to the subject (Italian, 30s, tech worker).

Analyze his episodes, decisions, and current projects to assess his NARRATIVE IDENTITY — how he constructs a coherent life story.

=== Episodes (life events and facts) ===
{episodes}

=== Decisions (explicit choices) ===
{decisions}

=== Active goals ===
{goals}

=== Key personality traits ===
{traits}

Assess:

1. **narrative_agency** (0.0-1.0): Does he position himself as an active protagonist shaping his story (1.0) or as someone reacting to circumstances (0.0)?

2. **redemptive_meaning** (0.0-1.0): Does he find growth, learning, or positive transformation in difficult experiences (1.0) or see them as purely negative (0.0)?

3. **narrative_coherence** (0.0-1.0): Does his past connect meaningfully to present and future goals (1.0) or does life feel like disconnected events (0.0)?

4. **nuclear_episodes**: Identify peak experiences (highly positive, identity-defining), nadir experiences (most challenging), and turning points (moments of change) from the data.

5. **dominant_theme**: The main narrative theme of his life story (e.g., "achievement through autonomy", "building systems to control chaos", "reluctant connection")

Return JSON:
{{
  "narrative_agency": 0.75,
  "redemptive_meaning": 0.65,
  "narrative_coherence": 0.70,
  "confidence": 0.65,
  "dominant_theme": "Costruisce sistemi autonomi per ridurre dipendenza e caos",
  "agency_evidence": "Actively builds OpenClaw, starts therapy, adopts Fluoxetine — all proactive choices",
  "redemptive_evidence": "Describes Fluoxetine+therapy as 'lighter and less irritable' — positive reframing of mental health work",
  "nuclear_episodes": [
    {{"type": "turning_point", "content": "Inizio terapia psicologica e Fluoxetina — scelta proattiva di cambiamento"}},
    {{"type": "peak", "content": "a close contact che nota cambiamenti positivi in the subject"}},
    {{"type": "nadir", "content": "Periodo di irritabilità e chiusura prima della terapia"}}
  ]
}}

Rules:
- Base ONLY on evidence in the data
- confidence: your certainty about the assessment (0.0-1.0)
- nuclear_episodes: max 5 total, only if clearly supported by data
- dominant_theme: one concise sentence in Italian
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
        raise ValueError(f"No JSON object: {content[:80]}")
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
        "max_tokens": 2000,
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
        response_data = json.loads(body)
        if not response_data.get("choices"):
            raise RuntimeError(f"No choices in response: {body[:200]}")
        message = response_data["choices"][0].get("message")
        if not message:
            raise RuntimeError(f"No message in choice: {body[:200]}")
        content = message.get("content")
        if content is None:
            # Some providers might put the content in a different field
            content = message.get("reasoning") or message.get("refusal") or ""
            if not content:
                raise RuntimeError(f"No content in message: {body[:200]}")
        return _parse_json(content)
    finally:
        conn.close()


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def run(model: str = DEFAULT_MODEL, dry_run: bool = False) -> None:
    db = get_db()
    try:
        if not dry_run and should_skip(db, "narrative"):
            return
        episodes = db.execute(
            "SELECT episode_type, content, occurred_at, confidence FROM episodes WHERE active=1 ORDER BY confidence DESC LIMIT 30"
        ).fetchall()
        decisions = db.execute(
            "SELECT decision, domain, direction, context FROM decisions ORDER BY id DESC LIMIT 30"
        ).fetchall()
        goals = db.execute(
            "SELECT goal_text, domain, horizon, priority_rank FROM goals WHERE status='active' ORDER BY priority_rank LIMIT 8"
        ).fetchall()
        traits = db.execute(
            """SELECT t.facet_id, t.value_position, f.spectrum_low, f.spectrum_high
               FROM traits t JOIN facets f ON t.facet_id=f.id
               WHERE t.confidence > 0.5 AND t.facet_id IN (
                 'meta_cognition.growth_mindset','meta_cognition.self_awareness',
                 'meta_cognition.change_readiness','temporal.planning_horizon',
                 'values.autonomy_importance','emotional.resilience_pattern'
               )"""
        ).fetchall()

        ep_text  = "\n".join(f"- [{r['episode_type']}] {r['content'][:100]}" for r in episodes) or "(none)"
        dec_text = "\n".join(f"- [{r['domain']}] {r['decision'][:80]}" for r in decisions) or "(none)"
        goal_text = "\n".join(f"- [{r['domain']}|{r['horizon']}] {r['goal_text'][:80]}" for r in goals) or "(none)"
        trait_text = "\n".join(
            f"- {r['facet_id']}: {r['value_position']:.2f} ({r['spectrum_low']} ← → {r['spectrum_high']})"
            for r in traits
        ) or "(none)"

        prompt = NARRATIVE_PROMPT.format(
            episodes=ep_text, decisions=dec_text, goals=goal_text, traits=trait_text
        )

        if dry_run:
            print(f"Episodes: {len(episodes)}, Decisions: {len(decisions)}, Goals: {len(goals)}")
            print(f"Prompt: {len(prompt)} chars | Model: {model}")
            return

        info(SCRIPT, "run_start")
        result = _call_llm(prompt, model)

        agency = float(result.get("narrative_agency", 0.5))
        redemptive = float(result.get("redemptive_meaning", 0.5))
        coherence = float(result.get("narrative_coherence", 0.5))
        confidence = float(result.get("confidence", 0.5))
        theme = result.get("dominant_theme", "")

        now = datetime.now(timezone.utc).isoformat()

        # Store narrative_agency observation
        db.execute("""
            INSERT INTO observations
                (facet_id, signal_position, signal_strength, content, source_type, source_ref, created_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(facet_id, source_ref) DO UPDATE SET
                signal_position=excluded.signal_position,
                signal_strength=excluded.signal_strength,
                content=excluded.content
        """, (
            "meta_cognition.narrative_agency", agency, confidence,
            f"Narrative agency={agency:.2f} | redemptive={redemptive:.2f} | coherence={coherence:.2f} | theme: {theme[:100]}",
            "narrative_analysis", "narrative:current",
            now
        ))

        # Store nuclear episodes
        for ne in result.get("nuclear_episodes", []):
            ep_type = f"narrative_{ne.get('type', 'node')}"
            content = ne.get("content", "").strip()
            if not content:
                continue
            # Use fixed source_ref per type so re-runs update in place
            db.execute("""
                INSERT INTO episodes
                    (episode_type, content, source_type, source_ref, confidence,
                     extracted_at, active)
                VALUES (?,?,?,?,?,?,1)
                ON CONFLICT(episode_type, source_ref) DO UPDATE SET
                    content=excluded.content,
                    confidence=excluded.confidence,
                    extracted_at=excluded.extracted_at
            """, (
                ep_type, content, "narrative_analysis",
                f"narrative:{ep_type}",
                confidence, now
            ))
            info(SCRIPT, "nuclear_episode_stored", episode_type=ep_type, content=content[:60])

        mark_ran(db, "narrative")
        db.commit()

        info(SCRIPT, "run_complete",
             agency=round(agency, 2),
             redemptive=round(redemptive, 2),
             coherence=round(coherence, 2),
             theme=theme[:80])

    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Narrative Identity")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(model=args.model, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
