"""memory_context — Soulkiller native operational memory layer.

Builds a compact, psychologically-grounded context bundle for agent sessions
by reading directly from Soulkiller's analytical database.

Sources (in priority order):
  hypotheses      — cross-facet behavioral patterns, confidence-scored
  traits          — personality facet positions with observation count
  entities        — active people/projects/tools
  model_snapshots — latest synthesis state and drift alerts

Design principles:
  - Never raw decisions or episodes — those are analytical inputs, not
    operational outputs
  - Max 12-15 items per session, ordered by psychological salience
  - Every item carries provenance (which table, which id, confidence)
  - DriftRetrieval expands from active hypotheses to related facets,
    finding tensions and structural changes
  - QueryRouter selects the right psychological dimensions for the query

This module implements the MemoryProvider protocol defined in
lib.memory_provider so it can be used as the native Soulkiller provider
without requiring any external package.

Usage
-----
    import sqlite3
    from lib.memory_context import MemoryContextBuilder, SoulkillerMemoryProvider

    db = sqlite3.connect("soulkiller.db")
    db.row_factory = sqlite3.Row
    builder = MemoryContextBuilder(db)
    ctx = builder.build(query_text="current priorities", agent_role="assistant")
    print(ctx.format_for_injection())
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ContextItem:
    """A single item in the operational memory context."""

    category: str        # trait | hypothesis | tension | entity | rule
    content: str         # compact human-readable statement
    confidence: float
    source: str          # e.g. "hypotheses:42" or "traits:facet_id=3"
    facet: str | None = None   # psychological dimension (e.g. "stress_response")
    facet_category: str | None = None  # e.g. "emotional"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryContext:
    """Compact operational memory context for one agent session."""

    items: list[ContextItem] = field(default_factory=list)
    summary: str = ""
    retrieval_trace: list[dict[str, Any]] = field(default_factory=list)

    def format_for_injection(self) -> str:
        """Format the context as a compact text block for agent injection."""
        if not self.items:
            return ""

        sections: dict[str, list[ContextItem]] = {}
        for item in self.items:
            sections.setdefault(item.category, []).append(item)

        _headings = {
            "hypothesis": "Behavioral Patterns",
            "tension":    "Active Tensions",
            "trait":      "Personality Dimensions",
            "entity":     "Active Entities",
            "rule":       "Interaction Rules",
        }
        _order = ["tension", "hypothesis", "trait", "entity", "rule"]

        lines = ["PSYCHOLOGICAL CONTEXT\n"]
        for cat in _order:
            items = sections.get(cat, [])
            if not items:
                continue
            lines.append(_headings.get(cat, cat.title()))
            for i in items:
                conf_tag = f"[{i.confidence:.0%}]"
                lines.append(f"- {i.content} {conf_tag}")
            lines.append("")

        # Add any remaining categories
        for cat, items in sections.items():
            if cat not in _order:
                lines.append(cat.title())
                for i in items:
                    lines.append(f"- {i.content} [{i.confidence:.0%}]")
                lines.append("")

        return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# QueryRouter
# ---------------------------------------------------------------------------

@dataclass
class RouteResult:
    categories: list[str]
    entity_search: bool = False
    bias_confirmed: bool = False


_EMOTIONAL_PATTERNS = re.compile(
    r"\b(stress|anxi|emotion|feel|mood|worry|overwhelm|burnout|exhausted|sad|"
    r"fear|panic|depress|stress[a-z]*|tensione|stressato|ansia)\b",
    re.IGNORECASE,
)
_RELATIONAL_PATTERNS = re.compile(
    r"\b(person|people|colleague|manager|team|friend|relationship|who|chi|"
    r"relazione|collega|amico|persona)\b",
    re.IGNORECASE,
)
_DECISIONAL_PATTERNS = re.compile(
    r"\b(decide|decision|choose|choice|should I|how to|approach|strategy|"
    r"decid|scelta|come fare|come dovrei)\b",
    re.IGNORECASE,
)
_WORK_PATTERNS = re.compile(
    r"\b(work|project|task|deadline|technical|code|build|deliver|sprint|"
    r"lavoro|progetto|consegna|scadenza)\b",
    re.IGNORECASE,
)
_TEMPORAL_PATTERNS = re.compile(
    r"\b(routine|habit|schedule|plan|time|week|day|month|abitudine|piano|"
    r"settimana|futuro|past)\b",
    re.IGNORECASE,
)


class QueryRouter:
    """Routes a session query to the relevant psychological dimensions.

    Args:
        db: sqlite3.Connection to soulkiller.db.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def route(self, query_text: str) -> RouteResult:
        """Determine which psychological categories are relevant to this query."""
        categories: list[str] = []
        entity_search = False

        if _EMOTIONAL_PATTERNS.search(query_text):
            categories.append("emotional")
        if _RELATIONAL_PATTERNS.search(query_text):
            categories.append("relational")
            entity_search = True
        if _DECISIONAL_PATTERNS.search(query_text):
            categories.append("cognitive")
            categories.append("meta_cognition")
        if _WORK_PATTERNS.search(query_text):
            categories.append("cognitive")
            categories.append("temporal")
            entity_search = True
        if _TEMPORAL_PATTERNS.search(query_text):
            categories.append("temporal")

        # Deduplicate, preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in categories:
            if c not in seen:
                seen.add(c)
                unique.append(c)

        if not unique:
            # Generic: broad coverage
            unique = ["emotional", "cognitive", "relational", "temporal"]

        return RouteResult(categories=unique, entity_search=entity_search)


