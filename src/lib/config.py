"""Minimal config shim for Soulkiller OSS.

In production, OpenClaw provides a full nanobot config system.
This stub reads configuration from environment variables.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class _Config:
    """Simple env-driven config object."""

    def __init__(self) -> None:
        self.data_dir = Path(os.environ.get("SOULKILLER_DATA_DIR", "") or _default_data_dir())
        self.subject_id = os.environ.get("SOULKILLER_SUBJECT_ID", "demo-subject")
        self.subject_name = os.environ.get("SOULKILLER_SUBJECT_NAME", "Demo Subject")
        self.model = os.environ.get("SOULKILLER_MODEL", "")
        self.provider = os.environ.get("SOULKILLER_PROVIDER", "")
        self.openclaw_bin = os.environ.get("OPENCLAW_BIN", "openclaw")
        self.relational_agent = os.environ.get("SOULKILLER_RELATIONAL_AGENT", "")
        self.relational_agent_ids = [
            x.strip()
            for x in os.environ.get("SOULKILLER_RELATIONAL_AGENT_IDS", self.relational_agent).split(",")
            if x.strip()
        ]

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)


def _default_data_dir() -> str:
    openclaw_home = os.environ.get("OPENCLAW_HOME", os.environ.get("HOME", ""))
    return str(Path(openclaw_home) / ".openclaw" / "runtime" / "soulkiller")


_CONFIG: _Config | None = None


def get_config() -> _Config:
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _Config()
    return _CONFIG


def load_nanobot_config(path: str | None = None) -> dict[str, Any]:
    """Return a minimal nanobot-compatible config dict from env vars."""
    cfg = get_config()
    return {
        "model": cfg.model,
        "provider": cfg.provider,
        "subject_id": cfg.subject_id,
        "subject_name": cfg.subject_name,
        "openclaw_bin": cfg.openclaw_bin,
        "data_dir": str(cfg.data_dir),
    }


def openclaw_home() -> Path:
    home = os.environ.get("OPENCLAW_HOME", os.environ.get("HOME", ""))
    return Path(home) / ".openclaw"
