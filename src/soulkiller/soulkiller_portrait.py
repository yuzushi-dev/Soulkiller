#!/usr/bin/env python3
"""Soulkiller Portrait Synthesizer - Livello 4

Monthly synthesis of all soulkiller layers into a coherent
narrative portrait of the subject, stored as PORTRAIT.md.

Integrates:
  - 46 personality facets (traits)
  - Hypotheses (behavioral patterns)
  - Entities (people, projects, places)
  - Episodes (significant events)
  - Communication metrics (behavioral signals)
  - High-confidence check-in reply observations

Output: soulkiller/PORTRAIT.md - injected into relational agent sessions via the bootstrap hook.

Cron: soulkiller:portrait, monthly (1st day, 06:00 Europe/Rome)

Usage:
  python3 soulkiller_portrait.py [--model ...] [--force] [--dry-run]
"""

from __future__ import annotations
import os

import json
import http.client
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from lib.config import load_nanobot_config
from lib.log import info, warn

SCRIPT = "soulkiller_portrait"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
PORTRAIT_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "PORTRAIT.md"
DEFAULT_MODEL = "openrouter/meta-llama/llama-3.3-70b-instruct:free"
LLM_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def load_portrait_data(db) -> dict:
    """Load all relevant soulkiller data for portrait synthesis."""

    # Top traits (confidence > 0.5), both poles represented
    traits = db.execute("""
        SELECT f.id, f.name, f.category, t.value_position, t.confidence,
               f.spectrum_low, f.spectrum_high
        FROM traits t JOIN facets f ON t.facet_id=f.id
        WHERE t.confidence > 0.50
        ORDER BY t.confidence DESC
        LIMIT 25
    """).fetchall()

    # Hypotheses
    hypotheses = db.execute("""
        SELECT hypothesis, confidence
        FROM hypotheses
        ORDER BY confidence DESC
    """).fetchall()

    # Entities
    entities = db.execute("""
        SELECT e.name, e.entity_type, e.label, e.mention_count, e.description,
               er.relation_type, er.dynamic, er.sentiment
        FROM entities e
        LEFT JOIN entity_relations er ON er.entity_id=e.id
        ORDER BY e.mention_count DESC
        LIMIT 20
    """).fetchall()

    # Episodes (significant, high confidence)
    episodes = db.execute("""
        SELECT episode_type, content, confidence, occurred_at, context
        FROM episodes
        WHERE confidence > 0.65
        ORDER BY confidence DESC
        LIMIT 15
    """).fetchall()

    # Communication metrics
    metrics = db.execute("""
        SELECT metric_type, metric_data
        FROM communication_metrics
    """).fetchall()

    # High-confidence checkin observations (human-stated signals)
    checkin_obs = db.execute("""
        SELECT o.facet_id, o.signal_position, o.signal_strength, o.content,
               f.spectrum_low, f.spectrum_high
        FROM observations o
        JOIN facets f ON o.facet_id=f.id
        WHERE o.source_type='checkin_reply'
          AND o.signal_strength > 0.7
          AND o.signal_position IS NOT NULL
          AND o.signal_position != 0.5
        ORDER BY o.signal_strength DESC
        LIMIT 12
    """).fetchall()

    # Budget signals
    budget_obs = db.execute("""
        SELECT facet_id, signal_position, content
        FROM observations
        WHERE source_type='budget_analysis'
        ORDER BY rowid DESC
    """).fetchall()

    # LIWC linguistic profile (most recent 4 weeks)
    liwc_rows = db.execute("""
        SELECT period, message_count, i_ratio, negative_affect, positive_affect,
               future_focus, past_focus, cognitive_complexity, certainty_ratio
        FROM liwc_metrics ORDER BY period DESC LIMIT 4
    """).fetchall()

    # Stress snapshots (most recent 4 weeks)
    stress_rows = db.execute("""
        SELECT period, stress_index, stress_level, dominant_signal
        FROM stress_snapshots ORDER BY period DESC LIMIT 4
    """).fetchall()

    # Active schemas
    schema_rows = db.execute("""
        SELECT schema_name, schema_domain, activation_level, confidence,
               trigger_contexts, behavioral_signatures
        FROM schemas ORDER BY activation_level DESC LIMIT 6
    """).fetchall()

    # Active goals
    goal_rows = db.execute("""
        SELECT goal_text, domain, horizon, progress, priority_rank, conflicts_with
        FROM goals WHERE status='active' ORDER BY priority_rank ASC LIMIT 8
    """).fetchall()

    # CAPS signatures
    caps_rows = db.execute("""
        SELECT situation_type, behavioral_response, emotional_response, confidence
        FROM caps_signatures ORDER BY confidence DESC LIMIT 8
    """).fetchall()

    # SDT satisfaction (most recent per domain)
    sdt_rows = db.execute("""
        SELECT domain, autonomy_satisfaction, competence_satisfaction,
               relatedness_satisfaction
        FROM sdt_satisfaction
        WHERE period = (SELECT MAX(period) FROM sdt_satisfaction)
    """).fetchall()

    # Attachment signals (ECR-R per context)
    attachment_rows = db.execute("""
        SELECT relationship_context, anxiety_level, avoidance_level,
               secure_behaviors, avoidant_behaviors, evidence
        FROM attachment_signals ORDER BY id
    """).fetchall()

    # Narrative episodes (peak/nadir/turning_point)
    narrative_rows = db.execute("""
        SELECT episode_type, content
        FROM episodes WHERE episode_type LIKE 'narrative_%'
        ORDER BY id
    """).fetchall()

    # v3: Idiolect profile (most recent "all" period)
    idiolect_rows = db.execute("""
        SELECT period, type_token_ratio, hapax_legomena_pct,
               avg_sentence_length, fragment_pct, english_word_pct,
               ellipsis_rate, emoji_rate, filler_phrases, top_bigrams
        FROM idiolect_profile WHERE period='all'
        LIMIT 1
    """).fetchall()

    # v3: Appraisal patterns
    appraisal_rows = db.execute("""
        SELECT domain, novelty_sensitivity, goal_relevance_weight,
               coping_potential_default, agency_attribution,
               norm_compatibility_weight, typical_appraisals
        FROM appraisal_patterns ORDER BY domain
    """).fetchall()

    # v3: Mental model patterns
    mental_model_rows = db.execute("""
        SELECT domain, representation_style, model_complexity,
               counterfactual_frequency, analogy_preference
        FROM mental_model_patterns ORDER BY domain
    """).fetchall()

    # v3: Dual process profile
    dual_process_rows = db.execute("""
        SELECT domain, system1_dominance, switching_triggers,
               self_correction_rate, deliberation_marker_rate
        FROM dual_process_profile ORDER BY domain
    """).fetchall()

    # v3: Personal constructs
    construct_rows = db.execute("""
        SELECT construct_name, pole_positive, pole_negative,
               superordinate, usage_frequency, range_of_convenience
        FROM personal_constructs ORDER BY usage_frequency DESC
    """).fetchall()

    # IMP-04: drift alerts (model changes)
    drift_rows = db.execute("""
        SELECT hypothesis, confidence, created_at
        FROM hypotheses WHERE hypothesis LIKE '[drift_alert]%'
        ORDER BY created_at DESC LIMIT 10
    """).fetchall()

    # IMP-19: domain coverage (last 30 days observations)
    domain_rows = db.execute("""
        SELECT conversation_domain, COUNT(*) as cnt
        FROM observations
        WHERE conversation_domain IS NOT NULL
          AND created_at >= datetime('now', '-30 days')
        GROUP BY conversation_domain
        ORDER BY cnt DESC
    """).fetchall()

    # Source type breakdown
    source_rows = db.execute("""
        SELECT source_type, COUNT(*) as cnt
        FROM observations
        WHERE created_at >= datetime('now', '-30 days')
        GROUP BY source_type ORDER BY cnt DESC
    """).fetchall()

    # IMP-17: IPIP anchor z-scores (for facets with anchors)
    anchor_rows = db.execute("""
        SELECT fa.facet_id, fa.ipip_subscale, fa.direction,
               fa.population_mean, fa.population_sd, t.value_position
        FROM facet_anchors fa
        JOIN traits t ON t.facet_id = fa.facet_id
        WHERE t.value_position IS NOT NULL AND t.confidence > 0.3
        ORDER BY fa.facet_id
    """).fetchall()

    # IMP-17: facets WITHOUT anchors (to label as "no normative reference")
    anchored_ids = {r["facet_id"] for r in anchor_rows}
    all_facet_ids_rows = db.execute("SELECT id FROM facets").fetchall()
    unmapped_facet_ids = [r["id"] for r in all_facet_ids_rows if r["id"] not in anchored_ids]

    # IMP-02: active corrections (unapplied or applied in last 30 days)
    correction_rows = db.execute("""
        SELECT id, facet_id, correction_note, created_at, applied_at
        FROM corrections
        WHERE applied_at IS NULL
           OR applied_at >= datetime('now', '-30 days')
        ORDER BY created_at DESC
        LIMIT 20
    """).fetchall()

    # IMP-10: low-consensus schemas and defenses (defenses stored in schemas table)
    low_consensus_schemas = db.execute("""
        SELECT schema_name, schema_domain, confidence, activation_level
        FROM schemas WHERE consensus = 0 AND schema_domain != 'defense_mechanism'
        ORDER BY activation_level DESC LIMIT 10
    """).fetchall()
    low_consensus_defenses = db.execute("""
        SELECT schema_name as defense_name, activation_level as activation_strength, confidence
        FROM schemas WHERE consensus = 0 AND schema_domain = 'defense_mechanism'
        ORDER BY activation_level DESC LIMIT 10
    """).fetchall()

    # IMP-12: most recent implicit motives
    motives_rows = db.execute("""
        SELECT n_ach, n_aff, n_pow, sample_size, evidence, computed_at
        FROM implicit_motives ORDER BY computed_at DESC LIMIT 1
    """).fetchall()

    return {
        "traits": [dict(r) for r in traits],
        "hypotheses": [dict(r) for r in hypotheses],
        "entities": [dict(r) for r in entities],
        "episodes": [dict(r) for r in episodes],
        "metrics": {r["metric_type"]: json.loads(r["metric_data"]) for r in metrics},
        "checkin_obs": [dict(r) for r in checkin_obs],
        "budget_obs": [dict(r) for r in budget_obs],
        "liwc": [dict(r) for r in liwc_rows],
        "stress": [dict(r) for r in stress_rows],
        "schemas": [dict(r) for r in schema_rows],
        "goals": [dict(r) for r in goal_rows],
        "caps": [dict(r) for r in caps_rows],
        "sdt": [dict(r) for r in sdt_rows],
        "attachment": [dict(r) for r in attachment_rows],
        "narrative": [dict(r) for r in narrative_rows],
        "idiolect": [dict(r) for r in idiolect_rows],
        "appraisal": [dict(r) for r in appraisal_rows],
        "mental_models": [dict(r) for r in mental_model_rows],
        "dual_process": [dict(r) for r in dual_process_rows],
        "constructs": [dict(r) for r in construct_rows],
        "drift_alerts": [dict(r) for r in drift_rows],
        "domain_coverage": [dict(r) for r in domain_rows],
        "source_types": [dict(r) for r in source_rows],
        "anchors": [dict(r) for r in anchor_rows],
        "unmapped_facet_ids": unmapped_facet_ids,
        "corrections": [dict(r) for r in correction_rows],
        "low_consensus_schemas": [dict(r) for r in low_consensus_schemas],
        "low_consensus_defenses": [dict(r) for r in low_consensus_defenses],
        "motives": [dict(r) for r in motives_rows],
    }


