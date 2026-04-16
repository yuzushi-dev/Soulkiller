"""Tests for MemoryContextBuilder — Soulkiller native memory layer.

Reads from hypotheses, traits, facets, entities in soulkiller.db and
produces a compact, psychologically-grounded context bundle for agents.

No LLM required. No external deps beyond stdlib.

Covers:
- MemoryContextBuilder returns MemoryContext with typed items
- Items are drawn from hypotheses and traits, not raw decisions
- Max item cap enforced
- QueryRouter selects relevant psychological dimensions per query
- DriftRetrieval expands from hypotheses to related facets
- SoulkillerMemoryProvider satisfies MemoryProvider protocol
- Empty db returns gracefully
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lib.memory_context import (
    MemoryContextBuilder,
    MemoryContext,
    ContextItem,
    QueryRouter,
    DriftRetrieval,
)
from lib.memory_provider import MemoryProvider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def empty_db(tmp_path):
    db = sqlite3.connect(str(tmp_path / "sk.db"))
    db.row_factory = sqlite3.Row
    db.executescript("""
        CREATE TABLE facets (
            id INTEGER PRIMARY KEY,
            category TEXT, name TEXT, description TEXT,
            spectrum_low TEXT, spectrum_high TEXT,
            sensitivity REAL DEFAULT 0.5, intrusion_base REAL DEFAULT 0.5
        );
        CREATE TABLE traits (
            facet_id INTEGER, value_position REAL,
            confidence REAL, observation_count INTEGER,
            last_observation_at TEXT, last_synthesis_at TEXT,
            notes TEXT, status TEXT DEFAULT 'active'
        );
        CREATE TABLE hypotheses (
            id INTEGER PRIMARY KEY,
            hypothesis TEXT, status TEXT DEFAULT 'unverified',
            supporting_observations TEXT DEFAULT '[]',
            contradicting_observations TEXT DEFAULT '[]',
            confidence REAL DEFAULT 0.5,
            created_at TEXT, updated_at TEXT
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY,
            entity_type TEXT, name TEXT, label TEXT,
            description TEXT, first_seen_at TEXT,
            last_seen_at TEXT, mention_count INTEGER DEFAULT 0
        );
        CREATE TABLE model_snapshots (
            id INTEGER PRIMARY KEY,
            snapshot_at TEXT, total_observations INTEGER,
            avg_confidence REAL, coverage_pct REAL,
            snapshot_data TEXT DEFAULT '{}'
        );
        CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            facet_id INTEGER, source_type TEXT, source_ref TEXT,
            content TEXT, extracted_signal TEXT,
            signal_strength REAL, signal_position REAL,
            confidence REAL DEFAULT 0.5, observed_at TEXT
        );
    """)
    db.commit()
    return db


@pytest.fixture
def populated_db(empty_db):
    db = empty_db
    # Facets
    db.executemany(
        "INSERT INTO facets(id,category,name,spectrum_low,spectrum_high) VALUES(?,?,?,?,?)",
        [
            (1, "cognitive",  "analytical_approach",    "intuitive",   "analytical"),
            (2, "emotional",  "stress_response",        "freeze",      "fight"),
            (3, "relational", "social_orientation",     "withdrawn",   "outgoing"),
            (4, "temporal",   "routine_attachment",     "spontaneous", "routine"),
            (5, "communication", "directness",          "indirect",    "direct"),
        ]
    )
    # Traits
    db.executemany(
        "INSERT INTO traits(facet_id,value_position,confidence,observation_count) VALUES(?,?,?,?)",
        [
            (1, 0.84, 0.82, 1090),
            (2, 0.65, 0.75,  320),
            (3, 0.30, 0.70,  180),
            (4, 0.03, 0.80,   90),  # low routine_attachment (recent drift)
            (5, 0.85, 0.86,  941),
        ]
    )
    # Hypotheses
    db.executemany(
        "INSERT INTO hypotheses(id,hypothesis,status,confidence) VALUES(?,?,?,?)",
        [
            (1, "Tends to isolate under cognitive overload, reducing social engagement significantly.", "confirmed", 0.92),
            (2, "Shows strong analytical bias in decision-making, preferring data over intuition.", "confirmed", 0.88),
            (3, "Routine attachment facet in decline — structural disruption to temporal stability.", "drift_alert", 0.85),
            (4, "Direct communication style contrasts with tendency to withdraw in conflict.", "unverified", 0.60),
            (5, "Possible anxiety pattern linked to deadline proximity.", "unverified", 0.45),
        ]
    )
    # Entities
    db.executemany(
        "INSERT INTO entities(id,entity_type,name,mention_count,last_seen_at) VALUES(?,?,?,?,?)",
        [
            (1, "project", "ProjectAlpha", 24, "2026-04-15T10:00:00"),
            (2, "person",  "Manager",       12, "2026-04-14T08:00:00"),
        ]
    )
    db.commit()
    return db


# ---------------------------------------------------------------------------
# MemoryContext shape
# ---------------------------------------------------------------------------

def test_memory_context_has_items_and_summary(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="current state", agent_role="assistant")
    assert isinstance(ctx, MemoryContext)
    assert isinstance(ctx.items, list)
    assert isinstance(ctx.summary, str)
    assert isinstance(ctx.retrieval_trace, list)


def test_context_item_has_required_fields(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    assert len(ctx.items) > 0
    item = ctx.items[0]
    assert hasattr(item, "category")
    assert hasattr(item, "content")
    assert hasattr(item, "confidence")
    assert hasattr(item, "source")


# ---------------------------------------------------------------------------
# Source: hypotheses and traits, not raw decisions
# ---------------------------------------------------------------------------

def test_items_come_from_hypotheses(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    sources = [i.source for i in ctx.items]
    assert any("hypotheses" in s for s in sources)


def test_items_come_from_traits(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    sources = [i.source for i in ctx.items]
    assert any("traits" in s or "facets" in s for s in sources)


def test_no_raw_decisions_in_output(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    sources = [i.source for i in ctx.items]
    assert not any("decisions" in s for s in sources)


# ---------------------------------------------------------------------------
# Confidence ordering
# ---------------------------------------------------------------------------

def test_high_confidence_items_come_first(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="analytical style", agent_role="assistant")
    if len(ctx.items) >= 2:
        assert ctx.items[0].confidence >= ctx.items[-1].confidence


def test_low_confidence_hypothesis_excluded(populated_db):
    builder = MemoryContextBuilder(populated_db, min_confidence=0.7)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    assert all(i.confidence >= 0.7 for i in ctx.items)


# ---------------------------------------------------------------------------
# Item cap
# ---------------------------------------------------------------------------

def test_max_items_enforced(populated_db):
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant", max_items=5)
    assert len(ctx.items) <= 5


# ---------------------------------------------------------------------------
# QueryRouter
# ---------------------------------------------------------------------------

def test_router_emotional_query_targets_emotional_facets(populated_db):
    router = QueryRouter(populated_db)
    result = router.route("how am I handling stress and anxiety?")
    assert "emotional" in result.categories


def test_router_relational_query_targets_relational(populated_db):
    router = QueryRouter(populated_db)
    result = router.route("what do I know about my manager?")
    assert "relational" in result.categories or "entity" in result.categories


def test_router_work_query_targets_cognitive(populated_db):
    router = QueryRouter(populated_db)
    result = router.route("how should I approach this technical decision?")
    assert "cognitive" in result.categories


def test_router_generic_query_returns_broad_coverage(populated_db):
    router = QueryRouter(populated_db)
    result = router.route("how are things going?")
    assert len(result.categories) >= 2


# ---------------------------------------------------------------------------
# DriftRetrieval
# ---------------------------------------------------------------------------

def test_drift_expands_from_hypothesis(populated_db):
    drift = DriftRetrieval(populated_db)
    # Hypothesis 3 mentions "routine attachment" → should expand to facet 4
    items = drift.expand_from_hypotheses(
        hypothesis_ids=[3],
        max_expansion=3,
    )
    assert len(items) >= 1
    assert any("routine" in i.content.lower() or "temporal" in i.source for i in items)


def test_drift_detects_tensions(populated_db):
    drift = DriftRetrieval(populated_db)
    items = drift.expand_from_hypotheses(hypothesis_ids=[1, 3], max_expansion=5)
    # Should detect tension: isolates (social withdrawal) + drift in routine
    categories = [i.category for i in items]
    assert "tension" in categories or "hypothesis" in categories


def test_drift_respects_expansion_limit(populated_db):
    drift = DriftRetrieval(populated_db)
    items = drift.expand_from_hypotheses(hypothesis_ids=[1, 2, 3, 4], max_expansion=3)
    assert len(items) <= 3


# ---------------------------------------------------------------------------
# Empty database
# ---------------------------------------------------------------------------

def test_empty_db_returns_empty_context(empty_db):
    builder = MemoryContextBuilder(empty_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    assert isinstance(ctx, MemoryContext)
    assert ctx.items == []


def test_empty_db_drift_returns_empty(empty_db):
    drift = DriftRetrieval(empty_db)
    items = drift.expand_from_hypotheses(hypothesis_ids=[], max_expansion=5)
    assert items == []


# ---------------------------------------------------------------------------
# SoulkillerMemoryProvider
# ---------------------------------------------------------------------------

def test_soulkiller_provider_satisfies_memory_provider_protocol(populated_db):
    from lib.memory_context import SoulkillerMemoryProvider
    provider = SoulkillerMemoryProvider(db=populated_db)
    assert isinstance(provider, MemoryProvider)


def test_soulkiller_provider_returns_bundle(populated_db):
    from lib.memory_context import SoulkillerMemoryProvider
    provider = SoulkillerMemoryProvider(db=populated_db)
    bundle = provider.get_operational_memory(
        subject_id="demo-subject",
        query_text="current psychological state",
        agent_role="assistant",
        session_context={},
    )
    assert hasattr(bundle, "constraints")
    assert hasattr(bundle, "priorities")


def test_soulkiller_provider_health_check(populated_db):
    from lib.memory_context import SoulkillerMemoryProvider
    provider = SoulkillerMemoryProvider(db=populated_db)
    status = provider.health_check()
    assert status.healthy is True
    assert status.provider_name == "soulkiller"


def test_format_for_injection(populated_db):
    """format_for_injection returns a non-empty string for agent prompt."""
    builder = MemoryContextBuilder(populated_db)
    ctx = builder.build(query_text="anything", agent_role="assistant")
    text = ctx.format_for_injection()
    assert isinstance(text, str)
    if ctx.items:
        assert len(text) > 0
        assert "MEMORY" in text.upper() or len(text) > 10
