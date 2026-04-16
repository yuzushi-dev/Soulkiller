"""MemoryProvider — pluggable operational memory interface for Soulkiller.

Defines the contract that any memory provider must implement to integrate
with Soulkiller agents. The default (no-op) provider is NullMemoryProvider.

The reference implementation is Amber (separate repo). Users who want a
custom memory system implement MemoryProvider and configure Soulkiller to
use it via the SOULKILLER_MEMORY_PROVIDER env var or runtime config.

Provider registry
-----------------
  null        — NullMemoryProvider (built-in, always available)
  soulkiller  — SoulkillerMemoryProvider (built-in, reads from soulkiller.db)
  custom      — any importable class path, e.g. "mypackage.MyProvider"

Usage
-----
  from lib.memory_provider import load_memory_provider

  provider = load_memory_provider()   # uses SOULKILLER_MEMORY_PROVIDER
  bundle = provider.get_operational_memory(
      subject_id="demo-subject",
      query_text="current stress level",
      agent_role="assistant",
      session_context={},
  )
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class MemoryItem:
    """A single operational memory item surfaced to an agent."""

    memory_id: str
    memory_type: str          # constraint | priority | open_loop | recent_decision |
                              # relationship_note | contradiction | interaction_rule |
                              # active_entity | pending_verification | sensitive_boundary
    title: str
    content: str
    origin_type: str          # observed | inferred | confirmed | corrected
    review_status: str        # pending | confirmed | corrected | rejected | expired
    confidence: float = 0.5
    salience: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MemoryBundle:
    """Structured memory payload injected into an agent session."""

    session_relevant: list[MemoryItem] = field(default_factory=list)
    constraints: list[MemoryItem] = field(default_factory=list)
    priorities: list[MemoryItem] = field(default_factory=list)
    open_loops: list[MemoryItem] = field(default_factory=list)
    recent_decisions: list[MemoryItem] = field(default_factory=list)
    relationship_context: list[MemoryItem] = field(default_factory=list)
    contradictions: list[MemoryItem] = field(default_factory=list)
    interaction_rules: list[MemoryItem] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([
            self.session_relevant,
            self.constraints,
            self.priorities,
            self.open_loops,
            self.recent_decisions,
            self.relationship_context,
            self.contradictions,
            self.interaction_rules,
        ])

    def all_items(self) -> list[MemoryItem]:
        return (
            self.constraints
            + self.priorities
            + self.open_loops
            + self.recent_decisions
            + self.relationship_context
            + self.contradictions
            + self.interaction_rules
            + self.session_relevant
        )


@dataclass
class ProviderStatus:
    """Health status returned by a memory provider."""

    healthy: bool
    provider_name: str
    detail: str = ""


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryProvider(Protocol):
    """Structural protocol for Soulkiller memory providers.

    Any class that implements these four methods satisfies the protocol,
    regardless of inheritance. This makes provider substitution easy:
    third-party providers do not need to import this module.
    """

    def get_operational_memory(
        self,
        subject_id: str,
        query_text: str,
        agent_role: str,
        session_context: dict[str, Any],
        limit: int = 12,
    ) -> MemoryBundle:
        """Retrieve a ranked, bounded set of operational memory items.

        Args:
            subject_id:      Identifier for the subject being modeled.
            query_text:      Current agent task or user request text.
            agent_role:      Role hint (e.g. "assistant", "coach", "planner").
            session_context: Free-form dict with active entities, topics, thread id.
            limit:           Max total items across all categories (default 12).

        Returns:
            MemoryBundle with categorised items and a retrieval trace.
        """
        ...

    def store_interaction_summary(
        self,
        subject_id: str,
        summary: str,
        *,
        title: str = "",
        importance: float = 0.5,
    ) -> None:
        """Persist a compact summary of a completed session or interaction.

        Args:
            subject_id:  Subject identifier.
            summary:     Compact natural-language summary.
            title:       Optional short label.
            importance:  Salience weight [0.0, 1.0].
        """
        ...

    def review_memory_item(
        self,
        memory_id: str,
        action: str,
        note: str = "",
    ) -> None:
        """Apply a review action to an existing memory item.

        Args:
            memory_id: Target item id.
            action:    One of: confirm | correct | reject | expire |
                       downgrade_confidence | supersede
            note:      Optional free-text note for the review log.
        """
        ...

    def health_check(self) -> ProviderStatus:
        """Return the operational status of this provider."""
        ...


# ---------------------------------------------------------------------------
# Null provider (built-in default)
# ---------------------------------------------------------------------------

class NullMemoryProvider:
    """No-op memory provider.

    Used when no memory system is configured. Returns empty bundles and
    silently discards all writes. Always reports healthy.

    This is Soulkiller's built-in default so agents can run without
    requiring Amber or any external memory dependency.
    """

    def get_operational_memory(
        self,
        subject_id: str,
        query_text: str,
        agent_role: str,
        session_context: dict[str, Any],
        limit: int = 12,
    ) -> MemoryBundle:
        return MemoryBundle()

    def store_interaction_summary(
        self,
        subject_id: str,
        summary: str,
        *,
        title: str = "",
        importance: float = 0.5,
    ) -> None:
        pass

    def review_memory_item(
        self,
        memory_id: str,
        action: str,
        note: str = "",
    ) -> None:
        pass

    def health_check(self) -> ProviderStatus:
        return ProviderStatus(
            healthy=True,
            provider_name="null",
            detail="no-op provider — no memory system configured",
        )


# ---------------------------------------------------------------------------
# Provider loader
# ---------------------------------------------------------------------------

def _make_soulkiller_provider():
    """Lazily import SoulkillerMemoryProvider to avoid circular imports."""
    import os, sqlite3
    from pathlib import Path
    data_dir = os.environ.get("SOULKILLER_DATA_DIR")
    if data_dir:
        db_path = Path(data_dir) / "soulkiller.db"
    else:
        # Try common locations
        candidates = [
            Path(__file__).resolve().parents[1] / "soulkiller" / "soulkiller.db",
        ]
        db_path = next((p for p in candidates if p.exists()), candidates[0])
    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row
    from lib.memory_context import SoulkillerMemoryProvider
    return SoulkillerMemoryProvider(db=db)


_BUILTIN_PROVIDERS: dict[str, type] = {
    "null": NullMemoryProvider,
}


def load_memory_provider(provider_name: str | None = None) -> MemoryProvider:
    """Instantiate and return the configured memory provider.

    Resolution order:
    1. ``provider_name`` argument (if given)
    2. ``SOULKILLER_MEMORY_PROVIDER`` env var
    3. ``"null"`` (NullMemoryProvider)

    For the ``"amber"`` provider, the ``amber`` package must be installed
    and expose ``amber.provider.AmberMemoryProvider``.

    For custom providers, pass a dotted import path:
    ``"mypackage.module.MyProvider"``

    Args:
        provider_name: Provider key or dotted class path. Optional.

    Returns:
        An instance satisfying the MemoryProvider protocol.

    Raises:
        ImportError: If the requested provider cannot be imported.
        TypeError:   If the resolved class does not satisfy MemoryProvider.
    """
    name = provider_name or os.environ.get("SOULKILLER_MEMORY_PROVIDER", "null")

    if name in _BUILTIN_PROVIDERS:
        return _BUILTIN_PROVIDERS[name]()

    if name == "soulkiller":
        return _make_soulkiller_provider()

    if name == "amber":
        try:
            from amber.provider import AmberMemoryProvider  # type: ignore[import]
            instance = AmberMemoryProvider()
        except ImportError as exc:
            raise ImportError(
                "The 'amber' memory provider requires the amber package. "
                "Install it with: pip install soulkiller-amber"
            ) from exc
    else:
        # Treat as a dotted class path: "mypackage.module.MyProvider"
        try:
            module_path, class_name = name.rsplit(".", 1)
        except ValueError:
            raise ImportError(
                f"Cannot resolve memory provider '{name}'. "
                "Use a built-in name ('null', 'amber') or a dotted class path."
            )
        import importlib
        module = importlib.import_module(module_path)
        klass = getattr(module, class_name)
        instance = klass()

    if not isinstance(instance, MemoryProvider):
        raise TypeError(
            f"'{name}' does not satisfy the MemoryProvider protocol. "
            "Implement get_operational_memory, store_interaction_summary, "
            "review_memory_item, and health_check."
        )

    return instance
