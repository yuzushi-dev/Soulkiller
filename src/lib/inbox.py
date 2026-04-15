"""Generic inbox.jsonl append utility for the soulkiller pipeline.

Any component that sends a message (check-in question, follow-up reply, etc.)
can call append_to_inbox to log it bidirectionally alongside inbound messages.
This gives the extractor and correlator full conversation context.

Usage:
    from lib.inbox import append_to_inbox

    append_to_inbox(data_dir, {
        "from": "assistant",
        "direction": "sent",
        "content": "Come stai oggi?",
        "channel_id": "demo",
        "received_at": datetime.now(timezone.utc).isoformat(),
    })
"""
from __future__ import annotations

import json
from pathlib import Path


def append_to_inbox(data_dir: str | Path | None, entry: dict) -> None:
    """Append a JSON entry as a newline to inbox.jsonl inside data_dir.

    Silently skips if data_dir is empty or None — safe to call even when
    the runtime data directory is not configured.
    """
    if not data_dir:
        return
    inbox_path = Path(data_dir) / "inbox.jsonl"
    try:
        with open(inbox_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
