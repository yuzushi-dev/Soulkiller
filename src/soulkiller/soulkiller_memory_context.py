"""Soulkiller Memory Context — operational memory layer for agent sessions.

Reads from soulkiller.db (hypotheses, traits, facets, entities) and produces
a compact, psychologically-grounded context bundle for agent injection.

Unlike raw decision/episode extraction, this module derives session-relevant
context from Soulkiller's analytical model output:
  - Confirmed behavioral hypotheses (cross-facet patterns)
  - High-confidence personality trait positions
  - Active tensions and drift alerts
  - Contextually relevant entities

The output is suitable for injection into agent prompts via
soulkiller-bootstrap or any OpenClaw session hook.

Cron: soulkiller:memory-context, on-demand or before each agent session

Usage:
  python3 -m soulkiller.soulkiller_memory_context [--query "current state"]
  python3 -m soulkiller.soulkiller_memory_context --format json
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

SCRIPT = "soulkiller_memory_context"

DB_PATH = Path(
    os.environ.get("SOULKILLER_DATA_DIR")
    or str(Path(__file__).resolve().parents[1] / "soulkiller")
) / "soulkiller.db"


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def run(
    query: str = "current psychological state",
    agent_role: str = "assistant",
    max_items: int = 12,
    output_format: str = "text",
    min_confidence: float = 0.6,
) -> int:
    from lib.memory_context import MemoryContextBuilder

    db = get_db()
    builder = MemoryContextBuilder(db, min_confidence=min_confidence)
    ctx = builder.build(
        query_text=query,
        agent_role=agent_role,
        max_items=max_items,
    )

    if output_format == "json":
        payload = {
            "items": [
                {
                    "category": i.category,
                    "content":  i.content,
                    "confidence": i.confidence,
                    "source":   i.source,
                    "facet":    i.facet,
                }
                for i in ctx.items
            ],
            "summary": ctx.summary,
            "total":   len(ctx.items),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        text = ctx.format_for_injection()
        if text:
            print(text)
        else:
            print("No operational memory context available.", file=sys.stderr)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build operational memory context from Soulkiller analytical data."
    )
    parser.add_argument(
        "--query", default="current psychological state",
        help="Session query or agent task hint."
    )
    parser.add_argument(
        "--role", default="assistant",
        help="Agent role hint (assistant, coach, planner)."
    )
    parser.add_argument(
        "--max-items", type=int, default=12,
        help="Maximum items in the output bundle."
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format."
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.6,
        help="Minimum confidence threshold."
    )
    args = parser.parse_args()
    return run(
        query=args.query,
        agent_role=args.role,
        max_items=args.max_items,
        output_format=args.format,
        min_confidence=args.min_confidence,
    )


if __name__ == "__main__":
    sys.exit(main())
