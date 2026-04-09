#!/usr/bin/env python3
"""Build a self-contained static demo page for GitHub Pages.

Generates the demo SQLite DB, queries every API endpoint, embeds the
results as ``window.SOULKILLER_STATIC`` in the webui HTML, and writes
the output to ``_site/index.html``.

Usage:
    python scripts/build_static_demo.py [--out _site]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from soulkiller.demo_runner import _write_demo_db, _write_demo_jobs  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────

def safe_query(db: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    try:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def load_jobs(jobs_path: Path) -> list[dict]:
    if not jobs_path.exists():
        return []
    data = json.loads(jobs_path.read_text())
    jobs = [j for j in data.get("jobs", []) if j.get("id", "").startswith("soulkiller:")]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for j in jobs:
        s = j.get("state") or {}
        last_ms = s.get("lastRunAtMs")
        next_ms = s.get("nextRunAtMs")

        def ms_ago(ts: int) -> str:
            d = (now_ms - ts) // 1000
            if d < 60: return f"{d}s ago"
            if d < 3600: return f"{d // 60}m ago"
            if d < 86400: return f"{d // 3600}h ago"
            return f"{d // 86400}d ago"

        def ms_from_now(ts: int) -> str:
            d = (ts - now_ms) // 1000
            if d < 0: return "overdue"
            if d < 60: return f"in {d}s"
            if d < 3600: return f"in {d // 60}m"
            if d < 86400: return f"in {d // 3600}h"
            return f"in {d // 86400}d"

        j["_last_run_ago"] = ms_ago(last_ms) if last_ms else None
        j["_next_run_in"] = ms_from_now(next_ms) if next_ms else None
    return jobs


# ── main build ────────────────────────────────────────────────────────────────

def build(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # Load seed from repo demo directory
        seed = json.loads((ROOT / "demo" / "profile.seed.json").read_text())

        _write_demo_db(tmp_path, seed, observations=[])
        _write_demo_jobs(tmp_path)

        db = sqlite3.connect(str(tmp_path / "soulkiller.db"))
        db.row_factory = sqlite3.Row

        # Build health payload
        total_obs = safe_query(db, "SELECT COUNT(*) AS n FROM observations")[0]["n"]
        traits = safe_query(db,
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status != 'insufficient_data' THEN 1 ELSE 0 END) AS covered, "
            "AVG(confidence) AS avg_conf FROM traits")
        t = traits[0] if traits else {}
        total_facets = max(t.get("total") or 1, 1)
        health = {
            "total_observations": total_obs,
            "trait_coverage_pct": round((t.get("covered") or 0) / total_facets * 100, 1),
            "avg_confidence": round(t.get("avg_conf") or 0, 3),
            "active_alerts": safe_query(db,
                "SELECT COUNT(*) AS n FROM hypotheses WHERE status != 'dismissed'")[0]["n"],
            "pending_checkin": 0,
            "inbox_unprocessed": 0,
            "db_ok": True,
        }

        # Build biofeedback summary
        bio_rows = safe_query(db,
            "SELECT signal_type, value, date FROM biofeedback_readings "
            "WHERE date >= date('now', '-7 days') ORDER BY date DESC")
        by_signal: dict[str, list] = {}
        for r in bio_rows:
            by_signal.setdefault(r["signal_type"], []).append(
                {"date": r["date"], "value": r["value"]})
        latest_date = bio_rows[0]["date"] if bio_rows else None
        latest: dict[str, float] = {}
        for r in bio_rows:
            if r["date"] == latest_date and r["signal_type"] not in latest:
                latest[r["signal_type"]] = r["value"]
        bio_summary = {
            "latest_date": latest_date,
            "latest": latest,
            "by_signal_7d": by_signal,
            "total_readings": len(bio_rows),
        }

        # FGS — add days_stale
        fgs_rows = safe_query(db, """
            SELECT f.id, f.category, f.name, f.sensitivity,
                   t.value_position, t.confidence, t.observation_count, t.status,
                   t.last_observation_at
            FROM facets f
            LEFT JOIN traits t ON f.id = t.facet_id
            ORDER BY COALESCE(t.confidence, 0) ASC, COALESCE(t.observation_count, 0) ASC
        """)
        now = datetime.now(timezone.utc)
        for r in fgs_rows:
            if r.get("last_observation_at"):
                try:
                    last = datetime.fromisoformat(
                        r["last_observation_at"].replace("Z", "+00:00"))
                    r["days_stale"] = (now - last).days
                except Exception:
                    r["days_stale"] = None
            else:
                r["days_stale"] = None

        # Observations summary
        by_source = safe_query(db,
            "SELECT source_type, COUNT(*) AS n FROM observations GROUP BY source_type")
        last_7d = safe_query(db,
            "SELECT source_type, COUNT(*) AS n FROM observations "
            "WHERE created_at >= datetime('now', '-7 days') GROUP BY source_type")
        total_7d = sum(r["n"] for r in last_7d)
        session_7d = next((r["n"] for r in last_7d if r["source_type"] == "session_behavioral"), 0)
        obs_summary = {
            "by_source_all_time": by_source,
            "by_source_7d": last_7d,
            "bootstrap_loop_risk": (session_7d / total_7d) > 0.60 if total_7d > 0 else False,
            "session_behavioral_pct_7d": round(session_7d / total_7d * 100, 1) if total_7d else 0,
        }

        static_data = {
            "/api/health": health,
            "/api/cron": load_jobs(tmp_path / "cron" / "jobs.json"),
            "/api/profile": safe_query(db, """
                SELECT f.id, f.category, f.name, f.description,
                       f.spectrum_low, f.spectrum_high, f.sensitivity,
                       t.value_position, t.confidence, t.observation_count,
                       t.last_observation_at, t.last_synthesis_at, t.status, t.notes
                FROM facets f LEFT JOIN traits t ON f.id = t.facet_id
                ORDER BY f.category, f.name
            """),
            "/api/observations/summary": obs_summary,
            "/api/model/snapshots": safe_query(db,
                "SELECT snapshot_at, total_observations, avg_confidence, coverage_pct "
                "FROM model_snapshots ORDER BY snapshot_at DESC LIMIT 90"),
            "/api/hypotheses": safe_query(db,
                "SELECT id, hypothesis, status, confidence, "
                "supporting_observations, created_at, updated_at "
                "FROM hypotheses ORDER BY updated_at DESC LIMIT 50"),
            "/api/profile/fgs": fgs_rows,
            "/api/memory/entities": _entities(db),
            "/api/memory/episodes": safe_query(db,
                "SELECT id, episode_type, content, source_type, confidence, "
                "occurred_at, extracted_at, entity_names, active "
                "FROM episodes WHERE active = 1 ORDER BY extracted_at DESC LIMIT 200"),
            "/api/memory/decisions": safe_query(db,
                "SELECT id, decision, domain, direction, facet_ids, "
                "decided_at, extracted_at, context "
                "FROM decisions ORDER BY decided_at DESC LIMIT 100"),
            "/api/checkin/history": safe_query(db,
                "SELECT id, facet_id, question_text, reply_text, "
                "reply_captured_at, observations_extracted, asked_at, followup_sent_at "
                "FROM checkin_exchanges ORDER BY asked_at DESC LIMIT 50"),
            "/api/metrics/communication": safe_query(db,
                "SELECT platform, chat_id, period, metric_type, metric_data, "
                "sample_size, computed_at FROM communication_metrics "
                "ORDER BY computed_at DESC LIMIT 100"),
            "/api/metrics/liwc": safe_query(db,
                "SELECT * FROM liwc_metrics ORDER BY week_label DESC LIMIT 12"),
            "/api/metrics/stress": safe_query(db,
                "SELECT * FROM stress_snapshots ORDER BY computed_at DESC LIMIT 24"),
            "/api/motives": safe_query(db,
                "SELECT n_ach, n_aff, n_pow, sample_size, evidence, computed_at "
                "FROM implicit_motives ORDER BY computed_at DESC LIMIT 5"),
            "/api/schemas": safe_query(db,
                "SELECT * FROM schemas ORDER BY updated_at DESC LIMIT 50"),
            "/api/biofeedback": safe_query(db,
                "SELECT date, signal_type, value, unit, metadata_json, pulled_at "
                "FROM biofeedback_readings ORDER BY date DESC, signal_type LIMIT 300"),
            "/api/biofeedback/summary": bio_summary,
        }

        db.close()

    # Inject into HTML
    html_src = (ROOT / "src" / "soulkiller" / "soulkiller_webui.html").read_text()
    injection = (
        "<script>\n"
        "window.SOULKILLER_STATIC = "
        + json.dumps(static_data, ensure_ascii=False, default=str)
        + ";\n</script>\n"
    )
    html_out = html_src.replace("</head>", injection + "</head>", 1)

    out_path = out_dir / "index.html"
    out_path.write_text(html_out, encoding="utf-8")
    print(f"Built: {out_path}  ({out_path.stat().st_size // 1024} KB)")

    favicon_src = ROOT / "docs" / "soulkiller.svg"
    if favicon_src.exists():
        import shutil
        shutil.copy(favicon_src, out_dir / "favicon.svg")
        print(f"Copied: {out_dir / 'favicon.svg'}")


def _entities(db: sqlite3.Connection) -> list[dict]:
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build static demo for GitHub Pages")
    parser.add_argument("--out", default="_site", help="Output directory (default: _site)")
    args = parser.parse_args()
    build(Path(args.out))