# ---------------------------------------------------------------------------
# DriftRetrieval
# ---------------------------------------------------------------------------

# Facet name → psychological category mapping (fallback for text extraction)
_FACET_KEYWORDS: dict[str, str] = {
    "stress":       "emotional",
    "emotion":      "emotional",
    "anxi":         "emotional",
    "social":       "relational",
    "relation":     "relational",
    "attach":       "relational",
    "routine":      "temporal",
    "temporal":     "temporal",
    "time":         "temporal",
    "analytic":     "cognitive",
    "cognitive":    "cognitive",
    "decision":     "cognitive",
    "direct":       "communication",
    "communic":     "communication",
    "growth":       "meta_cognition",
    "meta":         "meta_cognition",
    "coping":       "emotional",
    "withdrawal":   "relational",
    "isolation":    "relational",
}


class DriftRetrieval:
    """Expands operational context from active hypotheses to related facets.

    Inspired by GraphRAG's drift search: start from high-signal behavioral
    hypotheses and expand to the psychological dimensions they activate,
    finding tensions and structural changes.

    Args:
        db: sqlite3.Connection to soulkiller.db.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    def expand_from_hypotheses(
        self,
        hypothesis_ids: list[int],
        max_expansion: int = 5,
    ) -> list[ContextItem]:
        """Expand from hypothesis texts to related facet items.

        For each hypothesis, extracts mentioned facet keywords, fetches
        the corresponding trait scores, and surfaces structural tensions
        (drift alerts, contradictions).

        Args:
            hypothesis_ids: IDs of hypotheses to expand from.
            max_expansion:  Maximum items to return.

        Returns:
            List of ContextItem derived from the expansion.
        """
        if not hypothesis_ids:
            return []

        # Fetch hypothesis texts
        placeholders = ",".join("?" * len(hypothesis_ids))
        hyp_rows = self._db.execute(
            f"SELECT id, hypothesis, status, confidence FROM hypotheses WHERE id IN ({placeholders})",
            hypothesis_ids,
        ).fetchall()

        if not hyp_rows:
            return []

        # Gather facet names mentioned in hypothesis texts
        all_text = " ".join(dict(r)["hypothesis"].lower() for r in hyp_rows)
        mentioned_categories: set[str] = set()
        for keyword, cat in _FACET_KEYWORDS.items():
            if keyword in all_text:
                mentioned_categories.add(cat)

        # Also add categories from drift_alert hypotheses directly
        for row in hyp_rows:
            if dict(row)["status"] == "drift_alert":
                mentioned_categories.update(["temporal", "meta_cognition"])

        if not mentioned_categories:
            return []

        items: list[ContextItem] = []

        # Fetch traits for mentioned categories
        if mentioned_categories:
            cat_placeholders = ",".join("?" * len(mentioned_categories))
            trait_rows = self._db.execute(
                f"""
                SELECT t.facet_id, t.value_position, t.confidence, t.observation_count,
                       f.name as facet_name, f.category, f.spectrum_low, f.spectrum_high
                FROM traits t
                JOIN facets f ON t.facet_id = f.id
                WHERE f.category IN ({cat_placeholders})
                  AND t.confidence >= 0.5
                ORDER BY t.confidence DESC
                LIMIT ?
                """,
                (*mentioned_categories, max_expansion * 2),
            ).fetchall()

            for row in trait_rows:
                r = dict(row)
                pole = r["spectrum_high"] if r["value_position"] >= 0.5 else r["spectrum_low"]
                strength = abs(r["value_position"] - 0.5) * 2  # 0–1 from center
                if strength < 0.15:
                    continue  # too neutral to be operationally relevant
                content = (
                    f"{r['facet_name'].replace('_', ' ').title()}: "
                    f"strongly {pole} (position {r['value_position']:.2f}, "
                    f"{r['observation_count']} observations)"
                )
                items.append(ContextItem(
                    category="trait",
                    content=content,
                    confidence=r["confidence"],
                    source=f"traits:facet_id={r['facet_id']}",
                    facet=r["facet_name"],
                    facet_category=r["category"],
                ))

        # Surface drift_alert hypotheses as tensions
        for row in hyp_rows:
            r = dict(row)
            if r["status"] == "drift_alert" and r["confidence"] >= 0.6:
                items.append(ContextItem(
                    category="tension",
                    content=r["hypothesis"],
                    confidence=r["confidence"],
                    source=f"hypotheses:{r['id']}",
                ))

        # Sort by confidence, cap
        items.sort(key=lambda x: x.confidence, reverse=True)
        return items[:max_expansion]


# ---------------------------------------------------------------------------
# MemoryContextBuilder
# ---------------------------------------------------------------------------

class MemoryContextBuilder:
    """Builds an operational memory context from Soulkiller's analytical data.

    Args:
        db:              sqlite3.Connection to soulkiller.db.
        min_confidence:  Minimum confidence to include an item (default 0.6).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        min_confidence: float = 0.6,
    ) -> None:
        self._db = db
        self._min_conf = min_confidence
        self._router = QueryRouter(db)
        self._drift = DriftRetrieval(db)

    def build(
        self,
        query_text: str,
        agent_role: str,
        max_items: int = 12,
    ) -> MemoryContext:
        """Build an operational memory context for this session.

        Steps:
          1. Route the query to relevant psychological categories
          2. Fetch top confirmed/active hypotheses
          3. Expand via drift to related facet traits
          4. Fetch relevant traits for routed categories
          5. Fetch active entities if relational/work query
          6. Merge, deduplicate, cap, sort by confidence

        Args:
            query_text:  Current agent task or user query.
            agent_role:  Role hint (assistant, coach, planner, analyst).
            max_items:   Maximum items in the output bundle.

        Returns:
            MemoryContext with items, summary, and retrieval trace.
        """
        route = self._router.route(query_text)
        trace: list[dict[str, Any]] = []
        items: list[ContextItem] = []
        seen_sources: set[str] = set()

        def _add(item: ContextItem, step: str) -> bool:
            if item.source in seen_sources:
                return False
            if item.confidence < self._min_conf:
                return False
            seen_sources.add(item.source)
            items.append(item)
            trace.append({"source": item.source, "step": step, "confidence": item.confidence})
            return True

        # Step 1: top confirmed hypotheses (always include)
        hyp_rows = self._db.execute(
            """
            SELECT id, hypothesis, status, confidence
            FROM hypotheses
            WHERE status IN ('confirmed', 'drift_alert')
              AND confidence >= ?
            ORDER BY confidence DESC
            LIMIT ?
            """,
            (self._min_conf, max_items // 2),
        ).fetchall()

        top_hyp_ids: list[int] = []
        for row in hyp_rows:
            r = dict(row)
            cat = "tension" if r["status"] == "drift_alert" else "hypothesis"
            item = ContextItem(
                category=cat,
                content=r["hypothesis"],
                confidence=r["confidence"],
                source=f"hypotheses:{r['id']}",
            )
            if _add(item, "base_hypotheses"):
                top_hyp_ids.append(r["id"])

        # Step 2: drift expansion from top hypotheses
        drift_items = self._drift.expand_from_hypotheses(
            hypothesis_ids=top_hyp_ids,
            max_expansion=max(2, max_items // 4),
        )
        for item in drift_items:
            _add(item, "drift_expansion")

        # Step 3: traits for routed categories
        if route.categories:
            cat_ph = ",".join("?" * len(route.categories))
            trait_rows = self._db.execute(
                f"""
                SELECT t.facet_id, t.value_position, t.confidence, t.observation_count,
                       f.name as facet_name, f.category, f.spectrum_low, f.spectrum_high
                FROM traits t
                JOIN facets f ON t.facet_id = f.id
                WHERE f.category IN ({cat_ph})
                  AND t.confidence >= ?
                ORDER BY t.confidence DESC
                LIMIT ?
                """,
                (*route.categories, self._min_conf, max_items // 3),
            ).fetchall()

            for row in trait_rows:
                r = dict(row)
                pole = r["spectrum_high"] if r["value_position"] >= 0.5 else r["spectrum_low"]
                strength = abs(r["value_position"] - 0.5) * 2
                if strength < 0.15:
                    continue
                content = (
                    f"{r['facet_name'].replace('_', ' ').title()}: {pole} "
                    f"(conf {r['confidence']:.0%}, {r['observation_count']} obs)"
                )
                _add(ContextItem(
                    category="trait",
                    content=content,
                    confidence=r["confidence"],
                    source=f"traits:facet_id={r['facet_id']}",
                    facet=r["facet_name"],
                    facet_category=r["category"],
                ), "routed_traits")

        # Step 4: entities if relational/work query
        if route.entity_search:
            entity_rows = self._db.execute(
                """
                SELECT id, entity_type, name, mention_count
                FROM entities
                WHERE mention_count > 5
                ORDER BY last_seen_at DESC
                LIMIT 3
                """,
            ).fetchall()
            for row in entity_rows:
                r = dict(row)
                _add(ContextItem(
                    category="entity",
                    content=f"{r['entity_type'].title()}: {r['name']} ({r['mention_count']} mentions)",
                    confidence=min(0.95, 0.5 + r["mention_count"] / 100),
                    source=f"entities:{r['id']}",
                ), "entity_context")

        # Step 5: unverified hypotheses if budget allows
        remaining = max_items - len(items)
        if remaining > 0:
            unver_rows = self._db.execute(
                """
                SELECT id, hypothesis, confidence
                FROM hypotheses
                WHERE status = 'unverified'
                  AND confidence >= ?
                ORDER BY confidence DESC
                LIMIT ?
                """,
                (max(self._min_conf, 0.65), remaining),
            ).fetchall()
            for row in unver_rows:
                r = dict(row)
                _add(ContextItem(
                    category="hypothesis",
                    content=r["hypothesis"],
                    confidence=r["confidence"],
                    source=f"hypotheses:{r['id']}",
                    metadata={"status": "unverified"},
                ), "unverified_hypotheses")

        # Sort final list by confidence
        items.sort(key=lambda x: x.confidence, reverse=True)
        items = items[:max_items]

        # Build summary from top hypotheses
        summary = self._build_summary(items)

        return MemoryContext(items=items, summary=summary, retrieval_trace=trace)

    def _build_summary(self, items: list[ContextItem]) -> str:
        """Build a 1-2 sentence summary from top-confidence items."""
        top = [i for i in items[:4] if i.category in ("hypothesis", "tension")]
        if not top:
            top = items[:2]
        if not top:
            return ""
        parts = [i.content[:80] for i in top[:2]]
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# SoulkillerMemoryProvider — implements MemoryProvider protocol
# ---------------------------------------------------------------------------

class SoulkillerMemoryProvider:
    """Native Soulkiller implementation of the MemoryProvider protocol.

    Reads from a soulkiller.db SQLite connection and produces a MemoryBundle
    compatible with lib.memory_provider without requiring the Amber package.

    Args:
        db:              sqlite3.Connection to soulkiller.db.
        min_confidence:  Minimum confidence threshold (default 0.6).
    """

    def __init__(
        self,
        db: sqlite3.Connection,
        min_confidence: float = 0.6,
    ) -> None:
        self._db = db
        self._builder = MemoryContextBuilder(db, min_confidence=min_confidence)

    def get_operational_memory(
        self,
        subject_id: str,
        query_text: str,
        agent_role: str,
        session_context: dict[str, Any],
        limit: int = 12,
    ):
        """Return a MemoryBundle derived from Soulkiller's analytical model."""
        from lib.memory_provider import MemoryBundle, MemoryItem

        ctx = self._builder.build(
            query_text=query_text,
            agent_role=agent_role,
            max_items=limit,
        )

        # Map ContextItems to MemoryBundle categories
        bundle = MemoryBundle()
        for item in ctx.items:
            mem = MemoryItem(
                memory_id=item.source,
                memory_type=_category_to_memory_type(item.category, item.facet_category),
                title=item.content[:60],
                content=item.content,
                origin_type="observed" if "confirmed" in item.metadata.get("status", "confirmed") else "inferred",
                review_status="confirmed",
                confidence=item.confidence,
                salience=item.confidence,
            )
            _assign_to_bundle(bundle, mem, item.category)

        return bundle

    def store_interaction_summary(
        self,
        subject_id: str,
        summary: str,
        *,
        title: str = "",
        importance: float = 0.5,
    ) -> None:
        # Not applicable for native Soulkiller provider — summaries are
        # managed by soulkiller_synthesizer
        pass

    def review_memory_item(
        self,
        memory_id: str,
        action: str,
        note: str = "",
    ) -> None:
        # Review loop goes through Soulkiller's corrections table
        pass

    def health_check(self):
        """Check connectivity to soulkiller.db."""
        from lib.memory_provider import ProviderStatus
        try:
            self._db.execute("SELECT 1 FROM hypotheses LIMIT 1")
            return ProviderStatus(healthy=True, provider_name="soulkiller")
        except Exception as exc:
            return ProviderStatus(healthy=False, provider_name="soulkiller", detail=str(exc))


def _category_to_memory_type(category: str, facet_category: str | None) -> str:
    if category == "tension":
        return "contradiction"
    if category == "hypothesis":
        return "interaction_rule"
    if category == "trait":
        if facet_category == "emotional":
            return "constraint"
        if facet_category == "temporal":
            return "open_loop"
        return "active_entity"
    if category == "entity":
        return "active_entity"
    return "interaction_rule"


def _assign_to_bundle(bundle, mem, category: str) -> None:
    if category == "tension":
        bundle.contradictions.append(mem)
    elif category == "trait":
        bundle.constraints.append(mem)
    elif category == "entity":
        bundle.session_relevant.append(mem)
    else:
        bundle.interaction_rules.append(mem)
