#!/usr/bin/env python3
"""Soulkiller Backfill - dump import + profile import + deduplication.

Steps:
  1. Load telegram dump messages into inbox (dedup by message_id)
  2. Import profile.seed.json records as episodes/facts/decisions
  3. Deduplicate entities (case-insensitive name merge)
  4. Deduplicate episodes (exact content dedup + short-text collapse)
  5. Deduplicate decisions (normalized content dedup)

Usage:
  python3 soulkiller_backfill.py [--dry-run] [--step 1|2|3]
  python3 soulkiller_backfill.py --dry-run        # preview all steps
  python3 soulkiller_backfill.py                  # run all steps
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = "soulkiller_backfill"

def _data_dir() -> Path:
    env = os.environ.get("SOULKILLER_DATA_DIR", "")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "runtime"

DB_PATH = _data_dir() / "soulkiller.db"
DUMP_PATH = _data_dir() / "dumps" / "telegram_dump.json"
PROFILE_PATH = Path(os.environ.get("SOULKILLER_DEMO_DIR", "demo")) / "profile.seed.json"
SUBJECT_FROM_ID = os.environ.get("SOULKILLER_SUBJECT_ID", "demo-subject")


def get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


# ---------------------------------------------------------------------------
# Step 1: Load dump messages into inbox
# ---------------------------------------------------------------------------

def step1_load_dump(db, dry_run: bool) -> None:
    log("=== STEP 1: Load dump messages into inbox ===")

    if not DUMP_PATH.exists():
        log(f"  SKIP - dump not found: {DUMP_PATH}")
        return

    with open(DUMP_PATH, encoding="utf-8") as f:
        raw = json.load(f)

    messages = raw if isinstance(raw, list) else raw.get("messages", [])
    log(f"  Dump: {len(messages)} total messages")

    # Get existing message_ids to dedup
    existing = set(
        r[0] for r in db.execute("SELECT message_id FROM inbox WHERE message_id IS NOT NULL").fetchall()
    )
    log(f"  Inbox already has {len(existing)} messages with message_id")

    to_insert = []
    for m in messages:
        msg_id = str(m.get("id", ""))
        content = m.get("text", "")
        # text can be a list of segments (Telegram export format)
        if isinstance(content, list):
            parts = []
            for seg in content:
                if isinstance(seg, str):
                    parts.append(seg)
                elif isinstance(seg, dict):
                    parts.append(seg.get("text", ""))
            content = "".join(parts)
        content = content.strip()
        if not content or len(content) < 3:
            continue
        if msg_id and msg_id in existing:
            continue

        from_id = str(m.get("from_id", "")).replace("user", "")
        date_str = m.get("date", "")
        # Normalize date to ISO with timezone
        if date_str and "T" in date_str and "+" not in date_str and "Z" not in date_str:
            date_str = date_str + "+00:00"

        to_insert.append((msg_id or None, from_id, content, "telegram", date_str, 0))

    log(f"  New messages to insert: {len(to_insert)} (skipped {len(messages) - len(to_insert)} existing/empty)")

    if dry_run:
        subject_new = sum(1 for r in to_insert if r[1] == SUBJECT_FROM_ID)
        log(f"  DRY-RUN: would insert {len(to_insert)} msgs ({subject_new} from the subject)")
        if to_insert:
            log(f"  Sample: [{to_insert[0][4][:10]}] {to_insert[0][2][:80]}")
        return

    inserted = 0
    for row in to_insert:
        try:
            db.execute(
                """INSERT OR IGNORE INTO inbox
                   (message_id, from_id, content, channel_id, received_at, processed)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                row
            )
            inserted += 1
        except sqlite3.IntegrityError:
            pass
    db.commit()
    subject_inserted = sum(1 for r in to_insert if r[1] == SUBJECT_FROM_ID)
    log(f"  Inserted {inserted} messages ({subject_inserted} from the subject)")


# ---------------------------------------------------------------------------
# Step 2: Import subject_profile.json records
# ---------------------------------------------------------------------------

CATEGORY_TO_EPISODE_TYPE = {
    "obiettivi":             "fact",
    "vincoli":               "fact",
    "preferenze_stile":      "preference",
    "valori":                "fact",
    "conoscenze_assimilate": "fact",
    "preferenze_relazionali":"preference",
    "decisioni":             "decision",
    "abitudine":             "habit",
}


