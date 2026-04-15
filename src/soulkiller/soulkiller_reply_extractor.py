#!/usr/bin/env python3
"""Soulkiller Reply Extractor — Livello 3 Feedback Loop

Processes pending check-in exchange replies (observations_extracted=0)
and converts them into soulkiller observations, closing the human
feedback loop.

For each exchange with a reply:
  1. Builds a prompt with question + reply + facet spectrum
  2. Calls LLM to extract value_position on the spectrum
  3. Inserts observation with high confidence (human-stated signal)
  4. Marks exchange as extracted

Cron: soulkiller:reply-extract, every 6 hours

Usage:
  python3 soulkiller_reply_extractor.py [--model ...] [--dry-run] [--limit N]
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
from lib.log import info, warn, error

SCRIPT = "soulkiller_reply_extractor"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
DEFAULT_MODEL = "openrouter/openai/gpt-oss-120b:free"
LLM_TIMEOUT_SECONDS = 180
BATCH_SIZE = 5  # process N exchanges per LLM call


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_pending(db, limit: int) -> list[dict]:
    rows = db.execute(
        """SELECT ce.id, ce.facet_id, ce.question_text, ce.reply_text, ce.asked_at,
                  f.spectrum_low, f.spectrum_high, f.description AS facet_description
           FROM checkin_exchanges ce
           LEFT JOIN facets f ON ce.facet_id = f.id
           WHERE ce.reply_text IS NOT NULL AND ce.observations_extracted = 0
           ORDER BY ce.id ASC
           LIMIT ?""",
        (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def store_observation(db, exchange_id: int, facet_id: str, value_position: float,
                      confidence: float, evidence: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    source_ref = f"checkin:{exchange_id}"
    db.execute(
        """INSERT INTO observations
           (facet_id, signal_position, signal_strength, content,
            source_type, source_ref, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(facet_id, source_ref)
           DO UPDATE SET signal_position=excluded.signal_position,
                         signal_strength=excluded.signal_strength,
                         content=excluded.content""",
        (facet_id, value_position, confidence, evidence,
         "checkin_reply", source_ref, now)
    )


def mark_extracted(db, exchange_ids: list[int]) -> None:
    db.executemany(
        "UPDATE checkin_exchanges SET observations_extracted=1 WHERE id=?",
        [(eid,) for eid in exchange_ids]
    )
    db.commit()


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _fix_json(s: str) -> str:
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    s = re.sub(r'(\])\s*\n(\s*\{)', r'\1,\n\2', s)
    return s


def _parse_llm_json(content: str) -> Any:
    s = content.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(lines[1:])
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()

    start = s.find("[") if "[" in s and (s.find("[") < s.find("{") if "{" in s else True) else s.find("{")
    if start == -1:
        raise ValueError(f"No JSON: {content[:100]}")

    end = s.rfind("]") if s[start] == "[" else s.rfind("}")
    candidates = [s[start:end + 1], _fix_json(s[start:end + 1])]
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError(f"Unparseable: {content[:200]}")


def _call_llm(prompt: str, model: str) -> Any:
    from lib.llm_resilience import chat_completion_content

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": (
                "You are a personality signal extractor. "
                "Return STRICT JSON only. No reasoning, no markdown."
            )},
            {"role": "user", "content": prompt},
        ],
        max_tokens=1500,
        temperature=0.1,
        timeout=LLM_TIMEOUT_SECONDS,
        title="Soulkiller Reply Extractor",
    )
    return _parse_llm_json(content)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

EXTRACT_PROMPT = """You are extracting personality signals from the subject's (Italian, 30s, tech worker)
replies to check-in questions.

For each exchange, determine where the subject's reply positions him on the spectrum.

Exchanges to analyze:
{exchanges}

Return a JSON array with one object per exchange:
[
  {{
    "exchange_id": 42,
    "facet_id": "values.fairness_model",
    "value_position": 0.2,
    "confidence": 0.75,
    "evidence": "one-sentence interpretation of the reply in context"
  }}
]

Rules for value_position:
- 0.0 = fully at spectrum_low pole
- 1.0 = fully at spectrum_high pole
- 0.5 = neutral/ambiguous
- If the reply is evasive, meta, or off-topic: set confidence <= 0.3
- If reply is a clear behavioral/opinion signal: confidence 0.7-0.9
- If reply directly states a preference: confidence 0.85-0.95

Rules for evidence:
- Quote or paraphrase the relevant part of the reply
- Explain which pole it points toward
- Keep under 120 chars
"""


def build_prompt(exchanges: list[dict]) -> str:
    lines = []
    for ex in exchanges:
        spectrum = f"{ex.get('spectrum_low', '?')} ← → {ex.get('spectrum_high', '?')}"
        lines.append(
            f"exchange_id: {ex['id']}\n"
            f"facet_id: {ex['facet_id']}\n"
            f"spectrum: {spectrum}\n"
            f"question: {ex['question_text']}\n"
            f"reply: {ex['reply_text']}\n"
        )
    return EXTRACT_PROMPT.format(exchanges="\n---\n".join(lines))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = DEFAULT_MODEL, dry_run: bool = False, limit: int = 70) -> None:
    db = get_db()
    try:
        pending = load_pending(db, limit)
        if not pending:
            info(SCRIPT, "nothing_pending")
            return

        info(SCRIPT, "run_start", pending=len(pending))

        total_extracted = 0
        processed_ids = []

        for i in range(0, len(pending), BATCH_SIZE):
            batch = pending[i:i + BATCH_SIZE]
            prompt = build_prompt(batch)

            if dry_run:
                for ex in batch:
                    print(f"\n[exchange {ex['id']}] {ex['facet_id']}")
                    print(f"  Q: {ex['question_text']}")
                    print(f"  A: {ex['reply_text'][:100]}")
                    print(f"  spectrum: {ex.get('spectrum_low')} ← → {ex.get('spectrum_high')}")
                continue

            try:
                results = _call_llm(prompt, model)
            except Exception as e:
                warn(SCRIPT, "llm_error", batch_start=i, error=str(e))
                continue

            if not isinstance(results, list):
                warn(SCRIPT, "unexpected_format", got=type(results).__name__)
                continue

            batch_ids = []
            for r in results:
                eid = r.get("exchange_id")
                facet_id = r.get("facet_id")
                pos = r.get("value_position")
                conf = r.get("confidence", 0.5)
                evidence = r.get("evidence", "")

                if eid is None or facet_id is None or pos is None:
                    continue

                # Clamp values
                pos = max(0.0, min(1.0, float(pos)))
                conf = max(0.0, min(1.0, float(conf)))

                store_observation(db, eid, facet_id, pos, conf, evidence)
                batch_ids.append(eid)
                total_extracted += 1

                info(SCRIPT, "extracted",
                     exchange_id=eid, facet=facet_id,
                     pos=round(pos, 2), conf=round(conf, 2),
                     evidence=evidence[:80])

            mark_extracted(db, batch_ids)
            processed_ids.extend(batch_ids)

        info(SCRIPT, "run_complete",
             exchanges_processed=len(processed_ids) if not dry_run else len(pending),
             observations_created=total_extracted)

    finally:
        db.close()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Soulkiller Reply Extractor")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=70)
    args = parser.parse_args()
    run(model=args.model, dry_run=args.dry_run, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
