"""Minimal structured logging shim for Soulkiller OSS."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone


def _emit(level: str, script: str, event: str, **kwargs: object) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": level,
        "script": script,
        "event": event,
        **kwargs,
    }
    stream = sys.stderr if level in ("WARN", "ERROR") else sys.stdout
    print(json.dumps(record, ensure_ascii=False), file=stream)


def info(script: str, event: str, **kwargs: object) -> None:
    _emit("INFO", script, event, **kwargs)


def warn(script: str, event: str, **kwargs: object) -> None:
    _emit("WARN", script, event, **kwargs)


def error(script: str, event: str, **kwargs: object) -> None:
    _emit("ERROR", script, event, **kwargs)