# ---------------------------------------------------------------------------
# Programmatic preamble (IMP-04, IMP-17, IMP-19)
# ---------------------------------------------------------------------------

def build_preamble(data: dict) -> str:
    """Build the static programmatic preamble prepended before the LLM portrait."""
    import math
    sections = []

    # Drift alerts (IMP-04)
    drift = data.get("drift_alerts", [])
    if drift:
        lines = ["## Variazioni del Modello (ultimi 30 giorni)\n"]
        for d in drift:
            lines.append(f"- {d['hypothesis']}")
        sections.append("\n".join(lines))

    # Domain coverage (IMP-19)
    domains = data.get("domain_coverage", [])
    if domains:
        total = sum(d["cnt"] for d in domains)
        lines = ["## Copertura Corpus (ultimi 30 giorni)\n"]
        lines.append(f"| Dominio | Osservazioni | % |")
        lines.append("|---|---|---|")
        for d in domains:
            pct = 100 * d["cnt"] / total if total else 0
            lines.append(f"| {d['conversation_domain']} | {d['cnt']} | {pct:.0f}% |")
        sections.append("\n".join(lines))

    # Source type breakdown
    sources = data.get("source_types", [])
    if sources:
        total = sum(s["cnt"] for s in sources)
        lines = ["## Sorgenti Osservazioni (ultimi 30 giorni)\n"]
        lines.append("| Sorgente | Count | % |")
        lines.append("|---|---|---|")
        for s in sources:
            pct = 100 * s["cnt"] / total if total else 0
            lines.append(f"| {s['source_type']} | {s['cnt']} | {pct:.0f}% |")
        sections.append("\n".join(lines))

    # IPIP z-scores (IMP-17)
    anchors = data.get("anchors", [])
    if anchors:
        lines = ["## Calibrazione Normativa (IPIP)\n"]
        lines.append("> **Nota**: z-score rispetto a un campione di convenienza, non norma rappresentativa.\n")
        lines.append("| Facet | Subscala IPIP | Posizione | z-score | Percentile stimato |")
        lines.append("|---|---|---|---|---|")
        for a in anchors:
            pos = a["value_position"]
            mean = a["population_mean"]
            sd = a["population_sd"]
            direction = a["direction"]
            # Flip sign for negative-direction mappings (e.g. low neuroticism = high pos)
            effective_pos = pos if direction == "positive" else (1.0 - pos)
            # Scale 0-1 position to approximate 1-5 Likert range
            scaled = 1.0 + effective_pos * 4.0
            z = (scaled - mean) / (sd + 1e-6)
            z = max(-3.0, min(3.0, z))
            # Rough percentile from z
            try:
                pct = int(50 * (1 + math.erf(z / math.sqrt(2))))
            except Exception:
                pct = 50
            lines.append(
                f"| {a['facet_id']} | {a['ipip_subscale']} | {pos:.2f} | "
                f"{z:+.2f} | ~{pct}° percentile |"
            )
        # IMP-17: label unmapped facets
        unmapped = data.get("unmapped_facet_ids", [])
        if unmapped:
            lines.append(f"\n**Facet senza riferimento normativo** ({len(unmapped)}): "
                         + ", ".join(str(f) for f in unmapped[:20])
                         + (" ..." if len(unmapped) > 20 else ""))
        sections.append("\n".join(lines))

    # IMP-02: active corrections
    corrections = data.get("corrections", [])
    if corrections:
        lines = ["## Correzioni Attive (IMP-02)\n"]
        for c in corrections:
            status = "applicata" if c.get("applied_at") else "in attesa"
            lines.append(f"- [{status}] Facet {c.get('facet_id', '?')}: {c['correction_note']}")
        sections.append("\n".join(lines))

    # IMP-10: low-consensus inferences
    lcs = data.get("low_consensus_schemas", [])
    lcd = data.get("low_consensus_defenses", [])
    if lcs or lcd:
        lines = ["## Inferenze a Bassa Confidenza Consensus (IMP-10)\n"]
        lines.append("> ⚠️ Le seguenti inferenze provengono da un solo modello (consenso non raggiunto).\n")
        if lcs:
            lines.append("**Schemi:**")
            for s in lcs:
                lines.append(f"- {s['schema_name']} [{s['schema_domain']}] "
                              f"activation={s['activation_level']:.2f} conf={s['confidence']:.2f} ⚠️ basso consenso")
        if lcd:
            lines.append("**Difese:**")
            for d in lcd:
                lines.append(f"- {d['defense_name']} strength={d['activation_strength']:.2f} "
                              f"conf={d['confidence']:.2f} ⚠️ basso consenso")
        sections.append("\n".join(lines))

    if not sections:
        return ""
    return "\n\n".join(sections) + "\n\n---\n\n"


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

