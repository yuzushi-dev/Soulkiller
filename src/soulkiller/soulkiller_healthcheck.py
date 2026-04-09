#!/usr/bin/env python3
"""Soulkiller Health Check — FAST version, verifies pipeline is working.

Cron: soulkiller:healthcheck, daily at 04:00 Europe/Rome

Optimized for speed (<10s): Only checks essential DB metrics, no LLM calls.

Exits 0 if healthy, 1 if degraded, 2 if broken.
Outputs JSON summary for cron/alerting.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = "soulkiller_healthcheck"
SOULKILLER_DIR = Path(__file__).resolve().parents[1] / "soulkiller"
DB_PATH = SOULKILLER_DIR / "soulkiller.db"
PROFILE_PATH = SOULKILLER_DIR / "PROFILE.md"


def check_db() -> dict:
    """Check DB exists and basic counts. FAST - single query."""
    if not DB_PATH.exists():
        return {"status": "broken", "error": "soulkiller.db not found"}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        # Single query for all counts
        row = db.execute("""
            SELECT 
                (SELECT COUNT(*) FROM observations) as obs,
                (SELECT COUNT(*) FROM traits WHERE confidence > 0) as traits,
                (SELECT COUNT(*) FROM facets) as facets,
                (SELECT COUNT(*) FROM inbox) as inbox_total,
                (SELECT COUNT(*) FROM inbox WHERE processed = 0) as inbox_pending
        """).fetchone()

        return {
            "status": "ok",
            "observations": row["obs"],
            "traits": row["traits"],
            "facets": row["facets"],
            "inbox_total": row["inbox_total"],
            "inbox_pending": row["inbox_pending"],
        }
    except Exception as e:
        return {"status": "broken", "error": str(e)}
    finally:
        db.close()


def check_pipeline_activity() -> dict:
    """Check if pipeline is active (critical: detects stuck pipeline)."""
    if not DB_PATH.exists():
        return {"status": "broken", "error": "no db"}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        # Last inbox message (input flow)
        last_inbox = db.execute(
            "SELECT received_at FROM inbox ORDER BY received_at DESC LIMIT 1"
        ).fetchone()
        last_inbox_at = last_inbox["received_at"] if last_inbox else "never"

        # Last observation (extractor output) - CRITICAL: detects if extractor stopped
        last_obs = db.execute(
            "SELECT created_at FROM observations ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        last_obs_at = last_obs["created_at"] if last_obs else "never"

        # Last trait synthesis (synthesizer output) - CRITICAL: detects if synthesizer stopped
        last_synthesis = db.execute(
            "SELECT last_synthesis_at FROM traits WHERE last_synthesis_at IS NOT NULL ORDER BY last_synthesis_at DESC LIMIT 1"
        ).fetchone()
        last_synthesis_at = last_synthesis["last_synthesis_at"] if last_synthesis else "never"

        result = {
            "last_inbox_message": last_inbox_at,
            "last_observation": last_obs_at,
            "last_synthesis": last_synthesis_at,
        }

        # Check thresholds (FAST: just compare ISO strings)
        now = datetime.now(timezone.utc)
        
        # Extractor: should create observations within 24h of inbox activity
        if last_obs != "never":
            try:
                obs_dt = datetime.fromisoformat(last_obs_at.replace("Z", "+00:00"))
                gap_hours = (now - obs_dt).total_seconds() / 3600
                result["hours_since_observation"] = round(gap_hours, 1)
                if gap_hours > 24:
                    result["extractor_status"] = "stale"
                else:
                    result["extractor_status"] = "ok"
            except (ValueError, TypeError):
                result["extractor_status"] = "unknown"
        else:
            result["extractor_status"] = "no_data"

        # Synthesizer: should update traits within 48h
        if last_synthesis != "never":
            try:
                synth_dt = datetime.fromisoformat(last_synthesis_at.replace("Z", "+00:00"))
                gap_hours = (now - synth_dt).total_seconds() / 3600
                result["hours_since_synthesis"] = round(gap_hours, 1)
                if gap_hours > 48:
                    result["synthesizer_status"] = "stale"
                else:
                    result["synthesizer_status"] = "ok"
            except (ValueError, TypeError):
                result["synthesizer_status"] = "unknown"
        else:
            result["synthesizer_status"] = "no_data"

        # Overall status
        is_stale = (result.get("extractor_status") == "stale" or 
                   result.get("synthesizer_status") == "stale")
        result["status"] = "degraded" if is_stale else "ok"
        
        return result
    finally:
        db.close()


def check_profile() -> dict:
    """Check PROFILE.md exists."""
    if not PROFILE_PATH.exists():
        return {"status": "degraded", "error": "PROFILE.md not found"}

    try:
        size = PROFILE_PATH.stat().st_size
        return {"status": "ok", "size_bytes": size, "exists": True}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


def check_backup() -> dict:
    """Verify a recent SQLite backup exists (IMP-05). Backup must be < 8 days old."""
    backup_files = sorted(SOULKILLER_DIR.glob("soulkiller_backup_*.db"), reverse=True)
    if not backup_files:
        return {"status": "degraded", "error": "no backup found — run weekly backup cron"}
    latest = backup_files[0]
    age_days = (datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime) / 86400
    if age_days > 8:
        return {
            "status": "degraded",
            "error": f"backup stale: {latest.name} ({age_days:.1f}d old)",
        }
    return {"status": "ok", "backup": latest.name, "age_days": round(age_days, 1)}


def check_stale_records() -> dict:
    """Check for stuck records. FAST - only critical checks."""
    issues = []

    if not DB_PATH.exists():
        return {"status": "ok", "issues": []}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        # Only check for massive inbox backlog
        pending = db.execute("SELECT COUNT(*) as c FROM inbox WHERE processed = 0").fetchone()["c"]
        if pending > 100:
            issues.append(f"Large inbox backlog: {pending} pending")
    finally:
        db.close()

    status = "degraded" if issues else "ok"
    return {"status": status, "issues": issues}


def check_agent_influence() -> dict:
    """Check if > 60% of recent observations come from AI-session sources (IMP-14).

    High AI-mediated proportion suggests bootstrap loop risk: the model may be
    influenced by its own prior outputs rather than independent behaviour.
    """
    if not DB_PATH.exists():
        return {"status": "ok", "agent_influence_index": None}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        row = db.execute(
            """SELECT
                COUNT(*) as total,
                SUM(CASE WHEN source_type='session_behavioral' THEN 1 ELSE 0 END) as ai_mediated
               FROM observations WHERE created_at >= ?""",
            (cutoff,),
        ).fetchone()
        total = row["total"] or 0
        ai_mediated = row["ai_mediated"] or 0
        if total == 0:
            return {"status": "ok", "agent_influence_index": None, "total": 0}

        index = ai_mediated / total
        if index > 0.60:
            # Write loop_warning hypothesis if not already present this week
            db2 = sqlite3.connect(DB_PATH)
            try:
                db2.execute("PRAGMA journal_mode=WAL")
                existing = db2.execute(
                    "SELECT id FROM hypotheses WHERE hypothesis LIKE '[loop_warning]%' "
                    "AND created_at >= ?",
                    (cutoff,),
                ).fetchone()
                if not existing:
                    now = datetime.now(timezone.utc).isoformat()
                    db2.execute(
                        "INSERT INTO hypotheses (hypothesis, status, confidence, created_at, updated_at) "
                        "VALUES (?,?,?,?,?)",
                        (
                            f"[loop_warning] {ai_mediated}/{total} ({index:.0%}) delle osservazioni "
                            f"nell'ultima settimana provengono da sessioni AI. "
                            f"Rischio loop bootstrap: ridurre peso osservazioni session_behavioral.",
                            "unverified", 0.8, now, now,
                        ),
                    )
                    db2.commit()
            finally:
                db2.close()
            return {
                "status": "degraded",
                "agent_influence_index": round(index, 3),
                "ai_mediated": ai_mediated,
                "total": total,
                "warning": "bootstrap loop risk — AI-mediated observations > 60%",
            }

        return {"status": "ok", "agent_influence_index": round(index, 3),
                "ai_mediated": ai_mediated, "total": total}
    finally:
        db.close()


def main() -> int:
    """FAST healthcheck - runs in <10s."""
    checks = {
        "db": check_db(),
        "pipeline_activity": check_pipeline_activity(),
        "profile": check_profile(),
        "stale_records": check_stale_records(),
        "backup": check_backup(),
        "agent_influence": check_agent_influence(),
    }

    # Overall status
    statuses = [c["status"] for c in checks.values()]
    if "broken" in statuses:
        overall = "broken"
        exit_code = 2
    elif "degraded" in statuses:
        overall = "degraded"
        exit_code = 1
    else:
        overall = "healthy"
        exit_code = 0

    result = {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "optimized": True,  # Flag to indicate fast mode
    }

    print(json.dumps(result, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
