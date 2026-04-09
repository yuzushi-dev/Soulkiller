#!/usr/bin/env python3
"""Soulkiller Synthesizer — daily consolidation of observations into trait scores.

Cron: soulkiller:synthesize, daily at 03:00 Europe/Rome

1. For each facet with new observations since last synthesis:
   - Aggregates signals (weighted by strength and recency)
   - Computes value_position (weighted average)
   - Updates confidence based on count + consistency
2. Updates hypotheses via LLM (max 1 call/day)
3. Saves a model snapshot
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import stdev
from typing import Any

from lib.config import get_config, load_nanobot_config
from lib.log import info, warn, error

SCRIPT = "soulkiller_synthesizer"
LLM_TIMEOUT_SECONDS = 120  # Synthesis is a heavier call
DEFAULT_MODEL = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"


def _call_llm_direct(prompt: str, model: str) -> dict[str, Any]:
    """Call LLM API directly using NanoBot provider config."""
    from lib.llm_resilience import chat_completion_content

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": "You are a personality analysis expert. Return STRICT JSON only. No reasoning, just JSON."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=2048,
        temperature=0.2,
        timeout=LLM_TIMEOUT_SECONDS,
        title="Soulkiller Synthesizer",
    )
    return _parse_llm_json(content)


def _fix_json(s: str) -> str:
    """Fix common glm4.7 JSON issues: missing commas between items."""
    s = re.sub(r'(\})\s*\n(\s*\{)', r'\1,\n\2', s)
    s = re.sub(r'(\])\s*\n(\s*\{)', r'\1,\n\2', s)
    s = re.sub(r'(\})\s*\n(\s*\[)', r'\1,\n\2', s)
    s = re.sub(r'(")\s*\n(\s*")', r'\1,\n\2', s)
    return s


def _parse_llm_json(content: str) -> dict[str, Any]:
    """Robust JSON parser: handles markdown fences, missing commas, truncation."""
    s = content.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        s = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3].strip()

    start = s.find("{")
    if start == -1:
        raise RuntimeError(f"No JSON in response: {content[:150]}")

    end = s.rfind("}")
    candidates = []
    if end > start:
        candidates.append(s[start:end + 1])
        candidates.append(_fix_json(s[start:end + 1]))

    # Truncation recovery
    for i in range(len(s) - 1, start, -1):
        if s[i] == '}':
            attempt = _fix_json(s[start:i + 1])
            for suffix in [']}', ']}}', '}}']:
                try:
                    return json.loads(attempt + suffix)
                except json.JSONDecodeError:
                    pass
            candidates.append(attempt)
            break

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise RuntimeError(f"Unparseable JSON: {content[:200]}")


def compute_confidence(observations: list[dict[str, Any]]) -> float:
    """Confidence formula: min(1.0, base_count_conf * consistency_factor)
    - base_count_conf = 1 - e^(-obs_count / 8)  (plateaus around 15 observations)
    - consistency_factor = 1 - stdev(signal_values)  (penalizes contradictions)
    """
    n = len(observations)
    if n == 0:
        return 0.0

    base_count_conf = 1.0 - math.exp(-n / 8.0)

    positions = [o["signal_position"] for o in observations
                 if o.get("signal_position") is not None]
    if len(positions) >= 2:
        sd = stdev(positions)
        consistency_factor = max(0.1, 1.0 - sd)
    elif len(positions) == 1:
        consistency_factor = 0.8  # Single observation, decent confidence
    else:
        consistency_factor = 0.6  # Non-linear facet, moderate confidence from count alone

    return min(1.0, base_count_conf * consistency_factor)


def compute_value_position(observations: list[dict[str, Any]],
                           half_life_days: int = 14) -> float | None:
    """Weighted average of signal positions, weighted by strength and recency.

    IMP-01: half_life_days is now per-facet (default 14 for backwards compat).
    """
    valid = [(o["signal_position"], o.get("signal_strength", 0.5), o.get("created_at", ""))
             for o in observations if o.get("signal_position") is not None]
    if not valid:
        return None

    now = datetime.now(timezone.utc)
    weighted_sum = 0.0
    weight_total = 0.0
    hl = max(1, half_life_days)  # guard against 0

    for position, strength, created_at in valid:
        try:
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            days_old = max(0, (now - created).total_seconds() / 86400.0)
        except (ValueError, TypeError):
            days_old = 30.0
        recency_weight = math.exp(-0.693 * days_old / hl)

        weight = strength * recency_weight
        weighted_sum += position * weight
        weight_total += weight

    if weight_total <= 0:
        return None
    return max(0.0, min(1.0, weighted_sum / weight_total))


def synthesize_non_linear_facet(facet_id: str, observations: list[dict[str, Any]]) -> str:
    """For non-linear facets, accumulate textual evidence instead of numeric average."""
    signals = [o.get("extracted_signal", "") for o in observations if o.get("extracted_signal")]
    if not signals:
        return ""

    # Count recurring labels/themes
    label_counts: dict[str, int] = {}
    for signal in signals:
        # Simple tokenization of the signal
        for token in signal.lower().split(","):
            token = token.strip()
            if len(token) > 2:
                label_counts[token] = label_counts.get(token, 0) + 1

    # Sort by frequency, take top entries
    sorted_labels = sorted(label_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    parts = []
    for label, count in sorted_labels:
        parts.append(f"{label} ({count}x)" if count > 1 else label)
    return "; ".join(parts)


def compute_trait_status(
    observation_count: int,
    confidence: float,
    observations: list[dict[str, Any]],
) -> str:
    """Compute explicit trait status from observations.

    Statuses:
      - insufficient_data: fewer than 5 observations
      - stalled: >= 10 obs, low confidence, no new data in 30 days
      - unreliable: >= 20 obs, low confidence, high variance
      - overfitting: >= 50 obs, very high confidence, low source_ref diversity
      - active: normal functioning
    """
    if observation_count < 5:
        return "insufficient_data"

    positions = [o["signal_position"] for o in observations
                 if o.get("signal_position") is not None]
    stdev_val = stdev(positions) if len(positions) >= 2 else 0.0

    # Check recency: days since most recent observation
    now = datetime.now(timezone.utc)
    last_dates = []
    for o in observations:
        try:
            last_dates.append(datetime.fromisoformat(
                o["created_at"].replace("Z", "+00:00")))
        except (ValueError, TypeError, KeyError):
            pass
    if last_dates:
        days_since_last = (now - max(last_dates)).total_seconds() / 86400.0
    else:
        days_since_last = 999.0

    # Check source_ref diversity (not just source_type — dump_import has many refs)
    source_refs = [o.get("source_ref", "") for o in observations]
    unique_refs = len(set(source_refs))

    # Overfitting: high confidence but all from essentially the same source
    # Use ratio: if < 20% of observations have distinct refs, it's suspicious
    if observation_count >= 50 and confidence > 0.95:
        ref_diversity = unique_refs / observation_count if observation_count else 1.0
        if ref_diversity < 0.2:
            return "overfitting"

    # Unreliable: many observations but contradictory signals
    if observation_count >= 20 and confidence < 0.3 and stdev_val > 0.4:
        return "unreliable"

    # Stalled: moderate data but low confidence and no recent observations
    if observation_count >= 10 and confidence < 0.3 and days_since_last > 30:
        return "stalled"

    return "active"


MIN_CLUSTER_OBS = 5  # Minimum observations to form a cluster
MIN_FACET_OBS_FOR_CLUSTERING = 20  # Only cluster facets with enough data


def compute_context_clusters(facet_id: str, observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group observations by interlocutor_type and compute per-cluster value_position/confidence.

    Only creates clusters when there are >= MIN_CLUSTER_OBS observations in a group.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for obs in observations:
        ctx_raw = obs.get("context_metadata")
        if not ctx_raw:
            continue
        try:
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else ctx_raw
        except (json.JSONDecodeError, TypeError):
            continue

        key = ctx.get("interlocutor_type", "unknown")
        if key == "unknown":
            continue
        groups.setdefault(key, []).append(obs)

    total_with_ctx = sum(len(v) for v in groups.values())
    if total_with_ctx == 0:
        return []

    clusters = []
    for label, obs_group in groups.items():
        if len(obs_group) < MIN_CLUSTER_OBS:
            continue

        positions = [o["signal_position"] for o in obs_group
                     if o.get("signal_position") is not None]
        if not positions:
            continue

        cluster_conf = compute_confidence(obs_group)
        cluster_pos = compute_value_position(obs_group)
        if cluster_pos is None:
            continue

        clusters.append({
            "cluster_label": label,
            "context_filter": {"interlocutor_type": label},
            "value_position": cluster_pos,
            "confidence": cluster_conf,
            "observation_count": len(obs_group),
            "weight": len(obs_group) / total_with_ctx,
        })

    return clusters


def synthesize_traits() -> dict[str, Any]:
    """Main synthesis: update all traits from observations."""
    from soulkiller_db import (
        get_db, get_all_facets, get_trait, get_observations_for_facet,
        update_trait, upsert_context_cluster, delete_context_clusters,
        NON_LINEAR_FACETS,
    )

    conn = get_db()

    # Apply pending subject corrections before synthesis (IMP-02)
    try:
        from soulkiller_db import apply_pending_corrections
        corrections_applied = apply_pending_corrections(conn)
        if corrections_applied:
            conn.commit()
            info(SCRIPT, "corrections_applied", count=corrections_applied)
    except Exception as e:
        warn(SCRIPT, "corrections_skipped", error=str(e))

    # IMP-14: check if loop_warning hypothesis is active this week
    # If so, halve signal_strength for session_behavioral observations
    cutoff_7d = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    loop_warning_active = conn.execute(
        "SELECT 1 FROM hypotheses WHERE hypothesis LIKE '[loop_warning]%' AND created_at >= ? LIMIT 1",
        (cutoff_7d,),
    ).fetchone() is not None
    if loop_warning_active:
        info(SCRIPT, "loop_warning_active_weight_halved")

    facets = get_all_facets(conn)
    updated_facets: list[str] = []
    status_counts: dict[str, int] = Counter()
    clustered_facets = 0
    summary: dict[str, Any] = {"facets_updated": 0, "facets_skipped": 0}

    try:
        for facet in facets:
            facet_id = facet["id"]
            trait = get_trait(facet_id, conn)
            last_synthesis = trait.get("last_synthesis_at") if trait else None

            # Get observations (all for first synthesis, or since last synthesis)
            observations = get_observations_for_facet(facet_id, since=None, conn=conn)
            # IMP-14: halve signal_strength for session_behavioral observations when loop_warning is active
            if loop_warning_active:
                observations = [
                    dict(o, signal_strength=o.get("signal_strength", 0.5) * 0.5)
                    if o.get("source_type") == "session_behavioral" else o
                    for o in observations
                ]
            if not observations:
                summary["facets_skipped"] += 1
                continue

            # Check if there are new observations since last synthesis
            if last_synthesis:
                new_obs = [o for o in observations if o["created_at"] > last_synthesis]
                if not new_obs:
                    summary["facets_skipped"] += 1
                    continue

            # Compute trait values
            confidence = compute_confidence(observations)
            obs_count = len(observations)

            # Compute trait status
            trait_status = compute_trait_status(obs_count, confidence, observations)
            status_counts[trait_status] += 1

            half_life = int(facet.get("half_life_days") or 14)

            if facet_id in NON_LINEAR_FACETS:
                notes = synthesize_non_linear_facet(facet_id, observations)
                update_trait(facet_id, value_position=None, confidence=confidence,
                             notes=notes, status=trait_status, conn=conn)
            else:
                value_position = compute_value_position(observations, half_life_days=half_life)
                # Generate concise notes
                strengths = [(o.get("extracted_signal") or "")[:100] for o in observations[:5]]
                notes = " | ".join(s for s in strengths if s)
                update_trait(facet_id, value_position=value_position, confidence=confidence,
                             notes=notes, status=trait_status, conn=conn)

            # Contextual clustering (only for linear facets with enough data)
            if facet_id not in NON_LINEAR_FACETS and obs_count >= MIN_FACET_OBS_FOR_CLUSTERING:
                clusters = compute_context_clusters(facet_id, observations)
                if clusters:
                    delete_context_clusters(facet_id, conn=conn)
                    for cl in clusters:
                        upsert_context_cluster(
                            facet_id=facet_id,
                            cluster_label=cl["cluster_label"],
                            context_filter=cl["context_filter"],
                            value_position=cl["value_position"],
                            confidence=cl["confidence"],
                            observation_count=cl["observation_count"],
                            weight=cl["weight"],
                            conn=conn,
                        )
                    clustered_facets += 1

            updated_facets.append(facet_id)
            summary["facets_updated"] += 1

        conn.commit()
    finally:
        conn.close()

    summary["updated_facets"] = updated_facets
    summary["status_counts"] = dict(status_counts)
    summary["clustered_facets"] = clustered_facets
    return summary


_DOMAIN_KEYWORDS: dict[str, list[str]] = {
    "tech":          ["python", "code", "bug", "server", "api", "deploy", "git",
                      "docker", "script", "database", "cron", "error", "debug",
                      "function", "module", "build", "test", "linux", "openclaw"],
    "emotional":     ["sento", "triste", "felice", "ansia", "paura", "stanco",
                      "frustrato", "arrabbiato", "contento", "preoccupato",
                      "stress", "umore", "emozione", "feel", "sad", "happy"],
    "relational":    ["amico", "amica", "partner", "famiglia",
                      "relazione", "insieme", "incontro", "litigato", "parla"],
    "planning":      ["piano", "progetto", "domani", "prossima settimana",
                      "obiettivo", "scadenza", "agenda", "calendario", "mese"],
    "aesthetic":     ["musica", "film", "libro", "cibo", "ristorante", "arte",
                      "design", "bello", "preferisco", "mi piace", "gusto"],
    "personal_life": ["casa", "sport", "vacanza", "dormito", "mangiato",
                      "salute", "medico", "viaggio", "weekend", "abitudine"],
}


def _classify_domain(text: str) -> str:
    t = text.lower()
    scores: dict[str, int] = {}
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        scores[domain] = sum(1 for kw in keywords if kw in t)
    best = max(scores, key=lambda k: scores[k])
    return best if scores[best] > 0 else "other"


def compute_domain_coverage() -> dict[str, Any]:
    """Compute 30-day observation domain distribution and write imbalance hypothesis
    if any single domain > 65% (IMP-19). Returns distribution dict.
    """
    from soulkiller_db import get_db, upsert_hypothesis

    conn = get_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rows = conn.execute(
            """SELECT content, conversation_domain FROM observations
               WHERE created_at >= ?""",
            (cutoff,),
        ).fetchall()

        if not rows:
            return {}

        domain_counts: dict[str, int] = Counter()
        updates: list[tuple] = []
        for row in rows:
            domain = row["conversation_domain"] or _classify_domain(row["content"])
            domain_counts[domain] += 1
            if not row["conversation_domain"]:
                updates.append((domain, row["content"]))  # store for future

        total = sum(domain_counts.values())
        distribution = {d: round(c / total, 3) for d, c in domain_counts.most_common()}

        # Check imbalance
        dominant = domain_counts.most_common(1)[0]
        if dominant[1] / total > 0.65:
            upsert_hypothesis(
                hypothesis=(
                    f"[domain_imbalance] '{dominant[0]}' accounts for "
                    f"{dominant[1]/total:.0%} of the last 30 days of observations. "
                    "Trait estimates from under-represented domains may be unreliable. "
                    "Consider running the domain prober for the weakest domains."
                ),
                confidence=min(1.0, dominant[1] / total),
                conn=conn,
            )

        conn.commit()
        return distribution
    finally:
        conn.close()


def detect_and_record_drift() -> int:
    """Compare current trait positions to the 30-day-old snapshot and write drift_alert
    hypotheses for facets with |delta| > 0.15. Returns number of alerts written (IMP-04).
    """
    from soulkiller_db import get_db, upsert_hypothesis

    conn = get_db()
    try:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=35)).isoformat()
        window = (now - timedelta(days=25)).isoformat()

        # Find snapshot closest to 30 days ago
        row = conn.execute(
            """SELECT snapshot_data FROM model_snapshots
               WHERE snapshot_at BETWEEN ? AND ?
               ORDER BY snapshot_at DESC LIMIT 1""",
            (cutoff, window),
        ).fetchone()
        if not row:
            return 0

        try:
            old_traits = {t["facet_id"]: t.get("value_position")
                          for t in json.loads(row["snapshot_data"])
                          if t.get("value_position") is not None}
        except (json.JSONDecodeError, KeyError):
            return 0

        current = conn.execute(
            "SELECT facet_id, value_position FROM traits WHERE value_position IS NOT NULL"
        ).fetchall()

        alerts = 0
        for row in current:
            fid = row["facet_id"]
            new_pos = row["value_position"]
            old_pos = old_traits.get(fid)
            if old_pos is None:
                continue
            delta = new_pos - old_pos
            if abs(delta) < 0.15:
                continue
            direction = "increasing" if delta > 0 else "decreasing"
            upsert_hypothesis(
                hypothesis=(
                    f"[drift_alert] {fid} shifted {direction} by {abs(delta):.2f} "
                    f"over the last 30 days ({old_pos:.2f} → {new_pos:.2f})"
                ),
                confidence=min(1.0, abs(delta) / 0.5),
                conn=conn,
            )
            alerts += 1

        conn.commit()
        return alerts
    finally:
        conn.close()


def update_hypotheses(model: str = DEFAULT_MODEL) -> int:
    """LLM-based hypothesis generation/update. Max 1 call/day."""
    from soulkiller_db import (
        get_db, get_all_traits, get_hypotheses, upsert_hypothesis,
        get_observations_for_facet,
    )

    conn = get_db()
    try:
        traits = get_all_traits(conn)
        existing = get_hypotheses(conn=conn)

        # Find top 12 traits by observation count (most data = best for patterns)
        top_traits = sorted(traits, key=lambda t: t.get("observation_count", 0), reverse=True)[:12]
        top_with_data = [t for t in top_traits if t.get("observation_count", 0) > 0]

        if not top_with_data:
            return 0

        # Build trait summaries for the prompt
        trait_summaries = []
        for t in top_with_data:
            trait_summaries.append({
                "facet_id": t["facet_id"],
                "value_position": t.get("value_position"),
                "confidence": round(t.get("confidence", 0), 2),
                "obs": t.get("observation_count", 0),
                "spectrum": f"{t.get('spectrum_low', '')} ↔ {t.get('spectrum_high', '')}",
                "notes": (t.get("notes") or "")[:150],
            })

        # Load entities (people + projects with most mentions)
        entity_rows = conn.execute(
            """SELECT entity_type, name, label, mention_count
               FROM entities
               WHERE entity_type IN ('person','project')
               ORDER BY mention_count DESC LIMIT 12"""
        ).fetchall()
        entity_ctx = [
            {"type": r["entity_type"], "name": r["name"],
             "role": r["label"], "mentions": r["mention_count"]}
            for r in entity_rows
        ]

        # Load most significant episodes (high confidence, recent)
        episode_rows = conn.execute(
            """SELECT episode_type, content, occurred_at, confidence
               FROM episodes
               WHERE active=1 AND confidence >= 0.65
               ORDER BY occurred_at DESC NULLS LAST LIMIT 12"""
        ).fetchall()
        episode_ctx = [
            {"type": r["episode_type"], "when": r["occurred_at"],
             "content": r["content"][:160], "conf": r["confidence"]}
            for r in episode_rows
        ]

        # Build existing hypotheses for review
        existing_summaries = [
            {"id": h["id"], "hypothesis": h["hypothesis"],
             "status": h["status"], "confidence": h["confidence"]}
            for h in existing[:10]
        ]

        entity_block = json.dumps(entity_ctx, ensure_ascii=False, indent=2) if entity_ctx else "none"
        episode_block = json.dumps(episode_ctx, ensure_ascii=False, indent=2) if episode_ctx else "none"

        prompt = f"""You are analyzing the personality of a real person (the subject, Italian, 30s, tech worker).