PORTRAIT_PROMPT = """You are synthesizing a comprehensive personality portrait of the subject.
This portrait will be injected into their relational agent's context via the bootstrap hook,
so it should read as a deep behavioral model - not a clinical report.

Write a narrative portrait in ITALIAN, ~1200-1500 words, present tense.
Structure it with markdown headers. Make it human and insightful - not a clinical report.
This should read like something a close friend who has studied the subject carefully would write.

--- PERSONALITY DATA ---

TRATTI (confidence > 0.5):
{traits}

IPOTESI COMPORTAMENTALI:
{hypotheses}

--- SEGNALI DIRETTI (dalle risposte ai check-in) ---
{checkin_signals}

--- DATI FINANZIARI/COMPORTAMENTALI ---
{budget_signals}

--- METRICHE COMUNICATIVE ---
{metrics}

--- PERSONE E RELAZIONI ---
{entities}

--- EPISODI SIGNIFICATIVI ---
{episodes}

--- PROFILO LINGUISTICO (LIWC - ultime settimane) ---
{liwc_summary}

--- STRESS INDEX RECENTE ---
{stress_summary}

--- SCHEMI MALADATTATIVI ATTIVI (Young) ---
{schemas_summary}

--- GOAL ATTIVI E CONFLITTI ---
{goals_summary}

--- FIRME SITUAZIONALI (CAPS) ---
{caps_summary}

--- BISOGNI PSICOLOGICI FONDAMENTALI (SDT) ---
{sdt_summary}

--- PATTERN DI ATTACCAMENTO (ECR-R) ---
{attachment_summary}

--- IDENTITÀ NARRATIVA (McAdams) ---
{narrative_summary}

--- IMPRONTA LINGUISTICA (Idioletto) ---
{idiolect_summary}

--- PATTERN DI APPRAISAL EMOTIVO (Lazarus/Scherer) ---
{appraisal_summary}

--- MODELLI MENTALI (Johnson-Laird) ---
{mental_models_summary}

--- PROCESSO DUALE S1/S2 (Kahneman) ---
{dual_process_summary}

--- COSTRUTTI PERSONALI (Kelly) ---
{constructs_summary}

--- MOTIVI IMPLICITI (McClelland n-Ach/Aff/Pow) ---
{motives_summary}

---

Write the portrait with these sections:
## Chi è the subject
(Sintesi di chi è come persona - 2-3 paragrafi)

## Come pensa
(Stile cognitivo, approccio ai problemi, decisioni)

## Come ragiona (processi cognitivi profondi)
(Modelli mentali, Sistema 1/2, costrutti personali - come elabora informazioni e prende decisioni)

## Come comunica
(Stile comunicativo, humor, tono, impronta linguistica)

## Il suo mondo
(Persone importanti, progetti, interessi)

## Pattern e abitudini
(Ritmi temporali, comportamenti ricorrenti, preferenze)

## Valori e tensioni
(Cosa conta per lui, dove emergono tensioni)

## Schemi e pattern profondi
(Schemi di Young attivi, difese, pattern relazionali profondi)

## Bisogni attuali e soddisfazione
(Quanto sono soddisfatti autonomia/competenza/relazionalità nei diversi domini della vita)

## Firme situazionali chiave
(Come risponde in situazioni specifiche - le sue "firme comportamentali")

## Relational guidance
(Consigli pratici su come relazionarsi con il soggetto, cosa funziona, cosa evitare)

Rules:
- Write entirely in Italian
- Use specific evidence from the data - quote real behaviors, not generic observations
- For traits near poles (pos < 0.25 or pos > 0.75): state them clearly
- For neutral traits (0.4-0.6): acknowledge ambiguity
- For the "Relational guidance" section: be concrete and actionable
- Integrate ALL data sources: don't ignore the financial signals or communication metrics
"""


