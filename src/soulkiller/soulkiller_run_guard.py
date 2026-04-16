"""
RunGuard - delta triggering per gli analyzer Soulkiller.

Traccia l'ultimo inbox.id processato per ogni analyzer.
Se non sono arrivati nuovi messaggi da allora, should_skip() restituisce True
e l'analyzer può uscire subito senza chiamare l'LLM.

Usage:
    from soulkiller_run_guard import should_skip, mark_ran

    db = get_db()
    if should_skip(db, "motives"):
        sys.exit(0)
    # ... lavoro ...
    mark_ran(db, "motives")
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

GUARD_TABLE = "analyzer_runs"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {GUARD_TABLE} (
    analyzer      TEXT PRIMARY KEY,
    last_run_at   TEXT NOT NULL,
    last_inbox_id INTEGER
)
"""


def _ensure_table(db: sqlite3.Connection) -> None:
    db.execute(_DDL)
    db.commit()


def should_skip(db: sqlite3.Connection, analyzer: str, *, verbose: bool = False) -> bool:
    """Return True if no new inbox messages since this analyzer's last run.

    Handles edge cases:
    - Never ran → False (must run)
    - last_inbox_id NULL + inbox empty → True (nothing to do)
    - last_inbox_id NULL + inbox has rows → False (first time with data)
    """
    _ensure_table(db)

    row = db.execute(
        f"SELECT last_inbox_id FROM {GUARD_TABLE} WHERE analyzer=?",
        (analyzer,),
    ).fetchone()

    if row is None:
        return False  # mai girato

    last_id = row[0]

    max_row = db.execute("SELECT MAX(id) FROM inbox").fetchone()
    max_id = max_row[0] if max_row else None  # None se inbox vuota

    if last_id is None and max_id is None:
        # inbox sempre vuota - niente da fare
        return True
    if last_id is None and max_id is not None:
        # la prima volta che ci sono messaggi
        return False

    skip = (max_id is None) or (max_id <= last_id)
    if skip and verbose:
        print(f"[run_guard] {analyzer}: nessun nuovo messaggio (last={last_id}, max={max_id}) - skip")
    return skip


def mark_ran(db: sqlite3.Connection, analyzer: str) -> None:
    """Registra che l'analyzer ha appena girato con successo."""
    _ensure_table(db)

    max_row = db.execute("SELECT MAX(id) FROM inbox").fetchone()
    last_inbox_id = max_row[0] if max_row else None

    db.execute(
        f"""
        INSERT INTO {GUARD_TABLE} (analyzer, last_run_at, last_inbox_id)
        VALUES (?, ?, ?)
        ON CONFLICT(analyzer) DO UPDATE SET
            last_run_at   = excluded.last_run_at,
            last_inbox_id = excluded.last_inbox_id
        """,
        (analyzer, datetime.now(timezone.utc).isoformat(), last_inbox_id),
    )
    db.commit()