def step2_import_profile(db, dry_run: bool) -> None:
    log("=== STEP 2: Import subject_profile.json records ===")

    if not PROFILE_PATH.exists():
        log(f"  SKIP - profile not found: {PROFILE_PATH}")
        return

    with open(PROFILE_PATH, encoding="utf-8") as f:
        dp = json.load(f)

    records = dp.get("records", [])
    # Filter: only attivo or da_verificare, skip superseded
    records = [r for r in records if r.get("stato") in ("attivo", "da_verificare")]
    log(f"  Profile records (active): {len(records)}")

    now = datetime.now(timezone.utc).isoformat()
    inserted_eps = 0
    inserted_dec = 0
    skipped = 0

    for r in records:
        record_id = r.get("id", "")
        categoria = r.get("categoria", "")
        contenuto = r.get("contenuto", "").strip()
        confidenza = float(r.get("confidenza", 0.7))
        fonte = r.get("fonte", "")

        if not contenuto:
            skipped += 1
            continue

        ep_type = CATEGORY_TO_EPISODE_TYPE.get(categoria, "fact")
        source_ref = f"profile:{record_id}"
        content_hash = hashlib.md5(contenuto.encode()).hexdigest()[:6]
        unique_ref = f"{source_ref}:{content_hash}"

        if dry_run:
            log(f"  [{ep_type}] {contenuto[:100]}")
            inserted_eps += 1
            if categoria == "decisioni":
                inserted_dec += 1
            continue

        # Insert as episode
        try:
            db.execute(
                """INSERT OR IGNORE INTO episodes
                   (episode_type, content, source_type, source_ref, confidence,
                    occurred_at, extracted_at, entity_names, context, active)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
                (ep_type, contenuto, "profile_import", unique_ref, confidenza,
                 None, now, json.dumps([]), fonte)
            )
            inserted_eps += 1
        except sqlite3.Error as e:
            log(f"  WARN episode insert: {e}")

        # Also insert decisions into decisions table
        if categoria == "decisioni":
            domain = _infer_domain(contenuto)
            dec_ref = f"profile_dec:{record_id}"
            try:
                db.execute(
                    """INSERT OR IGNORE INTO decisions
                       (decision, domain, source_type, source_ref, extracted_at, context)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (contenuto, domain, "profile_import", dec_ref, now, fonte)
                )
                inserted_dec += 1
            except sqlite3.Error as e:
                log(f"  WARN decision insert: {e}")

    if not dry_run:
        db.commit()

    prefix = "DRY-RUN: would insert" if dry_run else "Inserted"
    log(f"  {prefix} {inserted_eps} episodes, {inserted_dec} decisions (skipped {skipped})")


def _infer_domain(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ["lav", "career", "profess", "job", "work"]):
        return "lavoro"
    if any(w in t for w in ["relaz", "amico", "partner", "famiglia", "social"]):
        return "relazioni"
    if any(w in t for w in ["finan", "soldi", "invest", "money", "budget"]):
        return "finanza"
    if any(w in t for w in ["salute", "health", "sport", "fisico"]):
        return "salute"
    if any(w in t for w in ["tech", "code", "program", "software", "dev"]):
        return "tech"
    return "lifestyle"


# ---------------------------------------------------------------------------
# Step 3: Deduplicate entities
# ---------------------------------------------------------------------------

def step3_dedup_entities(db, dry_run: bool) -> None:
    log("=== STEP 3: Deduplicate entities ===")

    entities = db.execute(
        "SELECT id, entity_type, name, mention_count, description, label FROM entities ORDER BY mention_count DESC"
    ).fetchall()
    log(f"  Entities before dedup: {len(entities)}")

    # Group by (entity_type, normalized_name)
    groups: dict[tuple, list] = {}
    for e in entities:
        key = (e["entity_type"], _normalize_name(e["name"]))
        groups.setdefault(key, []).append(dict(e))

    duplicates = {k: v for k, v in groups.items() if len(v) > 1}
    log(f"  Duplicate groups found: {len(duplicates)}")

    if not duplicates:
        log("  No duplicates to merge.")
        return

    merges = 0
    for (etype, norm_name), group in duplicates.items():
        # Keep highest mention_count as canonical
        canonical = max(group, key=lambda x: x["mention_count"])
        to_merge = [e for e in group if e["id"] != canonical["id"]]
        log(f"  Merge {[e['name'] for e in to_merge]} → '{canonical['name']}' (id={canonical['id']})")

        if not dry_run:
            for dup in to_merge:
                # Reassign relations to canonical - delete first where source_ref already exists on canonical
                existing_refs = set(
                    r[0] for r in db.execute(
                        "SELECT source_ref FROM entity_relations WHERE entity_id=?",
                        (canonical["id"],)
                    ).fetchall()
                )
                db.execute(
                    "DELETE FROM entity_relations WHERE entity_id=? AND source_ref IN ({})".format(
                        ",".join("?" * len(existing_refs))
                    ),
                    (dup["id"], *existing_refs)
                ) if existing_refs else None
                db.execute(
                    "UPDATE entity_relations SET entity_id=? WHERE entity_id=?",
                    (canonical["id"], dup["id"])
                )
                # Merge mention_count
                db.execute(
                    "UPDATE entities SET mention_count=mention_count+? WHERE id=?",
                    (dup["mention_count"], canonical["id"])
                )
                # Update description if canonical is missing one
                if not canonical.get("description") and dup.get("description"):
                    db.execute(
                        "UPDATE entities SET description=? WHERE id=?",
                        (dup["description"], canonical["id"])
                    )
                db.execute("DELETE FROM entities WHERE id=?", (dup["id"],))
                merges += 1

    if not dry_run:
        db.commit()

    prefix = "DRY-RUN: would merge" if dry_run else "Merged"
    log(f"  {prefix} {merges} duplicate entity records")


