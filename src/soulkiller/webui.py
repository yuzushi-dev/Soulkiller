#!/usr/bin/env python3
"""Soulkiller Web UI — monitoring dashboard for the CPIS pipeline.

Requires the webui optional dependencies:
  pip install -e ".[webui]"

Usage:
  python -m soulkiller.webui [--port 8765] [--host 127.0.0.1]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────────

import os as _os

SCRIPT_DIR = Path(__file__).resolve().parent
HTML_PATH = SCRIPT_DIR / "soulkiller_webui.html"

def _resolve_db_path() -> Path:
    data_dir = _os.environ.get("SOULKILLER_DATA_DIR", "")
    if data_dir:
        return Path(data_dir) / "soulkiller.db"
    # fallback: repo-relative runtime/ directory (matches demo default)
    return Path(__file__).resolve().parents[3] / "runtime" / "soulkiller.db"

DB_PATH = _resolve_db_path()

# JOBS_PATH is an OpenClaw-internal file. In OSS it may not exist;
# endpoints that read it degrade gracefully.
JOBS_PATH = Path(_os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))) / "cron" / "jobs.json"

app = FastAPI(title="Soulkiller UI", docs_url=None, redoc_url=None)

# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def get_db_rw() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def safe_query(db: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """Run query; return [] if table does not exist yet."""
    try:
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


# ── Serve UI ──────────────────────────────────────────────────────────────────

@app.get("/")
def serve_ui() -> HTMLResponse:
    return HTMLResponse(HTML_PATH.read_text())


FAVICON_PATH = SCRIPT_DIR.parents[2] / "docs" / "soulkiller.svg"


@app.get("/favicon.svg")
def serve_favicon() -> Response:
    if FAVICON_PATH.exists():
        return Response(FAVICON_PATH.read_bytes(), media_type="image/svg+xml")
    return Response(status_code=404)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def api_health() -> dict:
    db = get_db()
    try:
        total_obs = safe_query(db, "SELECT COUNT(*) AS n FROM observations")[0]["n"]
        traits = safe_query(db,
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status != 'insufficient_data' THEN 1 ELSE 0 END) AS covered, "
            "AVG(confidence) AS avg_conf FROM traits")
        t = traits[0] if traits else {}
        total_facets = max(t.get("total") or 1, 1)
        active_alerts = safe_query(db,
            "SELECT COUNT(*) AS n FROM hypotheses "
            "WHERE status != 'dismissed'")[0]["n"]
        pending_checkin = safe_query(db,
            "SELECT COUNT(*) AS n FROM checkin_exchanges "
            "WHERE reply_text IS NULL AND asked_at >= datetime('now', '-48 hours')")[0]["n"]
        inbox_unprocessed = safe_query(db,
            "SELECT COUNT(*) AS n FROM inbox WHERE processed = 0")[0]["n"]
        return {
            "total_observations": total_obs,
            "trait_coverage_pct": round((t.get("covered") or 0) / total_facets * 100, 1),
            "avg_confidence": round(t.get("avg_conf") or 0, 3),
            "active_alerts": active_alerts,
            "pending_checkin": pending_checkin,
            "inbox_unprocessed": inbox_unprocessed,
            "db_ok": True,
        }
    finally:
        db.close()


# ── Cron ──────────────────────────────────────────────────────────────────────

def load_jobs() -> dict:
    if not JOBS_PATH.exists():
        return {}
    try:
        return json.loads(JOBS_PATH.read_text())
    except Exception:
        return {}


def save_jobs(data: dict) -> None:
    if not JOBS_PATH.parent.exists():
        return
    JOBS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _ms_ago(ts_ms: int, now_ms: int) -> str:
    delta = (now_ms - ts_ms) // 1000
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def _ms_from_now(ts_ms: int, now_ms: int) -> str:
    delta = (ts_ms - now_ms) // 1000
    if delta < 0:
        return "overdue"
    if delta < 60:
        return f"in {delta}s"
    if delta < 3600:
        return f"in {delta // 60}m"
    if delta < 86400:
        return f"in {delta // 3600}h"
    return f"in {delta // 86400}d"


class ToggleRequest(BaseModel):
    enabled: bool


class EntityPatch(BaseModel):
    name: str | None = None
    label: str | None = None
    entity_type: str | None = None
    description: str | None = None


class EpisodePatch(BaseModel):
    content: str | None = None
    episode_type: str | None = None
    confidence: float | None = None
    occurred_at: str | None = None
    active: int | None = None


class DecisionPatch(BaseModel):
    domain: str | None = None
    direction: str | None = None
    facet_ids: str | None = None


@app.get("/api/cron")
def api_cron() -> list[dict]:
    data = load_jobs()
    # Only expose soulkiller-owned jobs — never leak unrelated cron entries.
    jobs = [j for j in data.get("jobs", []) if j.get("id", "").startswith("soulkiller:")]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for j in jobs:
        s = j.get("state") or {}
        last_ms = s.get("lastRunAtMs")
        next_ms = s.get("nextRunAtMs")
        j["_last_run_ago"] = _ms_ago(last_ms, now_ms) if last_ms else None
        j["_next_run_in"] = _ms_from_now(next_ms, now_ms) if next_ms else None
    return jobs


@app.patch("/api/cron/{job_id}")
def api_cron_toggle(job_id: str, body: ToggleRequest) -> dict:
    if not job_id.startswith("soulkiller:"):
        raise HTTPException(status_code=403, detail="not a soulkiller job")
    data = load_jobs()
    for job in data["jobs"]:
        if job["id"] == job_id:
            job["enabled"] = body.enabled
            job["updatedAtMs"] = int(datetime.now(timezone.utc).timestamp() * 1000)
            save_jobs(data)
            return {"id": job_id, "enabled": body.enabled, "ok": True}
    raise HTTPException(status_code=404, detail="job not found")


# ── Profile ───────────────────────────────────────────────────────────────────

@app.get("/api/profile")
def api_profile() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db, """
            SELECT
                f.id, f.category, f.name, f.description,
                f.spectrum_low, f.spectrum_high, f.sensitivity,
                t.value_position, t.confidence, t.observation_count,
                t.last_observation_at, t.last_synthesis_at,
                t.status, t.notes
            FROM facets f
            LEFT JOIN traits t ON f.id = t.facet_id
            ORDER BY f.category, f.name
        """)
    finally:
        db.close()


@app.get("/api/observations/summary")
def api_observations_summary() -> dict:
    db = get_db()
    try:
        by_source = safe_query(db,
            "SELECT source_type, COUNT(*) AS n FROM observations GROUP BY source_type")
        last_7d = safe_query(db,
            "SELECT source_type, COUNT(*) AS n FROM observations "
            "WHERE created_at >= datetime('now', '-7 days') GROUP BY source_type")
        total_7d = sum(r["n"] for r in last_7d)
        session_7d = next((r["n"] for r in last_7d if r["source_type"] == "session_behavioral"), 0)
        bootstrap_loop_risk = (session_7d / total_7d) > 0.60 if total_7d > 0 else False
        return {
            "by_source_all_time": by_source,
            "by_source_7d": last_7d,
            "bootstrap_loop_risk": bootstrap_loop_risk,
            "session_behavioral_pct_7d": round(session_7d / total_7d * 100, 1) if total_7d else 0,
        }
    finally:
        db.close()


@app.get("/api/model/snapshots")
def api_model_snapshots() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT snapshot_at, total_observations, avg_confidence, coverage_pct "
            "FROM model_snapshots ORDER BY snapshot_at DESC LIMIT 90")
    finally:
        db.close()


@app.get("/api/hypotheses")
def api_hypotheses() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT id, hypothesis, status, confidence, "
            "supporting_observations, created_at, updated_at "
            "FROM hypotheses ORDER BY updated_at DESC LIMIT 50")
    finally:
        db.close()


@app.get("/api/profile/fgs")
def api_fgs() -> list[dict]:
    """Facet Gap Score ranking — what the question engine would prioritize."""
    db = get_db()
    try:
        rows = safe_query(db, """
            SELECT
                f.id, f.category, f.name, f.sensitivity,
                t.value_position, t.confidence, t.observation_count, t.status,
                t.last_observation_at
            FROM facets f
            LEFT JOIN traits t ON f.id = t.facet_id
            ORDER BY
                COALESCE(t.confidence, 0) ASC,
                COALESCE(t.observation_count, 0) ASC
        """)
        now = datetime.now(timezone.utc)
        for r in rows:
            if r.get("last_observation_at"):
                try:
                    last = datetime.fromisoformat(
                        r["last_observation_at"].replace("Z", "+00:00"))
                    r["days_stale"] = (now - last).days
                except Exception:
                    r["days_stale"] = None
            else:
                r["days_stale"] = None
        return rows
    finally:
        db.close()


# ── Memory ────────────────────────────────────────────────────────────────────

@app.get("/api/memory/entities")
def api_entities() -> list[dict]:
    db = get_db()
    try:
        entities = safe_query(db,
            "SELECT id, entity_type, name, label, description, "
            "mention_count, first_seen_at, last_seen_at "
            "FROM entities ORDER BY mention_count DESC LIMIT 100")
        relations = safe_query(db,
            "SELECT entity_id, relation_type, dynamic, sentiment, updated_at "
            "FROM entity_relations ORDER BY updated_at DESC")
        rel_map: dict[int, dict] = {}
        for r in relations:
            if r["entity_id"] not in rel_map:
                rel_map[r["entity_id"]] = r
        for e in entities:
            e["relation"] = rel_map.get(e["id"])
        return entities
    finally:
        db.close()


@app.get("/api/memory/episodes")
def api_episodes() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT id, episode_type, content, source_type, confidence, "
            "occurred_at, extracted_at, entity_names, active "
            "FROM episodes WHERE active = 1 "
            "ORDER BY extracted_at DESC LIMIT 200")
    finally:
        db.close()


@app.get("/api/memory/decisions")
def api_decisions() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT id, decision, domain, direction, facet_ids, "
            "decided_at, extracted_at, context "
            "FROM decisions ORDER BY decided_at DESC LIMIT 100")
    finally:
        db.close()


@app.patch("/api/memory/entities/{entity_id}")
def api_entity_patch(entity_id: int, body: EntityPatch) -> dict:
    fields: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    if "name" in fields and not fields["name"].strip():
        raise HTTPException(status_code=422, detail="name cannot be empty")
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="entity not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE entities SET {set_clause} WHERE id = ?",
                   (*fields.values(), entity_id))
        db.commit()
        return {"ok": True, "id": entity_id}
    finally:
        db.close()


@app.delete("/api/memory/entities/{entity_id}")
def api_entity_delete(entity_id: int) -> dict:
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM entities WHERE id = ?", (entity_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="entity not found")
        db.execute("DELETE FROM entity_relations WHERE entity_id = ?", (entity_id,))
        db.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        db.commit()
        return {"ok": True, "id": entity_id}
    finally:
        db.close()


@app.patch("/api/memory/episodes/{episode_id}")
def api_episode_patch(episode_id: int, body: EpisodePatch) -> dict:
    fields: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    if "confidence" in fields and not (0.0 <= fields["confidence"] <= 1.0):
        raise HTTPException(status_code=422, detail="confidence must be 0.0–1.0")
    if "active" in fields and fields["active"] not in (0, 1):
        raise HTTPException(status_code=422, detail="active must be 0 or 1")
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="episode not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE episodes SET {set_clause} WHERE id = ?",
                   (*fields.values(), episode_id))
        db.commit()
        return {"ok": True, "id": episode_id}
    finally:
        db.close()


@app.delete("/api/memory/episodes/{episode_id}")
def api_episode_delete(episode_id: int) -> dict:
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM episodes WHERE id = ?", (episode_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="episode not found")
        db.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
        db.commit()
        return {"ok": True, "id": episode_id}
    finally:
        db.close()


@app.patch("/api/memory/decisions/{decision_id}")
def api_decision_patch(decision_id: int, body: DecisionPatch) -> dict:
    fields: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="no fields to update")
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="decision not found")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE decisions SET {set_clause} WHERE id = ?",
                   (*fields.values(), decision_id))
        db.commit()
        return {"ok": True, "id": decision_id}
    finally:
        db.close()


@app.delete("/api/memory/decisions/{decision_id}")
def api_decision_delete(decision_id: int) -> dict:
    db = get_db_rw()
    try:
        row = db.execute("SELECT id FROM decisions WHERE id = ?", (decision_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="decision not found")
        db.execute("DELETE FROM decisions WHERE id = ?", (decision_id,))
        db.commit()
        return {"ok": True, "id": decision_id}
    finally:
        db.close()


@app.get("/api/checkin/history")
def api_checkin_history() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT id, facet_id, question_text, reply_text, "
            "reply_captured_at, observations_extracted, asked_at, followup_sent_at "
            "FROM checkin_exchanges ORDER BY asked_at DESC LIMIT 50")
    finally:
        db.close()


# ── Deep constructs ───────────────────────────────────────────────────────────

@app.get("/api/metrics/communication")
def api_communication_metrics() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT platform, chat_id, period, metric_type, metric_data, "
            "sample_size, computed_at FROM communication_metrics "
            "ORDER BY computed_at DESC LIMIT 100")
    finally:
        db.close()


@app.get("/api/metrics/liwc")
def api_liwc() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT * FROM liwc_metrics ORDER BY week_label DESC LIMIT 12")
    finally:
        db.close()


@app.get("/api/metrics/stress")
def api_stress() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT * FROM stress_snapshots ORDER BY computed_at DESC LIMIT 24")
    finally:
        db.close()


@app.get("/api/motives")
def api_motives() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT n_ach, n_aff, n_pow, sample_size, evidence, computed_at "
            "FROM implicit_motives ORDER BY computed_at DESC LIMIT 5")
    finally:
        db.close()


@app.get("/api/schemas")
def api_schemas() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT * FROM schemas ORDER BY updated_at DESC LIMIT 50")
    finally:
        db.close()


# ── Biofeedback ───────────────────────────────────────────────────────────────

@app.get("/api/biofeedback")
def api_biofeedback() -> list[dict]:
    db = get_db()
    try:
        return safe_query(db,
            "SELECT date, signal_type, value, unit, metadata_json, pulled_at "
            "FROM biofeedback_readings ORDER BY date DESC, signal_type LIMIT 300")
    finally:
        db.close()


@app.get("/api/biofeedback/summary")
def api_biofeedback_summary() -> dict:
    db = get_db()
    try:
        rows = safe_query(db,
            "SELECT signal_type, value, date FROM biofeedback_readings "
            "WHERE date >= date('now', '-7 days') ORDER BY date DESC")
        by_signal: dict[str, list] = {}
        for r in rows:
            by_signal.setdefault(r["signal_type"], []).append(
                {"date": r["date"], "value": r["value"]}
            )
        latest_date = rows[0]["date"] if rows else None
        latest: dict[str, float] = {}
        for r in rows:
            if r["date"] == latest_date and r["signal_type"] not in latest:
                latest[r["signal_type"]] = r["value"]
        return {
            "latest_date": latest_date,
            "latest": latest,
            "by_signal_7d": by_signal,
            "total_readings": len(rows),
        }
    finally:
        db.close()


# ── Entry point ───────────────────────────────────────────────────────────────

# ── Operational Memory ────────────────────────────────────────────────────────

def _load_provider():
    """Return a SoulkillerMemoryProvider backed by the active DB_PATH.

    In OSS this is the demo DB (or SOULKILLER_DATA_DIR).
    No external package required — reads hypotheses/traits/entities directly.
    """
    import sys as _sys
    _lib = Path(__file__).resolve().parents[2] / "lib"
    if _lib.exists() and str(_lib) not in _sys.path:
        _sys.path.insert(0, str(_lib))
    from lib.memory_context import SoulkillerMemoryProvider
    return SoulkillerMemoryProvider(db=get_db())


def _subject_id() -> str:
    return _os.environ.get("SOULKILLER_SUBJECT_ID", "demo-subject")


@app.get("/api/memory/provider/status")
def api_memory_provider_status() -> dict:
    try:
        provider = _load_provider()
        status = provider.health_check()
        return {
            "provider": status.provider_name,
            "healthy": status.healthy,
            "detail": status.detail,
        }
    except Exception as exc:
        return {"provider": "error", "healthy": False, "detail": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Soulkiller Web UI")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
