#!/usr/bin/env python3
"""Soulkiller Profile Bridge — syncs trait model to subject_profile.json + PROFILE.md.

Cron: soulkiller:profile-sync, daily at 03:30 (after synthesis)

Maintains backward compatibility with existing operational protocols by mapping
Soulkiller facets to profile categories and preserving all non-soulkiller records.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn, error

SCRIPT = "soulkiller_profile_bridge"

import os as _os
def _data_dir() -> Path:
    env = _os.environ.get("SOULKILLER_DATA_DIR", "")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "runtime"

PROFILE_PATH = _data_dir() / "subject_profile.json"
PROFILE_MD_PATH = _data_dir() / "PROFILE.md"

# Soulkiller category → profile categoria mapping
CATEGORY_MAP: dict[str, list[str]] = {
    "cognitive": ["decisioni", "preferenze_stile"],
    "emotional": ["vincoli", "conoscenze_assimilate"],
    "communication": ["preferenze_stile"],
    "relational": ["conoscenze_assimilate", "valori"],
    "values": ["valori"],
    "temporal": ["abitudine", "vincoli"],
    "aesthetic": ["preferenze_stile"],
    "meta_cognition": ["conoscenze_assimilate"],
}

# Category display names (Italian)
CATEGORY_DISPLAY: dict[str, str] = {
    "cognitive": "Stile Cognitivo",
    "emotional": "Pattern Emotivi",
    "communication": "Stile Comunicativo",
    "relational": "Dinamiche Relazionali",
    "values": "Valori",
    "temporal": "Pattern Temporali",
    "aesthetic": "Preferenze Estetiche",
    "meta_cognition": "Meta-cognizione",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_position(value: float | None, low: str | None, high: str | None) -> str:
    """Format a spectrum position as human-readable text."""
    if value is None or low is None or high is None:
        return ""
    if value < 0.3:
        return f"Tendenza verso: {low}"
    elif value > 0.7:
        return f"Tendenza verso: {high}"
    elif value < 0.45:
        return f"Moderatamente verso: {low}"
    elif value > 0.55:
        return f"Moderatamente verso: {high}"
    else:
        return f"Bilanciato tra {low} e {high}"


def sync_profile() -> dict[str, Any]:
    """Sync Soulkiller traits to subject_profile.json."""
    from soulkiller_db import get_db, get_all_traits, NON_LINEAR_FACETS

    profile = load_json(PROFILE_PATH)
    records: list[dict[str, Any]] = profile.get("records", [])
    traits = get_all_traits()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Index existing soulkiller records by fonte
    sk_records: dict[str, int] = {}  # fonte -> index in records
    for i, rec in enumerate(records):
        fonte = rec.get("fonte", "")
        if fonte.startswith("soulkiller:"):
            sk_records[fonte] = i

    # Find next ID sequence
    existing_ids = [r.get("id", "") for r in records if r.get("id", "").startswith("mem-")]
    max_seq = 0
    for rid in existing_ids:
        parts = rid.split("-")
        if len(parts) >= 3:
            try:
                max_seq = max(max_seq, int(parts[-1]))
            except ValueError:
                pass
    next_seq = max_seq + 1

    added = 0
    updated = 0
    verified = 0

    for trait in traits:
        facet_id = trait["facet_id"]
        confidence = float(trait.get("confidence", 0) or 0)
        fonte = f"soulkiller:{facet_id}"
        category = trait.get("category", "")
        profile_categories = CATEGORY_MAP.get(category, ["conoscenze_assimilate"])
        profile_cat = profile_categories[0]  # Use primary category

        if confidence >= 0.3:
            # Build contenuto
            if facet_id in NON_LINEAR_FACETS:
                contenuto = f"{trait.get('description', facet_id)}: {trait.get('notes', 'dati insufficienti')}"
            else:
                pos_text = format_position(
                    trait.get("value_position"),
                    trait.get("spectrum_low"),
                    trait.get("spectrum_high"),
                )
                notes = trait.get("notes") or ""
                contenuto = f"{trait.get('description', facet_id)}. {pos_text}"
                if notes:
                    contenuto += f" — {notes[:150]}"

            if fonte in sk_records:
                # Update existing
                idx = sk_records[fonte]
                records[idx]["contenuto"] = contenuto
                records[idx]["confidenza"] = round(confidence, 2)
                records[idx]["stato"] = "attivo"
                records[idx]["ultimo_aggiornamento"] = today
                records[idx]["soulkiller_facet_id"] = facet_id
                updated += 1
            else:
                # Create new record
                new_id = f"mem-{today.replace('-', '')}-{next_seq:03d}"
                next_seq += 1
                records.append({
                    "id": new_id,
                    "categoria": profile_cat,
                    "fonte": fonte,
                    "confidenza": round(confidence, 2),
                    "stato": "attivo",
                    "priority_rank": 3,
                    "supersedes": None,
                    "contenuto": contenuto,
                    "ultimo_aggiornamento": today,
                    "sensitivity": trait.get("sensitivity", "media"),
                    "retention_days": 365,
                    "soulkiller_facet_id": facet_id,
                })
                added += 1
        else:
            # Low confidence — mark existing record as da_verificare
            if fonte in sk_records:
                idx = sk_records[fonte]
                if records[idx].get("stato") == "attivo":
                    records[idx]["stato"] = "da_verificare"
                    records[idx]["ultimo_aggiornamento"] = today
                    verified += 1

    # Update profile
    profile["records"] = records
    profile["schema_version"] = "1.2"
    save_json(PROFILE_PATH, profile)

    summary = {"added": added, "updated": updated, "marked_da_verificare": verified}
    info(SCRIPT, "profile_synced", **summary)
    return summary


def generate_profile_md() -> dict[str, Any]:
    """Generate a human-readable PROFILE.md snapshot."""
    from soulkiller_db import get_db, get_all_traits, get_hypotheses, get_model_summary, get_context_clusters, NON_LINEAR_FACETS

    traits = get_all_traits()
    hypotheses = get_hypotheses()
    summary = get_model_summary()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    covered = summary.get("covered_facets", 0)
    total = summary.get("total_facets", 46)
    avg_conf = summary.get("avg_confidence", 0)
    coverage_pct = summary.get("coverage_pct", 0)

    # Count trait statuses
    status_counts: dict[str, int] = {}
    for t in traits:
        s = t.get("status", "insufficient_data")
        status_counts[s] = status_counts.get(s, 0) + 1

    lines: list[str] = [
        f"# Personality Model — the subject",
        f"Updated: {today} | Coverage: {covered}/{total} facets ({coverage_pct:.0f}%) | Avg confidence: {avg_conf:.2f}",
        "",
    ]

    # Status summary
    active_n = status_counts.get("active", 0)
    insuff_n = status_counts.get("insufficient_data", 0)
    unreliable_n = status_counts.get("unreliable", 0)
    stalled_n = status_counts.get("stalled", 0)
    overfit_n = status_counts.get("overfitting", 0)
    status_parts = [f"{active_n} active"]
    if insuff_n:
        status_parts.append(f"{insuff_n} insufficient data")
    if unreliable_n:
        status_parts.append(f"{unreliable_n} unreliable")
    if stalled_n:
        status_parts.append(f"{stalled_n} stalled")
    if overfit_n:
        status_parts.append(f"{overfit_n} overfitting")
    lines.append(f"Status: {' | '.join(status_parts)}")
    lines.append("")

    # Group traits by category
    by_category: dict[str, list[dict[str, Any]]] = {}
    for trait in traits:
        cat = trait.get("category", "unknown")
        by_category.setdefault(cat, []).append(trait)

    for cat in ["cognitive", "emotional", "communication", "relational",
                "values", "temporal", "aesthetic", "meta_cognition"]:
        cat_traits = by_category.get(cat, [])
        if not cat_traits:
            continue

        display_name = CATEGORY_DISPLAY.get(cat, cat.title())
        lines.append(f"## {display_name}")
        lines.append("")

        for t in sorted(cat_traits, key=lambda x: x.get("confidence", 0), reverse=True):
            facet_id = t["facet_id"]
            confidence = float(t.get("confidence", 0) or 0)
            obs_count = int(t.get("observation_count", 0) or 0)
            name = t.get("name", facet_id.split(".")[-1]).replace("_", " ").title()
            trait_status = t.get("status", "insufficient_data")

            if confidence <= 0:
                lines.append(f"- **{name}**: _Dati insufficienti_")
                continue

            # Status warning prefix
            status_prefix = ""
            if trait_status == "unreliable":
                status_prefix = " [UNRELIABLE]"
            elif trait_status == "stalled":
                status_prefix = " [STALLED]"
            elif trait_status == "overfitting":
                status_prefix = " [OVERFITTING]"

            if facet_id in NON_LINEAR_FACETS:
                notes = t.get("notes") or "in raccolta"
                lines.append(f"- **{name}**{status_prefix}: {notes} (conf: {confidence:.2f}, {obs_count} obs)")
            else:
                pos = t.get("value_position")
                pos_text = format_position(pos, t.get("spectrum_low"), t.get("spectrum_high"))
                if pos_text:
                    lines.append(f"- **{name}**{status_prefix}: {pos_text} (conf: {confidence:.2f}, {obs_count} obs)")
                else:
                    lines.append(f"- **{name}**{status_prefix}: _In analisi_ (conf: {confidence:.2f}, {obs_count} obs)")

            # Status detail line
            if trait_status == "unreliable":
                lines.append(f"  Segnali contraddittori — questa facetta potrebbe non essere rilevabile con i dati attuali.")
            elif trait_status == "stalled":
                lines.append(f"  Bloccata — nessuna nuova osservazione da 30+ giorni, confidenza bassa.")
            elif trait_status == "overfitting":
                lines.append(f"  Rischio overfitting — alta confidenza ma bassa diversita' nelle fonti.")

            # Show contextual clusters if available
            if facet_id not in NON_LINEAR_FACETS:
                clusters = get_context_clusters(facet_id)
                if clusters:
                    for cl in clusters:
                        cl_label = cl["cluster_label"]
                        cl_pos = format_position(cl["value_position"], t.get("spectrum_low"), t.get("spectrum_high"))
                        cl_conf = cl["confidence"]
                        cl_obs = cl["observation_count"]
                        lines.append(f"  - Con {cl_label}: {cl_pos} (conf: {cl_conf:.2f}, {cl_obs} obs)")

        lines.append("")

    # Hypotheses section
    active_hyps = [h for h in hypotheses if h.get("status") in ("unverified", "confirmed", "nuanced")]
    if active_hyps:
        lines.append("## Hypotheses")
        lines.append("")
        for h in active_hyps[:10]:
            status = h["status"]
            conf = float(h.get("confidence", 0))
            marker = {"confirmed": "confirmed", "nuanced": "nuanced", "unverified": "unverified"}.get(status, status)
            lines.append(f'- "{h["hypothesis"]}" ({marker}, conf: {conf:.2f})')
        lines.append("")

    PROFILE_MD_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_MD_PATH.write_text("\n".join(lines), encoding="utf-8")

    stats = {"path": str(PROFILE_MD_PATH), "covered": covered, "total": total}
    info(SCRIPT, "profile_md_generated", **stats)
    return stats


def main() -> int:
    # Step 1: Sync to subject_profile.json
    sync_summary = sync_profile()

    # Step 2: Generate PROFILE.md
    md_stats = generate_profile_md()

    print(json.dumps({
        "profile_sync": sync_summary,
        "profile_md": md_stats,
    }, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