def _normalize_name(name: str) -> str:
    return re.sub(r'\s+', ' ', name.lower().strip())


# ---------------------------------------------------------------------------
# Step 4: Deduplicate episodes
# ---------------------------------------------------------------------------

def step4_dedup_episodes(db, dry_run: bool) -> None:
    log("=== STEP 4: Deduplicate episodes ===")

    episodes = db.execute(
        "SELECT id, episode_type, content, confidence, active FROM episodes ORDER BY id ASC"
    ).fetchall()
    log(f"  Episodes before dedup: {len(episodes)}")

    seen_hashes: dict[str, int] = {}   # content_hash → first id
    seen_normalized: dict[str, int] = {}  # normalized short content → first id
    to_delete: list[int] = []

    for ep in episodes:
        content = ep["content"].strip()
        # Exact hash dedup
        ch = hashlib.md5(content.encode()).hexdigest()
        if ch in seen_hashes:
            to_delete.append(ep["id"])
            continue
        seen_hashes[ch] = ep["id"]

        # Normalized dedup for short episodes (< 120 chars)
        if len(content) < 120:
            norm = re.sub(r'\s+', ' ', content.lower().strip())
            norm = re.sub(r'[^\w\s]', '', norm)
            if norm in seen_normalized:
                to_delete.append(ep["id"])
                continue
            seen_normalized[norm] = ep["id"]

    log(f"  Duplicate episodes to remove: {len(to_delete)}")

    if dry_run or not to_delete:
        if dry_run and to_delete:
            for eid in to_delete[:5]:
                ep = next(e for e in episodes if e["id"] == eid)
                log(f"  DRY-RUN: would delete ep id={eid}: {ep['content'][:80]}")
        return

    for eid in to_delete:
        db.execute("DELETE FROM episodes WHERE id=?", (eid,))
    db.commit()
    log(f"  Deleted {len(to_delete)} duplicate episodes")


# ---------------------------------------------------------------------------
# Step 5: Deduplicate decisions
# ---------------------------------------------------------------------------

def step5_dedup_decisions(db, dry_run: bool) -> None:
    log("=== STEP 5: Deduplicate decisions ===")

    decisions = db.execute(
        "SELECT id, decision, domain FROM decisions ORDER BY id ASC"
    ).fetchall()
    log(f"  Decisions before dedup: {len(decisions)}")

    seen: dict[str, int] = {}
    to_delete: list[int] = []

    for dec in decisions:
        norm = re.sub(r'\s+', ' ', dec["decision"].lower().strip())
        norm = re.sub(r'[^\w\s]', '', norm)
        if norm in seen:
            to_delete.append(dec["id"])
            continue
        seen[norm] = dec["id"]

    log(f"  Duplicate decisions to remove: {len(to_delete)}")

    if dry_run or not to_delete:
        return

    for did in to_delete:
        db.execute("DELETE FROM decisions WHERE id=?", (did,))
    db.commit()
    log(f"  Deleted {len(to_delete)} duplicate decisions")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Backfill")
    p.add_argument("--dry-run", action="store_true", help="Preview without writing")
    p.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5],
                   help="Run only this step (1=dump, 2=profile, 3=dedup-entities, 4=dedup-episodes, 5=dedup-decisions)")
    args = p.parse_args()

    db = get_db()
    try:
        steps = [args.step] if args.step else [1, 2, 3, 4, 5]

        if 1 in steps:
            step1_load_dump(db, args.dry_run)
        if 2 in steps:
            step2_import_profile(db, args.dry_run)
        if 3 in steps:
            step3_dedup_entities(db, args.dry_run)
        if 4 in steps:
            step4_dedup_episodes(db, args.dry_run)
        if 5 in steps:
            step5_dedup_decisions(db, args.dry_run)

        # Final counts
        log("=== FINAL STATE ===")
        for table in ["inbox", "entities", "episodes", "decisions", "entity_relations"]:
            n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log(f"  {table}: {n}")

    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