def _pole_label(pos: float, low: str, high: str) -> str:
    if pos < 0.25:
        return f"{low} (forte)"
    elif pos < 0.40:
        return f"{low} (moderato)"
    elif pos < 0.60:
        return "neutro/bilanciato"
    elif pos < 0.75:
        return f"{high} (moderato)"
    else:
        return f"{high} (forte)"


def build_prompt(data: dict) -> str:
    # Traits
    trait_lines = []
    for t in data["traits"]:
        pole = _pole_label(t["value_position"], t["spectrum_low"] or "?", t["spectrum_high"] or "?")
        trait_lines.append(
            f"  {t['id']} [{t['category']}]: {pole} | conf={t['confidence']:.2f}"
        )

    # Hypotheses
    hyp_lines = [
        f"  [{h['confidence']:.2f}] {h['hypothesis']}"
        for h in data["hypotheses"]
        if h['hypothesis']
    ]

    # Checkin signals
    ck_lines = []
    for o in data["checkin_obs"]:
        pole = _pole_label(o["signal_position"], o["spectrum_low"] or "?", o["spectrum_high"] or "?")
        ck_lines.append(f"  {o['facet_id']}: {pole} (conf={o['signal_strength']:.2f})")
        if o["content"]:
            ck_lines.append(f"    → {o['content'][:100]}")

    # Budget signals
    bud_lines = [
        f"  {o['facet_id']}: pos={float(o['signal_position'] or 0.5):.2f} - {(o['content'] or '')[:100]}"
        for o in data["budget_obs"]
    ]

    # Metrics summary
    m = data["metrics"]
    metric_lines = []
    if "activity_hours" in m:
        ah = m["activity_hours"]
        metric_lines.append(
            f"  Attività: picco ore {ah['peak_hour']:02d}:xx, range {ah['active_range']}, "
            f"giorno principale: {ah['peak_dow']}, weekend: {ah['weekend_pct']}% dei messaggi"
        )
    if "msg_length" in m:
        ml = m["msg_length"]
        metric_lines.append(
            f"  Lunghezza msg: media {ml['mean_chars']:.0f} chars, "
            f"{ml['short_pct']:.0f}% brevi, {ml['long_pct']:.0f}% lunghi"
        )
    if "burst_pattern" in m:
        bp = m["burst_pattern"]
        metric_lines.append(
            f"  Pattern: {bp['single_msg_pct']:.0f}% msg singoli, "
            f"{bp['burst_msg_pct']:.0f}% in raffica (max {bp['max_burst_size']} consecutivi)"
        )
    if "vocabulary" in m:
        v = m["vocabulary"]
        metric_lines.append(
            f"  Vocabolario: TTR={v['ttr']:.3f}, {v['it_pct']:.0f}% italiano, "
            f"frase media {v['avg_sentence_length']} parole"
        )

    # Entities
    entity_lines = []
    seen = set()
    for e in data["entities"]:
        key = e["name"]
        if key in seen:
            continue
        seen.add(key)
        desc = e["description"] or ""
        rel = e["relation_type"] or e["label"] or e["entity_type"]
        dyn = e["dynamic"] or ""
        sent = f"sentiment={e['sentiment']:.1f}" if e["sentiment"] is not None else ""
        entity_lines.append(
            f"  {e['name']} [{rel}] ({e['mention_count']}x) - {desc[:60]} {dyn} {sent}".strip()
        )

    # Episodes
    ep_lines = []
    for ep in data["episodes"]:
        date_str = f"({ep['occurred_at'][:10]})" if ep['occurred_at'] else ""
        ep_lines.append(
            f"  [{ep['episode_type']}] {date_str} {ep['content'][:100]}"
        )

    # LIWC summary
    liwc_lines = []
    for lw in data.get("liwc", []):
        liwc_lines.append(
            f"  [{lw['period']}] {lw['message_count']}msg | "
            f"neg_affect={lw['negative_affect']:.2f} pos={lw['positive_affect']:.2f} | "
            f"future={lw['future_focus']:.2f} past={lw['past_focus']:.2f} | "
            f"complexity={lw['cognitive_complexity']:.2f}"
        )

    # Stress summary
    stress_lines = []
    for sw in data.get("stress", []):
        stress_lines.append(
            f"  [{sw['period']}] index={sw['stress_index']:.3f} ({sw['stress_level']}) "
            f"dominant={sw['dominant_signal']}"
        )

    # Schemas summary
    schema_lines = []
    for s in data.get("schemas", []):
        triggers = s.get("trigger_contexts", "[]")
        if isinstance(triggers, str):
            try:
                triggers = ", ".join(json.loads(triggers))
            except Exception:
                pass
        schema_lines.append(
            f"  {s['schema_name']} [{s['schema_domain']}] "
            f"activation={s['activation_level']:.2f} conf={s['confidence']:.2f} | "
            f"triggers: {triggers}"
        )

    # Goals summary
    goal_lines = []
    for g in data.get("goals", []):
        conflicts = g.get("conflicts_with", "[]")
        if isinstance(conflicts, str):
            try:
                conflicts_list = json.loads(conflicts)
                conflicts = f"conflicts: {len(conflicts_list)}" if conflicts_list else ""
            except Exception:
                conflicts = ""
        goal_lines.append(
            f"  [{g['priority_rank']}] [{g['domain']}|{g['horizon']}] "
            f"progress={g['progress']:.1f} - {g['goal_text'][:80]} {conflicts}"
        )

    # CAPS summary
    caps_lines = []
    for c in data.get("caps", []):
        caps_lines.append(
            f"  IF {c['situation_type']} → {c['behavioral_response'][:70]} "
            f"(conf={c['confidence']:.2f})"
        )

    # SDT summary
    sdt_lines = []
    for s in data.get("sdt", []):
        sdt_lines.append(
            f"  {s['domain']}: aut={s['autonomy_satisfaction']:.1f} "
            f"comp={s['competence_satisfaction']:.1f} "
            f"rel={s['relatedness_satisfaction']:.1f}"
        )

    # Attachment summary
    attach_lines = []
    for a in data.get("attachment", []):
        ctx = a["relationship_context"]
        anx = a["anxiety_level"]
        avo = a["avoidance_level"]
        style = "sicuro"
        if anx > 0.5 and avo > 0.5:
            style = "timoroso-evitante"
        elif anx > 0.5:
            style = "preoccupato"
        elif avo > 0.5:
            style = "distanziante-evitante"
        attach_lines.append(
            f"  {ctx}: ansia={anx:.2f} evitamento={avo:.2f} → {style}"
        )

    # Narrative summary
    narr_lines = []
    for n in data.get("narrative", []):
        etype = n["episode_type"].replace("narrative_", "")
        narr_lines.append(f"  [{etype}] {n['content'][:100]}")

    # v3: Idiolect summary
    idiolect_lines = []
    for i in data.get("idiolect", []):
        fillers = ""
        try:
            fillers = ", ".join(f[0] for f in json.loads(i.get("filler_phrases", "[]"))[:5])
        except Exception:
            pass
        idiolect_lines.append(
            f"  TTR={i['type_token_ratio']:.3f} | hapax={i['hapax_legomena_pct']:.1f}% | "
            f"avg_sent={i['avg_sentence_length']:.1f}w | fragments={i['fragment_pct']:.0f}% | "
            f"english={i['english_word_pct']:.1f}% | emoji={i['emoji_rate']:.3f}/100ch"
        )
        if fillers:
            idiolect_lines.append(f"  Filler ricorrenti: {fillers}")

    # v3: Appraisal summary
    appraisal_lines = []
    for a in data.get("appraisal", []):
        appraisal_lines.append(
            f"  {a['domain']}: agency={a['agency_attribution']}, "
            f"coping={a['coping_potential_default']:.2f}, "
            f"goal_relevance={a['goal_relevance_weight']:.2f}, "
            f"novelty_sens={a['novelty_sensitivity']:.2f}"
        )

    # v3: Mental models summary
    mm_lines = []
    for m in data.get("mental_models", []):
        mm_lines.append(
            f"  {m['domain']}: style={m['representation_style']}, "
            f"complexity={m['model_complexity']}, "
            f"counterfactual={m['counterfactual_frequency']:.2f}, "
            f"analogy={m['analogy_preference']:.2f}"
        )

    # v3: Dual process summary
    dp_lines = []
    for d in data.get("dual_process", []):
        triggers = ""
        try:
            triggers = ", ".join(json.loads(d.get("switching_triggers", "[]"))[:3])
        except Exception:
            pass
        dp_lines.append(
            f"  {d['domain']}: S1_dom={d['system1_dominance']:.2f}, "
            f"self_corr={d['self_correction_rate']:.1f}/1000w, "
            f"delib={d['deliberation_marker_rate']:.1f}/1000w"
        )
        if triggers:
            dp_lines.append(f"    S2 triggers: {triggers}")

    # v3: Constructs summary
    constr_lines = []
    for c in data.get("constructs", []):
        sup = " [SUPERORDINATO]" if c.get("superordinate") else ""
        domains = ""
        try:
            domains = ", ".join(json.loads(c.get("range_of_convenience", "[]")))
        except Exception:
            pass
        constr_lines.append(
            f"  {c['pole_positive']}-{c['pole_negative']}{sup} "
            f"(freq={c['usage_frequency']:.2f}) [{domains}]"
        )

    # IMP-12: Implicit motives summary
    motives_lines = []
    for mv in data.get("motives", []):
        motives_lines.append(
            f"  n-Ach={mv['n_ach']:.2f} n-Aff={mv['n_aff']:.2f} n-Pow={mv['n_pow']:.2f} "
            f"(sample={mv['sample_size']}, {mv['computed_at'][:10]})"
        )
        try:
            ev = json.loads(mv["evidence"])
            for k in ("n_ach", "n_aff", "n_pow"):
                note = ev.get(k, "")
                if note:
                    label = {"n_ach": "Achievement", "n_aff": "Affiliation", "n_pow": "Power"}[k]
                    motives_lines.append(f"    {label}: {note[:120]}")
        except Exception:
            pass

    return PORTRAIT_PROMPT.format(
        traits="\n".join(trait_lines) or "  (nessuno)",
        hypotheses="\n".join(hyp_lines) or "  (nessuna)",
        checkin_signals="\n".join(ck_lines) or "  (nessuno)",
        budget_signals="\n".join(bud_lines) or "  (nessuno)",
        metrics="\n".join(metric_lines) or "  (nessuna)",
        entities="\n".join(entity_lines) or "  (nessuna)",
        episodes="\n".join(ep_lines) or "  (nessuno)",
        liwc_summary="\n".join(liwc_lines) or "  (nessuno)",
        stress_summary="\n".join(stress_lines) or "  (nessuno)",
        schemas_summary="\n".join(schema_lines) or "  (nessuno)",
        goals_summary="\n".join(goal_lines) or "  (nessuno)",
        caps_summary="\n".join(caps_lines) or "  (nessuno)",
        sdt_summary="\n".join(sdt_lines) or "  (nessuno)",
        attachment_summary="\n".join(attach_lines) or "  (nessuno)",
        narrative_summary="\n".join(narr_lines) or "  (nessuno)",
        idiolect_summary="\n".join(idiolect_lines) or "  (nessuno)",
        appraisal_summary="\n".join(appraisal_lines) or "  (nessuno)",
        mental_models_summary="\n".join(mm_lines) or "  (nessuno)",
        dual_process_summary="\n".join(dp_lines) or "  (nessuno)",
        constructs_summary="\n".join(constr_lines) or "  (nessuno)",
        motives_summary="\n".join(motives_lines) or "  (nessuno)",
    )


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, model: str) -> str:
    parts = model.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Invalid model: {model}")
    provider, model_id = parts

    config = load_nanobot_config()
    cfg = (config.get("providers") or {}).get(provider)
    if not cfg:
        # Fall back to ProviderLLMClient (supports ollama/anthropic/openai/nvidia/openrouter)
        try:
            import sys as _sys
            import os as _os
            _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from lib.provider_llm_client import ProviderLLMClient
            client = ProviderLLMClient()
            return client.complete(prompt)
        except Exception as exc:
            raise ValueError(f"Provider {provider} not found and ProviderLLMClient failed: {exc}") from exc

    parsed = urllib.parse.urlparse(cfg["apiBase"])
    host = parsed.netloc
    api_base = parsed.path.lstrip("/")
    use_https = parsed.scheme.lower() == "https"

    payload = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": (
                "You are a skilled writer synthesizing a personality portrait "
                "from behavioral and conversational data. Write only the portrait "
                "content - no preamble, no meta-commentary."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 3000,
    }

    conn_cls = http.client.HTTPSConnection if use_https else http.client.HTTPConnection
    conn = conn_cls(host, timeout=LLM_TIMEOUT_SECONDS)
    headers = {"Content-Type": "application/json"}
    if cfg.get("apiKey"):
        headers["Authorization"] = f"Bearer {cfg['apiKey']}"

    try:
        conn.request("POST", f"/{api_base}/chat/completions",
                     json.dumps(payload), headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(f"API {resp.status}: {body[:300]}")
        result = json.loads(body)
        msg = result["choices"][0]["message"]
        content = msg.get("content") or msg.get("reasoning") or ""
        if not content:
            raise RuntimeError("Empty response from LLM")
        return content.strip()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(model: str = DEFAULT_MODEL, force: bool = False, dry_run: bool = False) -> None:
    db = get_db()
    try:
        data = load_portrait_data(db)

        traits_count = len(data["traits"])
        entities_count = len(data["entities"])
        episodes_count = len(data["episodes"])

        info(SCRIPT, "data_loaded",
             traits=traits_count, entities=entities_count,
             episodes=episodes_count,
             hypotheses=len(data["hypotheses"]),
             checkin_obs=len(data["checkin_obs"]))

        prompt = build_prompt(data)

        if dry_run:
            print(f"Prompt length: {len(prompt)} chars")
            print("\n--- PROMPT PREVIEW (first 2000 chars) ---")
            print(prompt[:2000])
            return

        info(SCRIPT, "calling_llm", model=model, prompt_chars=len(prompt))
        portrait_text = _call_llm(prompt, model)
        info(SCRIPT, "llm_done", chars=len(portrait_text))

        # Write to PORTRAIT.md (with programmatic preamble)
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        header = (
            f"<!-- Generated by soulkiller_portrait.py on {now_str} -->\n"
            f"<!-- Traits: {traits_count} | Entities: {entities_count} | "
            f"Episodes: {episodes_count} -->\n\n"
        )
        preamble = build_preamble(data)

        PORTRAIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PORTRAIT_PATH.write_text(header + preamble + portrait_text + "\n", encoding="utf-8")

        info(SCRIPT, "portrait_saved", path=str(PORTRAIT_PATH), chars=len(portrait_text))

    finally:
        db.close()


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Soulkiller Portrait Synthesizer")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--force", action="store_true",
                        help="Regenerate even if portrait is recent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show prompt without calling LLM")
    args = parser.parse_args()
    run(model=args.model, force=args.force, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
