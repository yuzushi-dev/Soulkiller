"""Integration test: load_memory_provider("amber") resolves AmberMemoryProvider.

Skipped automatically if the amber package is not installed / not on PYTHONPATH.
Run with:
    PYTHONPATH=src:../Amber-Soulkiller python3 -m pytest tests/test_amber_integration.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Try to locate Amber-Soulkiller next to soulkiller-oss
_AMBER_ROOT = Path(__file__).resolve().parents[2] / "Amber-Soulkiller"
if _AMBER_ROOT.exists() and str(_AMBER_ROOT) not in sys.path:
    sys.path.insert(0, str(_AMBER_ROOT))

amber_available = False
try:
    import amber  # noqa: F401
    amber_available = True
except ImportError:
    pass

pytestmark = pytest.mark.skipif(
    not amber_available,
    reason="amber package not available — skipping integration tests",
)


def test_load_memory_provider_amber_returns_provider():
    from lib.memory_provider import load_memory_provider, MemoryProvider

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AMBER_DATA_DIR"] = tmp
        provider = load_memory_provider("amber")
        assert isinstance(provider, MemoryProvider)


def test_load_memory_provider_amber_health_check():
    from lib.memory_provider import load_memory_provider

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AMBER_DATA_DIR"] = tmp
        provider = load_memory_provider("amber")
        status = provider.health_check()
        assert status.healthy is True
        assert status.provider_name == "amber"


def test_load_memory_provider_amber_returns_empty_bundle():
    from lib.memory_provider import load_memory_provider

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AMBER_DATA_DIR"] = tmp
        provider = load_memory_provider("amber")
        bundle = provider.get_operational_memory(
            subject_id="demo-subject",
            query_text="test query",
            agent_role="assistant",
            session_context={},
        )
        assert bundle.constraints == []
        assert bundle.priorities == []


def test_env_var_selects_amber_provider():
    from lib.memory_provider import load_memory_provider

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["SOULKILLER_MEMORY_PROVIDER"] = "amber"
        os.environ["AMBER_DATA_DIR"] = tmp
        provider = load_memory_provider()
        assert type(provider).__name__ == "AmberMemoryProvider"
