"""Tests for the MemoryProvider protocol and NullMemoryProvider.

Covers:
- structural subtyping: any class with the right methods satisfies the Protocol
- NullMemoryProvider returns correct empty shapes
- MemoryBundle and MemoryItem fields are stable
- ProviderStatus fields are stable
"""

from __future__ import annotations

from lib.memory_provider import (
    MemoryBundle,
    MemoryItem,
    MemoryProvider,
    NullMemoryProvider,
    ProviderStatus,
)


# ---------------------------------------------------------------------------
# Null provider - basic contract
# ---------------------------------------------------------------------------

def test_null_provider_satisfies_protocol():
    provider = NullMemoryProvider()
    assert isinstance(provider, MemoryProvider)


def test_null_provider_get_returns_memory_bundle():
    provider = NullMemoryProvider()
    result = provider.get_operational_memory(
        subject_id="demo-subject",
        query_text="current priorities",
        agent_role="assistant",
        session_context={},
    )
    assert isinstance(result, MemoryBundle)


def test_null_provider_bundle_is_empty():
    provider = NullMemoryProvider()
    result = provider.get_operational_memory(
        subject_id="demo-subject",
        query_text="anything",
        agent_role="general",
        session_context={},
    )
    assert result.constraints == []
    assert result.priorities == []
    assert result.open_loops == []
    assert result.recent_decisions == []
    assert result.relationship_context == []
    assert result.contradictions == []
    assert result.interaction_rules == []
    assert result.session_relevant == []
    assert result.trace == []


def test_null_provider_store_summary_is_noop():
    provider = NullMemoryProvider()
    # should not raise
    provider.store_interaction_summary(
        subject_id="demo-subject",
        summary="session ended cleanly",
        title="session summary",
        importance=0.5,
    )


def test_null_provider_review_is_noop():
    provider = NullMemoryProvider()
    # should not raise
    provider.review_memory_item(
        memory_id="mem_123",
        action="confirm",
        note="verified by operator",
    )


def test_null_provider_health_check_is_healthy():
    provider = NullMemoryProvider()
    status = provider.health_check()
    assert isinstance(status, ProviderStatus)
    assert status.healthy is True
    assert status.provider_name == "null"


# ---------------------------------------------------------------------------
# Protocol structural check - any compatible class satisfies it
# ---------------------------------------------------------------------------

class _MinimalProvider:
    """Minimal class that satisfies MemoryProvider without inheriting from it."""

    def get_operational_memory(self, subject_id, query_text, agent_role, session_context, limit=12):
        return MemoryBundle()

    def store_interaction_summary(self, subject_id, summary, *, title="", importance=0.5):
        pass

    def review_memory_item(self, memory_id, action, note=""):
        pass

    def health_check(self):
        return ProviderStatus(healthy=True, provider_name="minimal")


def test_any_compatible_class_satisfies_protocol():
    provider = _MinimalProvider()
    assert isinstance(provider, MemoryProvider)


# ---------------------------------------------------------------------------
# Data shape stability
# ---------------------------------------------------------------------------

def test_memory_bundle_default_fields():
    bundle = MemoryBundle()
    for field in (
        "session_relevant",
        "constraints",
        "priorities",
        "open_loops",
        "recent_decisions",
        "relationship_context",
        "contradictions",
        "interaction_rules",
        "trace",
    ):
        assert hasattr(bundle, field), f"MemoryBundle missing field: {field}"
        assert getattr(bundle, field) == []


def test_memory_item_required_fields():
    item = MemoryItem(
        memory_id="mem_001",
        memory_type="constraint",
        title="avoid overconfident framing",
        content="do not reframe when confidence is below 0.5",
        origin_type="confirmed",
        review_status="confirmed",
        confidence=0.9,
        salience=0.8,
    )
    assert item.memory_id == "mem_001"
    assert item.memory_type == "constraint"
    assert item.confidence == 0.9


def test_provider_status_fields():
    ok = ProviderStatus(healthy=True, provider_name="amber")
    assert ok.healthy is True
    assert ok.provider_name == "amber"
    assert ok.detail == ""

    err = ProviderStatus(healthy=False, provider_name="amber", detail="db unreachable")
    assert err.healthy is False
    assert "db unreachable" in err.detail
