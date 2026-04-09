"""Minimal OpenClaw client shim for Soulkiller OSS.

In production, OpenClaw provides a full IPC client.
This stub raises NotImplementedError so callers fail clearly.
"""
from __future__ import annotations

import subprocess
import json
from typing import Any


class OpenClawClient:
    """Thin wrapper around the `openclaw` CLI binary."""

    def __init__(self, bin_path: str = "openclaw") -> None:
        self.bin_path = bin_path

    def run_agent_json(self, agent: str, message: str, thinking: str = "low") -> dict[str, Any]:
        """Send a message to an agent and return the JSON payload."""
        if not agent:
            raise ValueError("SOULKILLER_RELATIONAL_AGENT is not configured")
        result = subprocess.run(
            [self.bin_path, "agent", "run", agent, "--message", message,
             "--thinking", thinking, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        result.check_returncode()
        return json.loads(result.stdout)
