"""Minimal RuntimeClient shim for Soulkiller OSS.

In production, OpenClaw provides a full runtime IPC client.
This stub provides a no-op implementation for standalone usage.
"""
from __future__ import annotations

from typing import Any


class RuntimeClient:
    """No-op runtime client stub."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get(self, key: str, default: Any = None) -> Any:
        return default

    def set(self, key: str, value: Any) -> None:
        pass

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        pass