Given his trait scores, key people/projects in his life, and significant life episodes,
identify 2-4 cross-facet behavioral hypotheses — patterns that explain *how* and *why* he acts.

Good hypotheses connect multiple dimensions: a trait + a context + a person/episode.
Example: "Under technical stress the subject becomes more impulsive, contradicting his baseline systematic style."

For each NEW hypothesis:
{{"hypothesis": "...", "supporting_facets": ["facet.id"], "confidence": 0.0-1.0}}

For EXISTING hypothesis updates:
{{"id": N, "new_status": "confirmed|denied|nuanced", "new_confidence": 0.0-1.0, "reason": "..."}}

Return STRICT JSON:
{{
  "new_hypotheses": [...],
  "updated_hypotheses": [...]
}}

TRAIT SCORES (top by observation count):
{json.dumps(trait_summaries, ensure_ascii=False, indent=2)}

KEY PEOPLE & PROJECTS:
{entity_block}

SIGNIFICANT EPISODES (recent):
{episode_block}

EXISTING HYPOTHESES TO REVIEW:
{json.dumps(existing_summaries, ensure_ascii=False, indent=2)}"""

        result = _call_llm_direct(prompt, model)

        count = 0

        # Process new hypotheses
        for h in result.get("new_hypotheses", []):
            hypothesis_text = h.get("hypothesis", "")
            if not hypothesis_text:
                continue
            upsert_hypothesis(
                hypothesis=hypothesis_text,
                confidence=float(h.get("confidence", 0.3)),
                conn=conn,
            )
            count += 1

        # Process updates to existing hypotheses
        for u in result.get("updated_hypotheses", []):
            h_id = u.get("id")
            if not h_id:
                continue
            new_status = u.get("new_status", "")
            new_conf = u.get("new_confidence")
            if new_status:
                upsert_hypothesis(
                    hypothesis=u.get("reason", ""),
                    status=new_status,
                    confidence=float(new_conf) if new_conf is not None else 0.3,
                    hypothesis_id=int(h_id),
                    conn=conn,
                )
                count += 1

        conn.commit()
        return count
    except Exception as e:
        error(SCRIPT, "hypothesis_update_failed", error=str(e))
        return 0
    finally:
        conn.close()


def main() -> int:
    import argparse
    from soulkiller_db import save_snapshot

    parser = argparse.ArgumentParser(description='Soulkiller Synthesizer')
    parser.add_argument('--model', type=str, default=DEFAULT_MODEL,
                        help='LLM model for hypothesis generation (e.g., openrouter/meta-llama/llama-3.3-70b-instruct:free)')
    args = parser.parse_args()

    # Step 1: Synthesize traits
    info(SCRIPT, "synthesis_start")
    trait_summary = synthesize_traits()
    info(SCRIPT, "traits_synthesized", **{k: v for k, v in trait_summary.items() if k != "updated_facets"})

    # Step 2: Domain coverage (IMP-19)
    domain_dist = compute_domain_coverage()
    if domain_dist:
        info(SCRIPT, "domain_coverage", distribution=domain_dist)

    # Step 3: Drift detection (IMP-04)
    drift_alerts = detect_and_record_drift()
    if drift_alerts:
        info(SCRIPT, "drift_alerts_written", count=drift_alerts)

    # Step 4: Update hypotheses (only if we have data)
    if trait_summary["facets_updated"] > 0 or True:  # always run to catch first-time
        hyp_count = update_hypotheses(model=args.model)
        info(SCRIPT, "hypotheses_updated", count=hyp_count)

    # Step 5: Save snapshot
    snapshot = save_snapshot()
    info(SCRIPT, "synthesis_complete", **snapshot)

    # Print summary for cron logging
    print(json.dumps({
        "traits": trait_summary.get("facets_updated", 0),
        "coverage": snapshot.get("coverage_pct", 0),
        "avg_confidence": snapshot.get("avg_confidence", 0),
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
