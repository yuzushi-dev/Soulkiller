"""Synthetic demo pipeline for the Soulkiller OSS repo.

Simulates the full extract → accumulate → synthesize → portrait pipeline
using pre-seeded facet data and keyword-based synthetic signal extraction.
No LLM required. No personal data involved.

In the live system, each step calls an LLM. Here, the seed provides
pre-computed facet values and the extraction pass uses simple heuristics
to demonstrate the observation data structure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .public_runtime import (
    DEMO_DIR,
    DEMO_CONSOLE_FILENAME,
    DELIVERY_LOG_FILENAME,
    EVENT_LOG_FILENAME,
    MODEL_PORTRAIT_FILENAME,
    MODEL_PROFILE_FILENAME,
    SUMMARY_FILENAME,
)
from .demo_webui import write_demo_console
from .soulkiller_db import SCHEMA_SQL as _DEMO_DB_SCHEMA


# ── Synthetic signal extraction rules ────────────────────────────────────────
# In the live system, an LLM reads each message and extracts personality
# signals from it. Here we use keyword heuristics to demonstrate the
# observation data structure without requiring a model.

_SIGNAL_RULES: list[tuple[list[str], str, float, str]] = [
    # keywords, facet_id, strength, direction
    (["alone", "myself", "first pass", "independently", "by myself"],
     "relational.help_seeking", 0.78, "independent"),
    (["ask for help", "needed help", "asked", "reached out"],
     "relational.help_seeking", 0.65, "collaborative"),
    (["slowly", "slow and right", "don't make decisions quickly", "deliberate"],
     "cognitive.decision_speed", 0.82, "deliberate"),
    (["three different", "read the full", "documentation before", "see the tradeoffs"],
     "cognitive.information_gathering", 0.80, "exhaustive"),
    (["planning", "weeks out", "three months", "working backward"],
     "temporal.planning_horizon", 0.81, "long-term"),
    (["late to close", "half-done", "finish line", "satisfied with"],
     "values.work_ethic", 0.74, "maximal"),
    (["overwhelming", "write down", "resets", "noise"],
     "emotional.stress_response", 0.70, "regulated"),
    (["deep work", "interrupted", "fragmentation", "impossible to"],
     "emotional.frustration_triggers", 0.62, "low-threshold"),
    (["don't trust", "trust people quickly", "consistent about it"],
     "relational.trust_formation", 0.77, "slow"),
    (["direct feedback", "diplomatic softening", "easier to act"],
     "communication.feedback_preference", 0.83, "direct"),
    (["moved on", "explained my reasoning", "didn't change"],
     "communication.conflict_style", 0.55, "assertive"),
    (["avoiding", "conversation i need", "not let it drift"],
     "meta_cognition.self_awareness", 0.74, "high"),
    (["autonomy", "alone", "clear picture", "first pass", "prefer working"],
     "values.autonomy_drive", 0.82, "high"),
    (["direct", "concrete", "decode what", "not to say"],
     "communication.directness", 0.80, "direct"),
    (["satisfied", "worth it", "clean it came"],
     "emotional.resilience_pattern", 0.65, "resilient"),
]


def _extract_synthetic_observations(messages: list[dict]) -> list[dict]:
    """
    Simulate signal extraction: keyword-match each message against rules,
    produce observation records. Labels output as [synthetic] throughout.
    """
    observations: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for msg in messages:
        content = msg["content"].lower()
        for keywords, facet_id, strength, direction in _SIGNAL_RULES:
            if any(kw in content for kw in keywords):
                key = (msg["message_id"], facet_id)
                if key in seen:
                    continue
                seen.add(key)
                observations.append({
                    "message_id": msg["message_id"],
                    "facet_id": facet_id,
                    "signal_strength": strength,
                    "direction": direction,
                    "extracted_at": msg["received_at"],
                    "source": "[synthetic demo - keyword heuristic]",
                })

    return observations


# ── Formatting helpers ────────────────────────────────────────────────────────

def _bar(position: float, width: int = 22) -> str:
    filled = round(position * width)
    return "▓" * filled + "░" * (width - filled)


def _conf_tier(conf: float) -> str:
    if conf >= 0.70:
        return "high"
    if conf >= 0.45:
        return "moderate"
    return "low"


def _obs_label(n: int) -> str:
    return f"{n} obs"


# ── Profile document generator ────────────────────────────────────────────────

def _generate_profile_md(seed: dict, observations: list[dict]) -> str:
    facets = seed["facets"]
    by_cat: dict[str, list[dict]] = {}
    for f in facets:
        by_cat.setdefault(f["category"], []).append(f)

    # Observation count per facet (from extraction pass)
    obs_count: dict[str, int] = {}
    for o in observations:
        obs_count[o["facet_id"]] = obs_count.get(o["facet_id"], 0) + 1

    lines: list[str] = [
        "# Soulkiller - Personality Model",
        "",
        f"**Subject**: {seed['subject_name']}",
        f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}  (synthetic demo run)",
        f"**Total observations (seed + extraction pass)**: "
        f"{sum(f['observation_count'] for f in facets) + len(observations)}",
        f"**Facets modeled**: {len(facets)} / 60",
        "",
        "> ⚠️  This is a synthetic demo. All values are pre-seeded for demonstration purposes.",
        "> In the live system, facet positions and confidence scores are derived entirely",
        "> from accumulated LLM-extracted observations over time.",
        "",
        "---",
        "",
    ]

    category_order = [
        "cognitive", "emotional", "communication", "relational",
        "values", "temporal", "meta_cognition",
    ]

    for cat in category_order:
        if cat not in by_cat:
            continue
        lines.append(f"## {cat.replace('_', ' ').title()}")
        lines.append("")

        for f in sorted(by_cat[cat], key=lambda x: x["name"]):
            pos = f["position"]
            conf = f["confidence"]
            base_obs = f["observation_count"]
            extra_obs = obs_count.get(f["id"], 0)
            total_obs = base_obs + extra_obs

            tier = _conf_tier(conf)
            tier_badge = {"high": "●", "moderate": "◑", "low": "○"}[tier]
            conf_str = f"{conf:.2f}"

            lines += [
                f"### `{f['id']}`  ·  {_conf_tier(conf)} confidence",
                f"",
                f"  {f['low_label']}  [{_bar(pos)}]  {f['high_label']}",
                f"  position: **{pos:.2f}**  ·  {tier_badge} conf: {conf_str}  ·  {_obs_label(total_obs)}",
                "",
            ]

        lines.append("")

    lines += [
        "---",
        "",
        "## Hypotheses",
        "",
    ]
    for h in seed.get("hypotheses", []):
        conf_str = f"{h['confidence']:.2f}"
        tier = _conf_tier(h["confidence"])
        lines += [
            f"### {h['title']}  ·  confidence {conf_str} ({tier})",
            "",
            f"> {h['body']}",
            "",
            f"*Facets involved: {', '.join(f'`{fid}`' for fid in h['facets'])}*",
            "",
        ]

    lines += [
        "---",
        "",
        "## Active Goals",
        "",
        *[f"- {g}" for g in seed.get("goals", [])],
        "",
    ]

    return "\n".join(lines)


# ── Portrait document generator ───────────────────────────────────────────────

def _generate_portrait_md(seed: dict, observations: list[dict]) -> str:
    facets_by_id = {f["id"]: f for f in seed["facets"]}

    # Find most confident high and low facets for highlights
    high_conf = sorted(
        seed["facets"], key=lambda f: f["confidence"], reverse=True
    )[:4]

    lines: list[str] = [
        "# Portrait - Demo Subject",
        "",
        "> *Synthetic demo portrait. In the live system, this document is generated*",
        "> *by the synthesizer from accumulated observations and cross-facet hypotheses.*",
        "",
        "---",
        "",
        seed["portrait_summary"],
        "",
        "---",
        "",
        "## Behavioral Highlights",
        "",
    ]

    descriptions = {
        "cognitive.decision_speed": (
            "Decisions are made deliberately, often after exhausting available options. "
            "Speed is sacrificed for consistency. This is a stable pattern, not situational hesitation."
        ),
        "cognitive.information_gathering": (
            "Information gathering is front-loaded and extensive. "
            "The subject reads documentation fully, explores edge cases, and tolerates longer preparation phases "
            "in exchange for fewer course corrections downstream."
        ),
        "values.autonomy_drive": (
            "Autonomy functions as a primary operating condition, not a preference. "
            "The subject organizes work structure, decision processes, and pacing around the assumption of independence. "
            "Contexts that undermine this create observable friction."
        ),
        "relational.help_seeking": (
            "Help is sought as a last resort, not a first step. "
            "The subject typically exhausts independent problem-solving before engaging others - "
            "and often finds partial answers in the process. The pattern is functional but occasionally delays "
            "access to perspectives that would accelerate resolution."
        ),
        "communication.directness": (
            "Communication is direct to the point of occasionally bypassing social buffering. "
            "The subject prefers concrete, actionable feedback and extends the same to others. "
            "Diplomatic ambiguity reads as noise."
        ),
        "relational.trust_formation": (
            "Trust accumulates slowly through repeated consistency rather than initial rapport. "
            "Once established, it is durable and relatively resistant to single-incident erosion. "
            "The subject does not appear to distinguish strongly between professional and personal trust mechanisms."
        ),
        "emotional.stress_response": (
            "Stress is typically managed through externalization - writing down what's active, "
            "resetting the cognitive frame, and returning with a reduced load. "
            "The subject shows regulated rather than reactive patterns under observable pressure."
        ),
        "temporal.planning_horizon": (
            "Planning extends months out as a baseline. The subject works backward from future states, "
            "which creates overhead but compresses execution once the horizon is reached."
        ),
        "communication.feedback_preference": (
            "Direct feedback is strongly preferred over diplomatically softened delivery. "
            "The subject frames this as functional: concrete input is actionable, decoded input is not."
        ),
        "meta_cognition.self_awareness": (
            "Self-monitoring is active. The subject notices behavioral patterns in themselves "
            "(including avoidance - demo-015) and names them explicitly rather than rationalizing. "
            "This does not automatically translate to correction, but the detection layer is present."
        ),
    }

    for f in high_conf:
        fid = f["id"]
        desc = descriptions.get(fid)
        if not desc:
            continue
        lines += [
            f"**`{fid}`** - position {f['position']:.2f} · confidence {f['confidence']:.2f}",
            "",
            desc,
            "",
        ]

    lines += [
        "---",
        "",
        "## Cross-Facet Patterns",
        "",
    ]
    for h in seed.get("hypotheses", []):
        lines += [
            f"**{h['title']}**",
            "",
            h["body"],
            "",
        ]

    lines += [
        "---",
        "",
        "## Extraction Sample",
        "",
        "The following observations were produced by the synthetic extraction pass",
        "during this demo run. In the live system, equivalent records are generated",
        "by LLM analysis of each ingested message.",
        "",
    ]
    for o in observations[:6]:
        lines.append(
            f"- `{o['facet_id']}` - strength {o['signal_strength']:.2f} · "
            f"direction: {o['direction']} · source: {o['message_id']}"
        )

    lines += [
        "",
        "---",
        "",
        f"*Generated from {len(seed['facets'])} seeded facets + "
        f"{len(observations)} synthetic observations.*",
        "",
    ]

    return "\n".join(lines)


# ── Demo SQLite database ──────────────────────────────────────────────────────
# Schema is imported from soulkiller_db.SCHEMA_SQL (single source of truth).



def _write_demo_db(output_dir: Path, seed: dict, observations: list[dict]) -> None:
    """Write a demo soulkiller.db so the webui can serve synthetic data."""
    db_path = output_dir / "soulkiller.db"
    if db_path.exists():
        db_path.unlink()

    db = sqlite3.connect(str(db_path))
    try:
        db.executescript(_DEMO_DB_SCHEMA)

        now_iso = datetime.now(timezone.utc).isoformat()

        # facets + traits
        for f in seed["facets"]:
            db.execute(
                "INSERT OR REPLACE INTO facets (id, category, name, description, spectrum_low, spectrum_high, sensitivity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f["id"], f["category"], f["name"],
                 f.get("description", ""),
                 f.get("low_label", ""), f.get("high_label", ""),
                 1.0),
            )
            db.execute(
                "INSERT OR REPLACE INTO traits "
                "(facet_id, value_position, confidence, observation_count, "
                "last_observation_at, last_synthesis_at, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f["id"], f["position"], f["confidence"], f["observation_count"],
                 now_iso, now_iso, "active"),
            )

        # observations (from seed sample + synthetic extraction pass)
        seed_obs = seed.get("observations_sample", [])
        for i, o in enumerate(seed_obs):
            source_ref = o.get("message_id") or f"seed-{i:03d}"
            db.execute(
                "INSERT OR IGNORE INTO observations "
                "(facet_id, signal_strength, extracted_signal, source_type, content, source_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (o["facet_id"], o["signal_strength"], o.get("direction"),
                 "message", o.get("note", ""), source_ref, now_iso),
            )
        for i, o in enumerate(observations):
            source_ref = o.get("message_id") or f"synth-{i:03d}"
            db.execute(
                "INSERT OR IGNORE INTO observations "
                "(facet_id, signal_strength, extracted_signal, source_type, content, source_ref, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (o["facet_id"], o["signal_strength"], o.get("direction"),
                 "message", "[synthetic demo]", source_ref, o.get("extracted_at", now_iso)),
            )

        # hypotheses
        for h in seed.get("hypotheses", []):
            db.execute(
                "INSERT INTO hypotheses "
                "(hypothesis, status, confidence, supporting_observations, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (h["body"], "active", h["confidence"],
                 json.dumps(h.get("facets", [])), now_iso, now_iso),
            )

        # model snapshot
        total_obs = len(seed_obs) + len(observations)
        avg_conf = sum(f["confidence"] for f in seed["facets"]) / max(len(seed["facets"]), 1)
        db.execute(
            "INSERT INTO model_snapshots (snapshot_at, total_observations, avg_confidence, coverage_pct) "
            "VALUES (?, ?, ?, ?)",
            (now_iso, total_obs, round(avg_conf, 3), round(len(seed["facets"]) / 60 * 100, 1)),
        )

        # inbox - messages as processed
        for o in seed_obs:
            mid = o.get("message_id", "")
            if mid:
                db.execute(
                    "INSERT OR IGNORE INTO inbox (message_id, content, channel_id, received_at, processed) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (mid, o.get("note", ""), "demo", now_iso, 1),
                )

        # demo entities (person, project, concept, organization, activity)
        demo_entities = [
            ("person",       "Collaborator A",      "colleague",     "Recurring collaborator; appears in 5 message threads. Trust formation slow, relationship stable.", 12, "colleague", "stable", 0.2),
            ("person",       "Collaborator B",      "team-lead",     "Manages project delivery; subject shows assertive communication style with them.", 7, "manager", "stable", 0.1),
            ("project",      "Project Alpha",       "work-project",  "Primary technical project referenced across the demo signal set. High autonomy context.", 18, None, None, None),
            ("project",      "Project Beta",        "work-project",  "Secondary project; appears in planning and deadline-behavior signal.", 9, None, None, None),
            ("concept",      "Deep Work",           "work-pattern",  "Extended uninterrupted focus sessions. Subject references this as a prerequisite for quality output.", 11, None, None, None),
            ("concept",      "Documentation-first", "work-pattern",  "Subject's practice of reading full documentation before writing code.", 6, None, None, None),
            ("organization", "Current Employer",    "employer",      "Organization context. Subject shows mild tension between autonomy drive and collaborative demands.", 5, None, None, None),
            ("activity",     "Morning Review",      "routine",       "Recurring daily habit mentioned in temporal signal. Structured start to the day.", 4, None, None, None),
        ]
        for row in demo_entities:
            etype, name, label, desc, count = row[0], row[1], row[2], row[3], row[4]
            rel_type, dynamic, sentiment = row[5], row[6], row[7]
            cur = db.execute(
                "INSERT INTO entities (entity_type, name, label, description, mention_count, first_seen_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (etype, name, label, desc, count, now_iso, now_iso),
            )
            if rel_type:
                db.execute(
                    "INSERT OR IGNORE INTO entity_relations "
                    "(entity_id, relation_type, dynamic, sentiment, source_ref, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (cur.lastrowid, rel_type, dynamic, sentiment, f"demo-entity-{cur.lastrowid}", now_iso),
                )

        # demo episodes
        demo_episodes = [
            ("behavioral_pattern",
             "Subject resolved a multi-step technical problem independently before seeking input. "
             "Consistent with help_seeking independence pattern (pos 0.22). "
             "Explicitly framed asking for help as a fallback, not a first step.",
             "message", 0.78, '["Collaborator A"]'),
            ("preference",
             "Explicit preference stated for front-loaded information gathering over iterative discovery. "
             "Subject read full documentation before writing any code. "
             "Described this as a personal policy rather than a situational choice.",
             "message", 0.82, '[]'),
            ("behavioral_pattern",
             "Subject described slow, deliberate decision-making under time pressure. "
             "Explicitly chose consistency over speed and expressed comfort with that trade-off.",
             "message", 0.84, '[]'),
            ("emotional_event",
             "Described cognitive overload management via externalization technique: "
             "writes down all active threads, then resets. "
             "Pattern shows regulated stress response (pos 0.71).",
             "message", 0.70, '[]'),
            ("preference",
             "Strong preference for direct, unambiguous feedback. "
             "Subject stated diplomatic softening requires extra decoding effort and slows action.",
             "message", 0.83, '[]'),
            ("habit",
             "Structured morning review routine mentioned across multiple messages. "
             "Subject describes it as a prerequisite for focused work later in the day.",
             "message", 0.61, '["Morning Review"]'),
            ("behavioral_pattern",
             "Subject avoided a difficult interpersonal conversation for several days before initiating it. "
             "Noted awareness of the avoidance pattern while it was happening. "
             "Meta-cognitive detection present; correction delayed.",
             "message", 0.67, '["Collaborator B"]'),
            ("preference",
             "Works best with a clear picture of the problem before involving others. "
             "Describes collaborative early-phase work as cognitively expensive.",
             "message", 0.79, '[]'),
            ("fact",
             "Plans 3+ months out as a baseline. Works backward from future states. "
             "Subject described current project planning horizon as 12 weeks.",
             "message", 0.78, '["Project Alpha"]'),
            ("behavioral_pattern",
             "Finished a task late but did not consider it done until it met personal quality standard. "
             "Subject explicitly prioritized completeness over meeting external deadline.",
             "message", 0.74, '["Project Beta"]'),
        ]
        for i, (etype, content, stype, conf, entity_names) in enumerate(demo_episodes):
            db.execute(
                "INSERT OR IGNORE INTO episodes "
                "(episode_type, content, source_type, source_ref, confidence, occurred_at, extracted_at, entity_names, active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (etype, content, stype, f"demo-ep-{i:03d}", conf, now_iso, now_iso, entity_names, 1),
            )

        # demo decisions
        demo_decisions = [
            ("Read full documentation before beginning implementation",
             "work", "high", '["cognitive.information_gathering"]',
             "Stated as explicit personal policy, not situational."),
            ("Attempt independent problem-solving before asking for help",
             "work", "high", '["relational.help_seeking", "values.autonomy_drive"]',
             "Described as default mode across all problem types."),
            ("Prioritize output quality over delivery speed when they conflict",
             "work", "high", '["values.work_ethic", "cognitive.decision_speed"]',
             "Consistent across multiple signal events; not just stated preference."),
            ("Accept direct feedback over diplomatically softened delivery",
             "relationships", "high", '["communication.feedback_preference"]',
             "Actively requests unfiltered feedback from collaborators."),
            ("Front-load information gathering before committing to a direction",
             "work", "high", '["cognitive.information_gathering", "temporal.planning_horizon"]',
             "Applied to both technical and non-technical decisions."),
            ("Build trust slowly through demonstrated consistency, not rapport",
             "relationships", "high", '["relational.trust_formation"]',
             "Explicit about this; does not distinguish professional from personal contexts."),
            ("Plan 3+ months out as a baseline for significant work",
             "work", "high", '["temporal.planning_horizon"]',
             "Working backward from future states is the stated default planning mode."),
        ]
        for i, (decision, domain, direction, facet_ids, context) in enumerate(demo_decisions):
            db.execute(
                "INSERT OR IGNORE INTO decisions "
                "(decision, domain, direction, facet_ids, source_type, source_ref, decided_at, extracted_at, context) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (decision, domain, direction, facet_ids, "demo", f"demo-dec-{i:03d}", now_iso, now_iso, context),
            )

        # demo checkin_exchanges
        from datetime import timedelta
        demo_checkins = [
            ("cognitive.decision_speed",
             "When you make a decision under time pressure, what does your internal process look like?",
             "I slow down, actually. The pressure makes me more deliberate, not less - I need to understand the tradeoffs before I can commit. I've gotten faster over the years but I don't think I'll ever be someone who decides by gut alone.",
             4),
            ("relational.help_seeking",
             "How do you typically approach a problem you haven't seen before?",
             "I try to solve it myself first. Not because I don't want help - I just find I learn more by struggling through it. I'll usually hit a real wall before I ask anyone, and by then I have a much clearer question.",
             3),
            ("communication.feedback_preference",
             "When someone gives you feedback on your work, what format is most useful to you?",
             "Direct. Don't soften it. I can handle the actual thing, but I find myself having to decode diplomatically wrapped feedback and that costs energy. Just tell me what's wrong.",
             5),
            ("values.autonomy_drive",
             "How do you feel when you're working on a problem with a lot of external input early in the process?",
             "Honestly, a bit scattered. I do better when I can form my own picture first and then pressure-test it. Too much input before I have a frame just adds noise.",
             4),
            ("temporal.planning_horizon",
             "How far out do you typically plan your work?",
             "Depends on the project, but I usually work with a 3-month horizon at minimum. I like knowing what the end state looks like so I can work backward. Reactive planning stresses me out.",
             3),
            ("emotional.stress_response",
             "What do you do when you feel cognitively overloaded?",
             "I write everything down. Seriously - I get all the active threads out of my head and onto paper. It doesn't solve anything but it frees up enough space to actually think. Then I pick one thing and start.",
             4),
            ("meta_cognition.self_awareness",
             "Is there a pattern in your behavior that you notice but find hard to change?",
             "Avoidance on difficult conversations. I know when I need to have one, I'm usually aware I'm avoiding it, and I still let it drift for longer than I should. It's not fear exactly - more like I keep waiting for the right moment.",
             3),
        ]
        base_time = datetime.now(timezone.utc) - timedelta(days=len(demo_checkins) * 2)
        for i, (facet_id, question, reply, obs_extracted) in enumerate(demo_checkins):
            asked = (base_time + timedelta(days=i * 2)).isoformat()
            replied = (base_time + timedelta(days=i * 2, hours=3)).isoformat()
            db.execute(
                "INSERT INTO checkin_exchanges "
                "(facet_id, question_text, reply_text, reply_captured_at, observations_extracted, asked_at, followup_sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (facet_id, question, reply, replied, obs_extracted, asked, replied),
            )

        # demo biofeedback (7 days of synthetic readings)
        from datetime import date
        bio_signals = [
            ("hrv_rmssd", [58, 62, 55, 70, 66, 61, 64], "ms"),
            ("rhr",        [54, 55, 56, 52, 53, 54, 55], "bpm"),
            ("stress_avg", [28, 31, 35, 22, 26, 30, 27], ""),
            ("sleep_score",[78, 82, 74, 88, 80, 76, 85], "/100"),
            ("sleep_deep_pct", [18, 21, 16, 24, 20, 17, 22], "%"),
            ("sleep_rem_pct",  [22, 25, 20, 28, 24, 21, 26], "%"),
            ("sleep_total_min",[420, 450, 390, 480, 440, 410, 460], "min"),
            ("spo2",       [97, 97, 96, 98, 97, 97, 98], "%"),
        ]
        today = date.today()
        pulled = now_iso
        for signal_type, values, unit in bio_signals:
            for day_offset, value in enumerate(values):
                reading_date = (today - timedelta(days=len(values) - 1 - day_offset)).isoformat()
                db.execute(
                    "INSERT INTO biofeedback_readings (date, signal_type, value, unit, metadata_json, pulled_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (reading_date, signal_type, value, unit, "{}", pulled),
                )

        # demo implicit motives
        db.execute(
            "INSERT INTO implicit_motives (n_ach, n_aff, n_pow, sample_size, evidence, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (0.72, 0.38, 0.29, 180,
             json.dumps([
                 "Frequent references to mastery, completion, and quality thresholds",
                 "Low spontaneous affiliation bids; relational contact is task-adjacent",
                 "Minimal dominance language; influence expressed through expertise rather than authority",
             ]),
             now_iso),
        )

        # demo stress snapshots (3 weeks of weekly snapshots)
        stress_data = [
            (0.61, "medium", "elevated hrv variance + reduced sleep depth",
             "week -2", -3.1, 1.2, 5.4, -4.2),
            (0.44, "low", "stable hrv, good sleep, low self-reported load",
             "week -1", 4.8, -0.9, -2.1, 3.7),
            (0.53, "medium", "mild sleep fragmentation; high task density",
             "week 0 (current)", -1.2, 0.4, 3.0, -1.8),
        ]
        for stress_index, stress_level, dominant_signal, period, hrv_d, rhr_d, stress_d, sleep_d in stress_data:
            db.execute(
                "INSERT INTO stress_snapshots "
                "(stress_index, stress_level, dominant_signal, period, "
                " hrv_delta, rhr_delta, stress_avg_delta, sleep_score_delta, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (stress_index, stress_level, dominant_signal, period,
                 hrv_d, rhr_d, stress_d, sleep_d, now_iso),
            )

        # demo schemas
        schema_data = [
            (
                "Unrelenting Standards",
                "performance",
                0.71, 0.84, 0.78,
                json.dumps([
                    "Resists shipping until confident the implementation is correct",
                    "Describes completed work with qualifiers ('basically done', 'mostly there')",
                    "Spontaneously lists what's still imperfect in finished work",
                ]),
                json.dumps(["code review", "delivery deadlines", "peer evaluation"]),
                json.dumps(["perfectionism", "delayed closure", "over-preparation"]),
            ),
            (
                "Emotional Inhibition",
                "impaired autonomy",
                0.58, 0.70, 0.65,
                json.dumps([
                    "Describes emotional states analytically rather than experientially",
                    "Avoids 'difficult conversations' - acknowledged explicitly in check-in",
                    "Rare use of first-person affect language in transcripts",
                ]),
                json.dumps(["conflict situations", "relational friction", "vulnerability requests"]),
                json.dumps(["delayed confrontation", "intellectualizing", "emotional flattening"]),
            ),
            (
                "Self-Reliance / Autonomy",
                "disconnection",
                0.65, 0.79, 0.72,
                json.dumps([
                    "Strong preference to attempt problems alone before seeking input",
                    "Discomfort with early-stage external input ('too much noise')",
                    "Frames help-seeking as a last resort after genuine effort",
                ]),
                json.dumps(["collaborative projects", "onboarding situations", "ambiguous problems"]),
                json.dumps(["solo first-pass default", "reluctance to delegate", "over-independence"]),
            ),
        ]
        now_ts = now_iso
        first_ts = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        for name, domain, activation, confidence, consensus, evidence, triggers_j, behaviors_j in schema_data:
            db.execute(
                "INSERT INTO schemas "
                "(schema_name, schema_domain, activation_level, confidence, consensus, evidence, "
                " trigger_contexts, behavioral_signatures, first_detected_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, domain, activation, confidence, consensus, evidence,
                 triggers_j, behaviors_j, first_ts, now_ts),
            )

        # demo communication_metrics (platform: demo_chat, various metric types)
        comm_data = [
            ("demo_chat", "self", "week -1", "activity_hours",
             json.dumps({"mon": 1.2, "tue": 2.1, "wed": 0.8, "thu": 1.9, "fri": 1.4, "sat": 0.3, "sun": 0.5}),
             312),
            ("demo_chat", "self", "week -1", "response_latency",
             json.dumps({"median_min": 4.2, "p90_min": 18.7, "p99_min": 62.0}),
             289),
            ("demo_chat", "self", "week -1", "message_length",
             json.dumps({"mean_words": 34.1, "median_words": 22.0, "long_pct": 0.18}),
             312),
        ]
        for platform, chat_id, period, metric_type, metric_data_j, sample_size in comm_data:
            db.execute(
                "INSERT INTO communication_metrics "
                "(platform, chat_id, period, metric_type, metric_data, sample_size, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (platform, chat_id, period, metric_type, metric_data_j, sample_size, now_iso),
            )

        db.commit()
    finally:
        db.close()

    # Write a synthetic jobs.json so the webui cron panel shows demo data
    # without touching ~/.openclaw.  Set OPENCLAW_HOME=<output_dir> when
    # launching the webui against demo data.
    _write_demo_jobs(output_dir)


_DEMO_CRON_JOBS = [
    ("soulkiller:extract",       "0 */2 * * *",    "Extract personality signals from inbox"),
    ("soulkiller:checkin",       "*/30 9-22 * * *", "Select and deliver a check-in question"),
    ("soulkiller:checkin-followup", "on-demand",   "Process a check-in reply"),
    ("soulkiller:passive-scan",  "0 */6 * * *",    "Scan session transcripts for behavioral signal"),
    ("soulkiller:reply-extract", "0 */6 * * *",    "Extract observations from check-in replies"),
    ("soulkiller:synthesize",    "0 3 * * *",      "Consolidate observations into trait scores"),
    ("soulkiller:profile-sync",  "30 3 * * *",     "Sync trait scores to subject_profile.json and PORTRAIT.md"),
    ("soulkiller:entity-extract","0 4 * * *",      "Extract named entities from recent messages"),
    ("soulkiller:decisions",     "15 4 * * *",     "Extract decisions and choices from recent messages"),
    ("soulkiller:healthcheck",   "0 4 * * *",      "Check pipeline health and data freshness"),
    ("soulkiller:memory",        "0 5 * * 0",      "Weekly memory consolidation"),
    ("soulkiller:liwc",          "0 3 * * 0",      "Weekly LIWC-style language analysis"),
    ("soulkiller:stress-index",  "0 6 * * 1",      "Weekly stress index computation"),
    ("soulkiller:schemas",       "0 5 1 * *",      "Monthly: schema and core belief extraction"),
    ("soulkiller:goals",         "0 5 2 * *",      "Monthly: goal and motivation mapping"),
    ("soulkiller:portrait",      "0 5 3 * *",      "Monthly: narrative portrait generation"),
    ("soulkiller:attachment",    "0 5 4 * *",      "Monthly: attachment pattern analysis"),
    ("soulkiller:narrative",     "0 5 5 * *",      "Monthly: life narrative extraction"),
]


def _write_demo_jobs(output_dir: Path) -> None:
    """Write a synthetic cron jobs.json for demo use.

    Point OPENCLAW_HOME at output_dir when launching the webui so the cron
    panel shows these entries instead of any real ~/.openclaw/cron/jobs.json.
    """
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    jobs = []
    for job_id, schedule, description in _DEMO_CRON_JOBS:
        jobs.append({
            "id": job_id,
            "command": f"python3 -m {job_id.replace(':', '.').replace('-', '_')}",
            "schedule": schedule,
            "enabled": True,
            "description": description,
            "state": {
                "lastRunAtMs": now_ms - 3600_000,
                "nextRunAtMs": now_ms + 3600_000,
                "lastStatus": "ok",
            },
        })

    cron_dir = output_dir / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": jobs}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


# ── Main runner ───────────────────────────────────────────────────────────────

def run_demo(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)

    seed = json.loads((DEMO_DIR / "profile.seed.json").read_text(encoding="utf-8"))
    messages_raw = (DEMO_DIR / "inbox.sample.jsonl").read_text(encoding="utf-8")
    messages = [json.loads(line) for line in messages_raw.splitlines() if line.strip()]

    # Simulate extraction pipeline
    observations = _extract_synthetic_observations(messages)

    # Generate documents
    profile_md = _generate_profile_md(seed, observations)
    portrait_md = _generate_portrait_md(seed, observations)

    summary = {
        "subject_name": seed["subject_name"],
        "subject_id": seed["subject_id"],
        "message_count": len(messages),
        "facet_count": len(seed["facets"]),
        "observation_count_seed": sum(f["observation_count"] for f in seed["facets"]),
        "observation_count_demo_pass": len(observations),
        "hypothesis_count": len(seed.get("hypotheses", [])),
        "top_traits": seed["top_traits"],
        "goals": seed["goals"],
        "high_confidence_facets": [
            {"id": f["id"], "position": f["position"], "confidence": f["confidence"]}
            for f in sorted(seed["facets"], key=lambda x: x["confidence"], reverse=True)[:5]
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "demo": True,
    }

    (output_dir / MODEL_PROFILE_FILENAME).write_text(profile_md, encoding="utf-8")
    (output_dir / MODEL_PORTRAIT_FILENAME).write_text(portrait_md, encoding="utf-8")
    (output_dir / SUMMARY_FILENAME).write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / EVENT_LOG_FILENAME).write_text(messages_raw, encoding="utf-8")
    (output_dir / DELIVERY_LOG_FILENAME).write_text(
        json.dumps({
            "channel": "demo",
            "status": "not-sent",
            "note": "Synthetic demo run - no live channel connected.",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_demo_console(output_dir, output_dir / DEMO_CONSOLE_FILENAME)
    _write_demo_db(output_dir, seed, observations)

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the synthetic Soulkiller OSS demo pipeline."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("demo") / "generated",
        help="Directory where demo outputs will be written.",
    )
    args = parser.parse_args()

    summary = run_demo(args.output_dir)
    print(
        f"Demo complete.\n"
        f"  subject:      {summary['subject_name']}\n"
        f"  messages:     {summary['message_count']}\n"
        f"  facets:       {summary['facet_count']}\n"
        f"  observations: {summary['observation_count_seed']} (seed) "
        f"+ {summary['observation_count_demo_pass']} (extraction pass)\n"
        f"  hypotheses:   {summary['hypothesis_count']}\n"
        f"  output dir:   {args.output_dir}"
    )


if __name__ == "__main__":
    main()
