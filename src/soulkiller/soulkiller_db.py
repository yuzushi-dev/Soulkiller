#!/usr/bin/env python3
"""Soulkiller SQLite database: schema, seed data, and module API.

Database at: workspace/soulkiller/soulkiller.db
60-facet personality model with observations, traits, hypotheses, and check-in tracking.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn, error

SCRIPT = "soulkiller_db"
DB_DIR = Path(__file__).resolve().parents[1] / "soulkiller"
DB_PATH = DB_DIR / "soulkiller.db"

# ── Schema ──────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facets (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    spectrum_low TEXT,
    spectrum_high TEXT,
    sensitivity TEXT DEFAULT 'media',
    intrusion_base REAL DEFAULT 0.45,
    half_life_days INTEGER DEFAULT 14
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facet_id TEXT NOT NULL REFERENCES facets(id),
    source_type TEXT NOT NULL,
    source_ref TEXT,
    content TEXT NOT NULL,
    extracted_signal TEXT,
    signal_strength REAL DEFAULT 0.5,
    signal_position REAL,
    context TEXT,
    context_metadata TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(facet_id, source_ref)
);

CREATE TABLE IF NOT EXISTS traits (
    facet_id TEXT PRIMARY KEY REFERENCES facets(id),
    value_position REAL,
    confidence REAL DEFAULT 0.0,
    observation_count INTEGER DEFAULT 0,
    last_observation_at TEXT,
    last_synthesis_at TEXT,
    notes TEXT,
    status TEXT DEFAULT 'insufficient_data'
);

CREATE TABLE IF NOT EXISTS hypotheses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hypothesis TEXT NOT NULL,
    status TEXT DEFAULT 'unverified',
    supporting_observations TEXT,
    contradicting_observations TEXT,
    confidence REAL DEFAULT 0.3,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checkin_exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facet_id TEXT REFERENCES facets(id),
    question_text TEXT NOT NULL,
    reply_text TEXT,
    reply_captured_at TEXT,
    observations_extracted INTEGER DEFAULT 0,
    asked_at TEXT NOT NULL,
    message_id TEXT,
    followup_sent_at TEXT
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT UNIQUE,
    from_id TEXT NOT NULL,
    content TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed INTEGER DEFAULT 0,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS model_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    total_observations INTEGER,
    avg_confidence REAL,
    coverage_pct REAL,
    snapshot_data TEXT
);

CREATE INDEX IF NOT EXISTS idx_observations_facet ON observations(facet_id);
CREATE INDEX IF NOT EXISTS idx_observations_source ON observations(source_type);
CREATE INDEX IF NOT EXISTS idx_inbox_processed ON inbox(processed);
CREATE INDEX IF NOT EXISTS idx_checkin_reply ON checkin_exchanges(reply_text);

-- Memory layer tables (episodic memory, communication metrics, decisions)
CREATE TABLE IF NOT EXISTS entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    name TEXT NOT NULL,
    label TEXT,
    description TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    mention_count INTEGER DEFAULT 1,
    metadata TEXT,
    UNIQUE(entity_type, name)
);

CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    confidence REAL DEFAULT 0.7,
    occurred_at TEXT,
    extracted_at TEXT NOT NULL,
    entity_names TEXT,
    context TEXT,
    active INTEGER DEFAULT 1,
    superseded_by INTEGER REFERENCES episodes(id),
    UNIQUE(episode_type, source_ref)
);

CREATE TABLE IF NOT EXISTS entity_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    relation_type TEXT NOT NULL,
    dynamic TEXT,
    sentiment REAL,
    evidence TEXT,
    source_ref TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_id, source_ref)
);

CREATE TABLE IF NOT EXISTS communication_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    period TEXT NOT NULL,
    metric_type TEXT NOT NULL,
    metric_data TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    computed_at TEXT NOT NULL,
    UNIQUE(platform, chat_id, period, metric_type)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision TEXT NOT NULL,
    domain TEXT,
    facet_ids TEXT,
    direction TEXT,
    source_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    decided_at TEXT,
    extracted_at TEXT NOT NULL,
    context TEXT,
    UNIQUE(source_ref)
);

CREATE TABLE IF NOT EXISTS context_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    facet_id TEXT NOT NULL REFERENCES facets(id),
    cluster_label TEXT NOT NULL,
    context_filter TEXT NOT NULL,
    value_position REAL NOT NULL,
    confidence REAL NOT NULL,
    observation_count INTEGER NOT NULL,
    weight REAL NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(facet_id, cluster_label)
);

CREATE INDEX IF NOT EXISTS idx_context_clusters_facet ON context_clusters(facet_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_episodes_type ON episodes(episode_type);
CREATE INDEX IF NOT EXISTS idx_episodes_active ON episodes(active);
CREATE INDEX IF NOT EXISTS idx_decisions_domain ON decisions(domain);
CREATE INDEX IF NOT EXISTS idx_entity_relations_entity ON entity_relations(entity_id);

-- IMP-07: CAPS prediction validation loop
CREATE TABLE IF NOT EXISTS caps_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature_id INTEGER NOT NULL REFERENCES caps_signatures(id),
    prediction_text TEXT NOT NULL,
    pattern_regex TEXT,
    confirmations INTEGER DEFAULT 0,
    disconfirmations INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(signature_id, prediction_text)
);

-- IMP-17: IPIP anchor mapping for inter-subject spectral calibration
CREATE TABLE IF NOT EXISTS facet_anchors (
    facet_id TEXT NOT NULL REFERENCES facets(id),
    ipip_subscale TEXT NOT NULL,
    direction TEXT NOT NULL,
    population_mean REAL NOT NULL,
    population_sd REAL NOT NULL,
    PRIMARY KEY (facet_id, ipip_subscale)
);

-- IMP-15: Psychometric benchmark sessions
CREATE TABLE IF NOT EXISTS benchmark_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument TEXT NOT NULL,
    item_id TEXT NOT NULL,
    response INTEGER NOT NULL,
    session_date TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(instrument, item_id, session_date)
);

-- IMP-12: Implicit motives (McClelland n-Ach/Aff/Pow)
CREATE TABLE IF NOT EXISTS implicit_motives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    n_ach REAL NOT NULL,
    n_aff REAL NOT NULL,
    n_pow REAL NOT NULL,
    sample_size INTEGER NOT NULL,
    evidence TEXT,
    computed_at TEXT NOT NULL
);

-- IMP-20–28: Wearable biofeedback readings (Amazfit Helio Ring)
CREATE TABLE IF NOT EXISTS biofeedback_readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'helio_ring',
    signal_type TEXT NOT NULL,
    value REAL,
    unit TEXT,
    metadata_json TEXT,
    pulled_at TEXT NOT NULL,
    UNIQUE(date, signal_type)
);
CREATE INDEX IF NOT EXISTS idx_biofeedback_date ON biofeedback_readings(date);
CREATE INDEX IF NOT EXISTS idx_biofeedback_type ON biofeedback_readings(signal_type);
"""

# ── 46 Facet Seed Data ──────────────────────────────────────────────────────

FACETS: list[dict[str, Any]] = [
    # Cognitive (6)
    {"id": "cognitive.decision_speed", "category": "cognitive", "name": "decision_speed",
     "description": "Velocità nel prendere decisioni: impulsivo vs deliberato",
     "spectrum_low": "impulsivo", "spectrum_high": "deliberato",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "cognitive.risk_tolerance", "category": "cognitive", "name": "risk_tolerance",
     "description": "Propensione al rischio nelle scelte",
     "spectrum_low": "risk-averse", "spectrum_high": "risk-seeking",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "cognitive.abstraction_level", "category": "cognitive", "name": "abstraction_level",
     "description": "Livello di astrazione nel ragionamento",
     "spectrum_low": "concreto", "spectrum_high": "astratto",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "cognitive.information_gathering", "category": "cognitive", "name": "information_gathering",
     "description": "Approccio alla raccolta informazioni: basta abbastanza vs vuole il meglio",
     "spectrum_low": "satisficer (basta abbastanza)", "spectrum_high": "maximizer (vuole il meglio)",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "cognitive.analytical_approach", "category": "cognitive", "name": "analytical_approach",
     "description": "Approccio analitico: intuitivo vs sistematico",
     "spectrum_low": "intuitivo", "spectrum_high": "sistematico",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "cognitive.learning_style", "category": "cognitive", "name": "learning_style",
     "description": "Stile di apprendimento: pratica prima o teoria prima",
     "spectrum_low": "practice-first", "spectrum_high": "theory-first",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    # Emotional (6)
    {"id": "emotional.stress_response", "category": "emotional", "name": "stress_response",
     "description": "Risposta allo stress: freeze/evita vs fight/affronta",
     "spectrum_low": "freeze/evita", "spectrum_high": "fight/affronta",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.emotional_granularity", "category": "emotional", "name": "emotional_granularity",
     "description": "Granularità emotiva: generico vs articolato nel descrivere emozioni",
     "spectrum_low": "emotivamente generico", "spectrum_high": "emotivamente articolato",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.resilience_pattern", "category": "emotional", "name": "resilience_pattern",
     "description": "Pattern di resilienza: recovery lento vs bounce-back rapido",
     "spectrum_low": "recovery lento", "spectrum_high": "bounce-back rapido",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.frustration_triggers", "category": "emotional", "name": "frustration_triggers",
     "description": "Soglia di frustrazione: alta soglia vs bassa soglia",
     "spectrum_low": "alta soglia", "spectrum_high": "bassa soglia",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.joy_sources", "category": "emotional", "name": "joy_sources",
     "description": "Fonti di soddisfazione: strumentale vs intrinseca",
     "spectrum_low": "soddisfazione strumentale", "spectrum_high": "soddisfazione intrinseca",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "emotional.emotional_expression", "category": "emotional", "name": "emotional_expression",
     "description": "Espressione emotiva: contenuto vs espressivo",
     "spectrum_low": "contenuto", "spectrum_high": "espressivo",
     "sensitivity": "alta", "intrusion_base": 0.65},
    # Communication (6)
    {"id": "communication.verbosity", "category": "communication", "name": "verbosity",
     "description": "Verbosità comunicativa: telegrafico vs elaborato",
     "spectrum_low": "telegrafico", "spectrum_high": "elaborato",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "communication.directness", "category": "communication", "name": "directness",
     "description": "Stile comunicativo: diplomatico vs schietto",
     "spectrum_low": "diplomatico", "spectrum_high": "schietto",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "communication.humor_type", "category": "communication", "name": "humor_type",
     "description": "Uso dell'umorismo: serio/raro vs humor frequente",
     "spectrum_low": "serio/raro", "spectrum_high": "humor frequente",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "communication.conflict_style", "category": "communication", "name": "conflict_style",
     "description": "Stile nel conflitto: evitante vs confrontativo",
     "spectrum_low": "evitante", "spectrum_high": "confrontativo",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "communication.storytelling_tendency", "category": "communication", "name": "storytelling_tendency",
     "description": "Tendenza narrativa: fattuale/dati vs aneddotico/narrativo",
     "spectrum_low": "fattuale/dati", "spectrum_high": "aneddotico/narrativo",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "communication.formality_range", "category": "communication", "name": "formality_range",
     "description": "Range di formalità: sempre informale vs adatta al contesto",
     "spectrum_low": "sempre informale", "spectrum_high": "adatta al contesto",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    # Relational (6)
    {"id": "relational.trust_formation", "category": "relational", "name": "trust_formation",
     "description": "Formazione della fiducia: fiducia lenta vs fiducia veloce",
     "spectrum_low": "fiducia lenta", "spectrum_high": "fiducia veloce",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "relational.boundary_style", "category": "relational", "name": "boundary_style",
     "description": "Stile dei confini personali: rigidi vs flessibili",
     "spectrum_low": "confini rigidi", "spectrum_high": "confini flessibili",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "relational.loyalty_pattern", "category": "relational", "name": "loyalty_pattern",
     "description": "Pattern di lealtà: condizionale vs incondizionata",
     "spectrum_low": "lealtà condizionale", "spectrum_high": "lealtà incondizionata",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "relational.social_energy", "category": "relational", "name": "social_energy",
     "description": "Energia sociale: introverso vs estroverso",
     "spectrum_low": "introverso", "spectrum_high": "estroverso",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "relational.help_seeking", "category": "relational", "name": "help_seeking",
     "description": "Ricerca di aiuto: indipendente vs collaborativo",
     "spectrum_low": "indipendente", "spectrum_high": "collaborativo",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "relational.feedback_preference", "category": "relational", "name": "feedback_preference",
     "description": "Preferenza feedback: critica diretta vs feedback mediato",
     "spectrum_low": "critica diretta", "spectrum_high": "feedback mediato",
     "sensitivity": "media", "intrusion_base": 0.45},
    # Values (6)
    {"id": "values.core_values", "category": "values", "name": "core_values",
     "description": "Valori fondamentali (non spettro lineare — lista di valori)",
     "spectrum_low": None, "spectrum_high": None,
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "values.fairness_model", "category": "values", "name": "fairness_model",
     "description": "Modello di equità: meritocratico vs egualitario",
     "spectrum_low": "meritocratico", "spectrum_high": "egualitario",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "values.authority_stance", "category": "values", "name": "authority_stance",
     "description": "Posizione verso l'autorità: rispetta la gerarchia vs sfida l'autorità",
     "spectrum_low": "rispetta la gerarchia", "spectrum_high": "sfida l'autorità",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "values.autonomy_importance", "category": "values", "name": "autonomy_importance",
     "description": "Importanza dell'autonomia: team-oriented vs indipendenza forte",
     "spectrum_low": "team-oriented", "spectrum_high": "indipendenza forte",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "values.aesthetic_values", "category": "values", "name": "aesthetic_values",
     "description": "Valori estetici: funzionale/pragmatico vs eleganza/bellezza",
     "spectrum_low": "funzionale/pragmatico", "spectrum_high": "eleganza/bellezza",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "values.work_ethic", "category": "values", "name": "work_ethic",
     "description": "Etica del lavoro: output-oriented vs effort-oriented",
     "spectrum_low": "output-oriented", "spectrum_high": "effort-oriented",
     "sensitivity": "media", "intrusion_base": 0.45},
    # Temporal (5)
    {"id": "temporal.planning_horizon", "category": "temporal", "name": "planning_horizon",
     "description": "Orizzonte di pianificazione: vive nel presente vs pianifica a lungo termine",
     "spectrum_low": "vive nel presente", "spectrum_high": "pianifica a lungo termine",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "temporal.routine_attachment", "category": "temporal", "name": "routine_attachment",
     "description": "Attaccamento alla routine: cerca varietà vs ama la routine",
     "spectrum_low": "cerca varietà", "spectrum_high": "ama la routine",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "temporal.deadline_behavior", "category": "temporal", "name": "deadline_behavior",
     "description": "Comportamento con le scadenze: last-minute vs finisce in anticipo",
     "spectrum_low": "last-minute", "spectrum_high": "finisce in anticipo",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "temporal.nostalgia_tendency", "category": "temporal", "name": "nostalgia_tendency",
     "description": "Tendenza alla nostalgia: proiettato al futuro vs orientato al passato",
     "spectrum_low": "proiettato al futuro", "spectrum_high": "orientato al passato",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "temporal.patience_threshold", "category": "temporal", "name": "patience_threshold",
     "description": "Soglia di pazienza: impaziente vs paziente",
     "spectrum_low": "impaziente", "spectrum_high": "paziente",
     "sensitivity": "media", "intrusion_base": 0.45},
    # Aesthetic (5)
    {"id": "aesthetic.design_sensibility", "category": "aesthetic", "name": "design_sensibility",
     "description": "Sensibilità al design: massimalista vs minimalista",
     "spectrum_low": "massimalista", "spectrum_high": "minimalista",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "aesthetic.music_taste", "category": "aesthetic", "name": "music_taste",
     "description": "Gusti musicali (non spettro lineare — generi e pattern)",
     "spectrum_low": None, "spectrum_high": None,
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "aesthetic.media_consumption", "category": "aesthetic", "name": "media_consumption",
     "description": "Consumo media: passivo/mainstream vs attivo/di nicchia",
     "spectrum_low": "passivo/mainstream", "spectrum_high": "attivo/di nicchia",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "aesthetic.food_preferences", "category": "aesthetic", "name": "food_preferences",
     "description": "Preferenze alimentari: comfort/abitudinario vs avventuroso",
     "spectrum_low": "comfort/abitudinario", "spectrum_high": "avventuroso",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    {"id": "aesthetic.environment_preference", "category": "aesthetic", "name": "environment_preference",
     "description": "Preferenza ambiente: caotico/stimolante vs ordinato/minimal",
     "spectrum_low": "caotico/stimolante", "spectrum_high": "ordinato/minimal",
     "sensitivity": "bassa", "intrusion_base": 0.25},
    # Meta-cognition (6)
    {"id": "meta_cognition.self_awareness", "category": "meta_cognition", "name": "self_awareness",
     "description": "Consapevolezza di sé: bassa consapevolezza vs alta consapevolezza",
     "spectrum_low": "bassa consapevolezza", "spectrum_high": "alta consapevolezza",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.growth_mindset", "category": "meta_cognition", "name": "growth_mindset",
     "description": "Mentalità di crescita: fixed mindset vs growth mindset",
     "spectrum_low": "fixed mindset", "spectrum_high": "growth mindset",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.cognitive_biases", "category": "meta_cognition", "name": "cognitive_biases",
     "description": "Bias cognitivi osservati (non spettro — lista di bias)",
     "spectrum_low": None, "spectrum_high": None,
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.reflection_habit", "category": "meta_cognition", "name": "reflection_habit",
     "description": "Abitudine alla riflessione: raramente si esamina vs auto-riflessione frequente",
     "spectrum_low": "raramente si esamina", "spectrum_high": "auto-riflessione frequente",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.change_readiness", "category": "meta_cognition", "name": "change_readiness",
     "description": "Prontezza al cambiamento: resiste al cambiamento vs abbraccia il cambiamento",
     "spectrum_low": "resiste al cambiamento", "spectrum_high": "abbraccia il cambiamento",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.uncertainty_tolerance", "category": "meta_cognition", "name": "uncertainty_tolerance",
     "description": "Tolleranza all'incertezza: bisogno di certezza vs a proprio agio con l'ambiguità",
     "spectrum_low": "bisogno di certezza", "spectrum_high": "a proprio agio con l'ambiguità",
     "sensitivity": "media", "intrusion_base": 0.45},

    # v2.0 facets — deep psychological constructs
    {"id": "relational.attachment_anxiety", "category": "relational", "name": "attachment_anxiety",
     "description": "Ansia da attaccamento (ECR-R): paura di rifiuto/abbandono nelle relazioni",
     "spectrum_low": "bassa ansia", "spectrum_high": "alta ansia",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "relational.attachment_avoidance", "category": "relational", "name": "attachment_avoidance",
     "description": "Evitamento da attaccamento (ECR-R): disagio con la vicinanza emotiva",
     "spectrum_low": "basso evitamento", "spectrum_high": "alto evitamento",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "relational.vulnerability_capacity", "category": "relational", "name": "vulnerability_capacity",
     "description": "Capacità di mostrarsi vulnerabile: chiuso vs aperto nella vulnerabilità",
     "spectrum_low": "bassa capacità", "spectrum_high": "alta capacità",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.emotion_clarity", "category": "emotional", "name": "emotion_clarity",
     "description": "Chiarezza emotiva (DERS): capacità di identificare e descrivere le proprie emozioni",
     "spectrum_low": "bassa chiarezza", "spectrum_high": "alta chiarezza",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "emotional.distress_tolerance", "category": "emotional", "name": "distress_tolerance",
     "description": "Tolleranza al distress (DERS): capacità di funzionare durante stati emotivi intensi",
     "spectrum_low": "bassa tolleranza", "spectrum_high": "alta tolleranza",
     "sensitivity": "alta", "intrusion_base": 0.65},
    {"id": "temporal.delay_discounting", "category": "temporal", "name": "delay_discounting",
     "description": "Sconto temporale: preferenza per ricompense immediate vs differite",
     "spectrum_low": "impulsivo (preferisce ora)", "spectrum_high": "paziente (differisce la ricompensa)",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "meta_cognition.narrative_agency", "category": "meta_cognition", "name": "narrative_agency",
     "description": "Agency narrativa (McAdams): grado in cui si percepisce protagonista della propria vita",
     "spectrum_low": "si sente agito dagli eventi", "spectrum_high": "si sente architetto attivo",
     "sensitivity": "media", "intrusion_base": 0.45},
    {"id": "values.schwartz_self_enhancement", "category": "values", "name": "schwartz_self_enhancement",
     "description": "Valori Schwartz: auto-trascendenza vs auto-affermazione",
     "spectrum_low": "auto-trascendenza (benevolenza, universalismo)", "spectrum_high": "auto-affermazione (potere, successo)",
     "sensitivity": "media", "intrusion_base": 0.45},

    # v3.0 facets — Tier 1 cognitive constructs
    {"id": "language.verbal_complexity", "category": "language", "name": "verbal_complexity",
     "description": "Complessita' verbale: ricchezza vocabolario, struttura frasi, sofisticazione linguistica",
     "spectrum_low": "semplice/diretto", "spectrum_high": "complesso/elaborato",
     "sensitivity": "bassa", "intrusion_base": 0.20},
    {"id": "emotional.appraisal_agency", "category": "emotional", "name": "appraisal_agency",
     "description": "Attribuzione causale nelle situazioni emotive: cause esterne vs responsabilita' personale",
     "spectrum_low": "attribuzione esterna", "spectrum_high": "attribuzione interna",
     "sensitivity": "media", "intrusion_base": 0.25},
    {"id": "emotional.coping_appraisal", "category": "emotional", "name": "coping_appraisal",
     "description": "Valutazione di coping: senso di controllo e capacita' di fronteggiare le situazioni",
     "spectrum_low": "basso senso di controllo", "spectrum_high": "alto senso di controllo",
     "sensitivity": "media", "intrusion_base": 0.25},
    {"id": "cognitive.mental_model_complexity", "category": "cognitive", "name": "mental_model_complexity",
     "description": "Complessita' dei modelli mentali: quanto sono esaustivi vs minimali i modelli interni",
     "spectrum_low": "modelli minimali", "spectrum_high": "modelli esaustivi",
     "sensitivity": "bassa", "intrusion_base": 0.20},
    {"id": "cognitive.system1_dominance", "category": "cognitive", "name": "system1_dominance",
     "description": "Dominanza Sistema 1: quanto prevale il pensiero intuitivo/rapido vs deliberato/analitico",
     "spectrum_low": "deliberato/analitico", "spectrum_high": "intuitivo/rapido",
     "sensitivity": "bassa", "intrusion_base": 0.20},
    {"id": "cognitive.construct_complexity", "category": "cognitive", "name": "construct_complexity",
     "description": "Complessita' del sistema di costrutti personali: differenziazione e flessibilita' delle dimensioni valutative",
     "spectrum_low": "pochi costrutti rigidi", "spectrum_high": "molti costrutti flessibili",
     "sensitivity": "bassa", "intrusion_base": 0.20},
]

# Non-linear facets (use textual evidence, not numeric position)
NON_LINEAR_FACETS = {"values.core_values", "aesthetic.music_taste", "meta_cognition.cognitive_biases"}


def categorize_hour(hour: int) -> str:
    """Map hour of day to time_context label."""
    if 5 <= hour < 12:
        return "morning"
    elif 12 <= hour < 17:
        return "afternoon"
    elif 17 <= hour < 21:
        return "evening"
    else:
        return "night"


# ── DB connection ───────────────────────────────────────────────────────────

def get_db(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _apply_half_life_defaults(conn: sqlite3.Connection) -> None:
    """Set per-category half_life_days defaults (IMP-01)."""
    rules = [
        ("aesthetic.%",         120),
        ("values.%",             90),
        ("relational.attachment_%", 60),
        ("cognitive.%",          30),
        ("temporal.deadline_%",  21),
        ("emotional.stress_%",    7),
    ]
    for pattern, days in rules:
        conn.execute(
            "UPDATE facets SET half_life_days=? WHERE id LIKE ? AND half_life_days=14",
            (days, pattern),
        )


def _seed_facet_anchors(conn: sqlite3.Connection) -> None:
    """Seed IPIP Big Five anchor mappings for inter-subject calibration (IMP-17)."""
    # Mappings derived from §31.3 of the whitepaper: facet_id → IPIP subscale, direction, pop_mean, pop_sd
    # Population norms from IPIP convenience sample (N≈2000, online self-report)
    anchors = [
        # facet_id, ipip_subscale, direction, pop_mean, pop_sd
        ("cognitive.analytical_approach",       "IPIP-O-Ideas",          "positive", 3.7, 0.9),
        ("cognitive.risk_tolerance",            "IPIP-O-Adventurousness", "positive", 3.2, 1.0),
        ("cognitive.learning_style",            "IPIP-O-Intellect",      "positive", 3.8, 0.9),
        ("emotional.emotional_expression",      "IPIP-E-Cheerfulness",   "positive", 3.5, 0.9),
        ("emotional.resilience_pattern",        "IPIP-N-Vulnerability",  "negative", 2.8, 0.9),
        ("emotional.frustration_triggers",      "IPIP-N-Anger",          "negative", 2.9, 0.9),
        ("emotional.stress_response",           "IPIP-N-Anxiety",        "negative", 3.1, 1.0),
        ("communication.directness",            "IPIP-A-Straightforwardness", "negative", 3.6, 0.9),
        ("communication.conflict_style",        "IPIP-A-Compliance",     "negative", 3.3, 0.8),
        ("relational.trust_formation",          "IPIP-A-Trust",          "positive", 3.5, 0.9),
        ("relational.social_energy",            "IPIP-E-Gregariousness", "positive", 3.0, 1.0),
        ("relational.boundary_style",           "IPIP-A-Agreeableness",  "positive", 3.6, 0.8),
        ("values.autonomy_importance",          "IPIP-C-SelfEfficacy",   "positive", 3.8, 0.8),
        ("temporal.deadline_behavior",          "IPIP-C-OrderlyDutifulness", "positive", 3.5, 0.9),
        ("temporal.planning_horizon",           "IPIP-C-Deliberateness", "positive", 3.4, 0.9),
        ("meta_cognition.self_awareness",       "IPIP-N-SelfConsciousness", "negative", 2.9, 0.9),
        ("meta_cognition.growth_mindset",       "IPIP-O-Openness",       "positive", 3.7, 0.8),
        ("meta_cognition.uncertainty_tolerance","IPIP-N-Immoderation",   "negative", 2.7, 0.9),
    ]
    for row in anchors:
        conn.execute(
            "INSERT OR IGNORE INTO facet_anchors (facet_id, ipip_subscale, direction, population_mean, population_sd) "
            "VALUES (?,?,?,?,?)",
            row,
        )


def init_db(path: Path | None = None) -> None:
    conn = get_db(path)
    try:
        conn.executescript(SCHEMA_SQL)

        # Seed facets (upsert)
        for f in FACETS:
            conn.execute(
                """INSERT INTO facets (id, category, name, description, spectrum_low, spectrum_high, sensitivity, intrusion_base)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     description=excluded.description,
                     spectrum_low=excluded.spectrum_low,
                     spectrum_high=excluded.spectrum_high,
                     sensitivity=excluded.sensitivity,
                     intrusion_base=excluded.intrusion_base""",
                (f["id"], f["category"], f["name"], f["description"],
                 f["spectrum_low"], f["spectrum_high"], f["sensitivity"], f["intrusion_base"]),
            )

        # Seed empty traits for each facet
        for f in FACETS:
            conn.execute(
                """INSERT OR IGNORE INTO traits (facet_id, confidence, observation_count)
                   VALUES (?, 0.0, 0)""",
                (f["id"],),
            )

        # Migration: add followup_sent_at column to checkin_exchanges if missing
        checkin_cols = {r[1] for r in conn.execute("PRAGMA table_info(checkin_exchanges)").fetchall()}
        if "followup_sent_at" not in checkin_cols:
            conn.execute("ALTER TABLE checkin_exchanges ADD COLUMN followup_sent_at TEXT")

        # Migration: add status column to traits if missing
        trait_cols = {r[1] for r in conn.execute("PRAGMA table_info(traits)").fetchall()}
        if "status" not in trait_cols:
            conn.execute("ALTER TABLE traits ADD COLUMN status TEXT DEFAULT 'insufficient_data'")

        # Migration: add context_metadata column to observations if missing
        obs_cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)").fetchall()}
        if "context_metadata" not in obs_cols:
            conn.execute("ALTER TABLE observations ADD COLUMN context_metadata TEXT")

        # Migration: add half_life_days column to facets if missing (IMP-01)
        facet_cols = {r[1] for r in conn.execute("PRAGMA table_info(facets)").fetchall()}
        if "half_life_days" not in facet_cols:
            conn.execute("ALTER TABLE facets ADD COLUMN half_life_days INTEGER DEFAULT 14")
            _apply_half_life_defaults(conn)

        # Migration: add conversation_domain column to observations if missing (IMP-19)
        if "conversation_domain" not in obs_cols:
            conn.execute("ALTER TABLE observations ADD COLUMN conversation_domain TEXT")

        # Migration: add corrections table if missing (IMP-02)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS corrections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                facet_id TEXT NOT NULL REFERENCES facets(id),
                correction_note TEXT NOT NULL,
                created_at TEXT NOT NULL,
                applied_at TEXT
            )
        """)

        # Migration: add domain_probe_schedule table if missing (IMP-16)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS domain_probe_schedule (
                domain TEXT PRIMARY KEY,
                facet_ids_json TEXT NOT NULL,
                last_probe_at TEXT,
                probe_interval_days INTEGER NOT NULL DEFAULT 45
            )
        """)

        # Migration: add caps_predictions table if missing (IMP-07)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS caps_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signature_id INTEGER NOT NULL REFERENCES caps_signatures(id),
                prediction_text TEXT NOT NULL,
                pattern_regex TEXT,
                confirmations INTEGER DEFAULT 0,
                disconfirmations INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                UNIQUE(signature_id, prediction_text)
            )
        """)

        # Migration: add facet_anchors table if missing (IMP-17)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS facet_anchors (
                facet_id TEXT NOT NULL REFERENCES facets(id),
                ipip_subscale TEXT NOT NULL,
                direction TEXT NOT NULL,
                population_mean REAL NOT NULL,
                population_sd REAL NOT NULL,
                PRIMARY KEY (facet_id, ipip_subscale)
            )
        """)
        # Seed IPIP anchor mappings if empty (IMP-17)
        if conn.execute("SELECT COUNT(*) FROM facet_anchors").fetchone()[0] == 0:
            _seed_facet_anchors(conn)

        # Migration: add benchmark_sessions table if missing (IMP-15)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS benchmark_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument TEXT NOT NULL,
                item_id TEXT NOT NULL,
                response INTEGER NOT NULL,
                session_date TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(instrument, item_id, session_date)
            )
        """)

        # Migration: add implicit_motives table if missing (IMP-12)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS implicit_motives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                n_ach REAL NOT NULL,
                n_aff REAL NOT NULL,
                n_pow REAL NOT NULL,
                sample_size INTEGER NOT NULL,
                evidence TEXT,
                computed_at TEXT NOT NULL
            )
        """)

        # Migration: add consensus column to schemas table if missing (IMP-10)
        schema_cols = {r[1] for r in conn.execute("PRAGMA table_info(schemas)").fetchall()}
        if schema_cols and "consensus" not in schema_cols:
            conn.execute("ALTER TABLE schemas ADD COLUMN consensus INTEGER DEFAULT 1")

        conn.commit()
        # Log to stderr to avoid polluting stdout when used as CLI
        import sys as _sys
        print(json.dumps({"level": "info", "script": SCRIPT, "event": "db_initialized",
                          "facets": len(FACETS), "path": str(path or DB_PATH)}), file=_sys.stderr)
    finally:
        conn.close()


# ── Module API ──────────────────────────────────────────────────────────────

def get_facet(facet_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    c = conn or get_db()
    try:
        row = c.execute("SELECT * FROM facets WHERE id = ?", (facet_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if conn is None:
            c.close()


def get_all_facets(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        rows = c.execute("SELECT * FROM facets ORDER BY category, name").fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def get_weakest_facets(n: int = 5, min_gap_hours: float = 24.0,
                       conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        rows = c.execute(
            """SELECT f.*, t.confidence, t.observation_count, t.last_observation_at,
                      ce.last_asked
               FROM facets f
               JOIN traits t ON f.id = t.facet_id
               LEFT JOIN (
                   SELECT facet_id, MAX(asked_at) as last_asked
                   FROM checkin_exchanges
                   GROUP BY facet_id
               ) ce ON f.id = ce.facet_id
               ORDER BY t.confidence ASC, t.observation_count ASC
               LIMIT ?""",
            (n * 3,),  # fetch extra to filter by gap
        ).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            r = dict(row)
            last_asked = r.get("last_asked")
            if last_asked:
                try:
                    asked_dt = datetime.fromisoformat(last_asked.replace("Z", "+00:00"))
                    now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
                    hours_since = (now_dt - asked_dt).total_seconds() / 3600.0
                    if hours_since < min_gap_hours:
                        continue
                except (ValueError, TypeError):
                    pass
            results.append(r)
            if len(results) >= n:
                break
        return results
    finally:
        if conn is None:
            c.close()


def add_observation(
    facet_id: str,
    source_type: str,
    source_ref: str,
    content: str,
    extracted_signal: str,
    signal_strength: float = 0.5,
    signal_position: float | None = None,
    context: str | None = None,
    context_metadata: dict[str, Any] | None = None,
    conn: sqlite3.Connection | None = None,
) -> int | None:
    c = conn or get_db()
    close = conn is None
    ctx_meta_json = json.dumps(context_metadata, ensure_ascii=False) if context_metadata else None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            cur = c.execute(
                """INSERT INTO observations
                   (facet_id, source_type, source_ref, content, extracted_signal,
                    signal_strength, signal_position, context, context_metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(facet_id, source_ref) DO NOTHING""",
                (facet_id, source_type, source_ref, content, extracted_signal,
                 signal_strength, signal_position, context, ctx_meta_json, now_iso),
            )
        except sqlite3.IntegrityError:
            # FK constraint failed — invalid facet_id from LLM
            return None
        if cur.rowcount > 0:
            # Update trait observation count
            c.execute(
                """UPDATE traits SET
                     observation_count = observation_count + 1,
                     last_observation_at = ?
                   WHERE facet_id = ?""",
                (now_iso, facet_id),
            )
            if close:
                c.commit()
            return cur.lastrowid
        return None
    finally:
        if close:
            c.close()


def get_trait(facet_id: str, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    c = conn or get_db()
    try:
        row = c.execute("SELECT * FROM traits WHERE facet_id = ?", (facet_id,)).fetchone()
        return dict(row) if row else None
    finally:
        if conn is None:
            c.close()


def get_all_traits(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        rows = c.execute(
            """SELECT t.*, f.category, f.name, f.description, f.spectrum_low, f.spectrum_high
               FROM traits t JOIN facets f ON t.facet_id = f.id
               ORDER BY f.category, f.name"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def update_trait(
    facet_id: str,
    value_position: float | None = None,
    confidence: float | None = None,
    notes: str | None = None,
    status: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        sets: list[str] = ["last_synthesis_at = ?"]
        vals: list[Any] = [now_iso]
        if value_position is not None:
            sets.append("value_position = ?")
            vals.append(value_position)
        if confidence is not None:
            sets.append("confidence = ?")
            vals.append(confidence)
        if notes is not None:
            sets.append("notes = ?")
            vals.append(notes)
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        vals.append(facet_id)
        c.execute(f"UPDATE traits SET {', '.join(sets)} WHERE facet_id = ?", vals)
        if close:
            c.commit()
    finally:
        if close:
            c.close()


def record_checkin(
    facet_id: str,
    question_text: str,
    message_id: str = "",
    conn: sqlite3.Connection | None = None,
) -> int:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        cur = c.execute(
            """INSERT INTO checkin_exchanges (facet_id, question_text, asked_at, message_id)
               VALUES (?, ?, ?, ?)""",
            (facet_id, question_text, now_iso, message_id or ""),
        )
        if close:
            c.commit()
        return cur.lastrowid
    finally:
        if close:
            c.close()


def get_pending_checkins(hours: float = 4.0,
                         conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        rows = c.execute(
            """SELECT * FROM checkin_exchanges
               WHERE reply_text IS NULL
                 AND asked_at >= datetime('now', ?)
               ORDER BY asked_at DESC""",
            (f"-{int(hours)} hours",),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def capture_reply(
    exchange_id: int,
    content: str,
    conn: sqlite3.Connection | None = None,
) -> None:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        c.execute(
            """UPDATE checkin_exchanges SET reply_text = ?, reply_captured_at = ?
               WHERE id = ?""",
            (content, now_iso, exchange_id),
        )
        if close:
            c.commit()
    finally:
        if close:
            c.close()


def ingest_inbox_line(line: dict[str, Any],
                      conn: sqlite3.Connection | None = None) -> bool:
    # Defensive sender check — only ingest messages from the subject
    sender = str(line.get("from", line.get("from_id", "")))
    # Strip platform prefix (e.g. "telegram:demo-subject" → "demo-subject")
    sender_id = sender.rsplit(":", 1)[-1] if ":" in sender else sender
    if sender_id and sender_id != "demo-subject":
        return False

    c = conn or get_db()
    close = conn is None
    try:
        # Generate fallback message_id from content hash if missing
        msg_id = line.get("message_id", "")
        if not msg_id:
            import hashlib
            content = line.get("content", "")
            received = line.get("received_at", "")
            msg_id = "gen-" + hashlib.sha256(f"{content}:{received}".encode()).hexdigest()[:16]

        cur = c.execute(
            """INSERT INTO inbox (message_id, from_id, content, channel_id, received_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id) DO NOTHING""",
            (msg_id,
             line.get("from", line.get("from_id", "")),
             line.get("content", ""),
             line.get("channel_id", ""),
             line.get("received_at", datetime.now(timezone.utc).isoformat())),
        )
        if close:
            c.commit()
        return cur.rowcount > 0
    finally:
        if close:
            c.close()


def get_pending_inbox(limit: int = 20,
                      conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        rows = c.execute(
            "SELECT * FROM inbox WHERE processed = 0 ORDER BY received_at ASC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def mark_processed(inbox_ids: list[int],
                   conn: sqlite3.Connection | None = None) -> None:
    if not inbox_ids:
        return
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" for _ in inbox_ids)
        c.execute(
            f"UPDATE inbox SET processed = 1, processed_at = ? WHERE id IN ({placeholders})",
            [now_iso] + inbox_ids,
        )
        if close:
            c.commit()
    finally:
        if close:
            c.close()


def get_observations_for_facet(facet_id: str, since: str | None = None,
                               conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        if since:
            rows = c.execute(
                """SELECT * FROM observations WHERE facet_id = ? AND created_at >= ?
                   ORDER BY created_at DESC""",
                (facet_id, since),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM observations WHERE facet_id = ? ORDER BY created_at DESC",
                (facet_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def get_hypotheses(status: str | None = None,
                   conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        if status:
            rows = c.execute(
                "SELECT * FROM hypotheses WHERE status = ? ORDER BY confidence DESC",
                (status,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM hypotheses ORDER BY confidence DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn is None:
            c.close()


def upsert_hypothesis(
    hypothesis: str,
    status: str = "unverified",
    supporting: list[int] | None = None,
    contradicting: list[int] | None = None,
    confidence: float = 0.3,
    hypothesis_id: int | None = None,
    conn: sqlite3.Connection | None = None,
) -> int:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        sup_json = json.dumps(supporting) if supporting else "[]"
        con_json = json.dumps(contradicting) if contradicting else "[]"
        if hypothesis_id:
            c.execute(
                """UPDATE hypotheses SET hypothesis = ?, status = ?,
                     supporting_observations = ?, contradicting_observations = ?,
                     confidence = ?, updated_at = ?
                   WHERE id = ?""",
                (hypothesis, status, sup_json, con_json, confidence, now_iso, hypothesis_id),
            )
            row_id = hypothesis_id
        else:
            cur = c.execute(
                """INSERT INTO hypotheses
                     (hypothesis, status, supporting_observations, contradicting_observations,
                      confidence, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (hypothesis, status, sup_json, con_json, confidence, now_iso, now_iso),
            )
            row_id = cur.lastrowid
        if close:
            c.commit()
        return row_id
    finally:
        if close:
            c.close()


def save_snapshot(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        traits = c.execute("SELECT * FROM traits").fetchall()
        traits_list = [dict(t) for t in traits]

        total_obs = sum(t["observation_count"] for t in traits_list)
        confidences = [t["confidence"] for t in traits_list if t["confidence"] > 0]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        covered = sum(1 for t in traits_list if t["confidence"] > 0.3)
        coverage_pct = (covered / len(traits_list) * 100) if traits_list else 0.0

        snapshot_data = json.dumps(traits_list, ensure_ascii=False)
        c.execute(
            """INSERT INTO model_snapshots
                 (snapshot_at, total_observations, avg_confidence, coverage_pct, snapshot_data)
               VALUES (?, ?, ?, ?, ?)""",
            (now_iso, total_obs, round(avg_conf, 4), round(coverage_pct, 2), snapshot_data),
        )
        if close:
            c.commit()

        summary = {
            "snapshot_at": now_iso,
            "total_observations": total_obs,
            "avg_confidence": round(avg_conf, 4),
            "coverage_pct": round(coverage_pct, 2),
            "covered_facets": covered,
            "total_facets": len(traits_list),
        }
        info(SCRIPT, "snapshot_saved", **summary)
        return summary
    finally:
        if close:
            c.close()


def get_model_summary(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    c = conn or get_db()
    try:
        traits = c.execute(
            """SELECT f.category, t.confidence, t.observation_count
               FROM traits t JOIN facets f ON t.facet_id = f.id"""
        ).fetchall()

        by_category: dict[str, dict[str, Any]] = {}
        total_obs = 0
        confidences: list[float] = []
        covered = 0

        for row in traits:
            cat = row["category"]
            if cat not in by_category:
                by_category[cat] = {"count": 0, "avg_confidence": 0.0, "total_obs": 0}
            by_category[cat]["count"] += 1
            by_category[cat]["total_obs"] += row["observation_count"]
            by_category[cat]["avg_confidence"] += row["confidence"]
            total_obs += row["observation_count"]
            if row["confidence"] > 0:
                confidences.append(row["confidence"])
            if row["confidence"] > 0.3:
                covered += 1

        for cat in by_category:
            n = by_category[cat]["count"]
            if n > 0:
                by_category[cat]["avg_confidence"] = round(by_category[cat]["avg_confidence"] / n, 4)

        return {
            "total_facets": len(traits),
            "covered_facets": covered,
            "coverage_pct": round(covered / len(traits) * 100, 2) if traits else 0.0,
            "total_observations": total_obs,
            "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0.0,
            "by_category": by_category,
        }
    finally:
        if conn is None:
            c.close()


# ── Context Clusters ───────────────────────────────────────────────────────

def upsert_context_cluster(
    facet_id: str,
    cluster_label: str,
    context_filter: dict[str, str],
    value_position: float,
    confidence: float,
    observation_count: int,
    weight: float,
    conn: sqlite3.Connection | None = None,
) -> None:
    c = conn or get_db()
    close = conn is None
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        c.execute(
            """INSERT INTO context_clusters
                 (facet_id, cluster_label, context_filter, value_position,
                  confidence, observation_count, weight, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(facet_id, cluster_label) DO UPDATE SET
                 context_filter=excluded.context_filter,
                 value_position=excluded.value_position,
                 confidence=excluded.confidence,
                 observation_count=excluded.observation_count,
                 weight=excluded.weight,
                 updated_at=excluded.updated_at""",
            (facet_id, cluster_label, json.dumps(context_filter, ensure_ascii=False),
             value_position, confidence, observation_count, weight, now_iso, now_iso),
        )
        if close:
            c.commit()
    finally:
        if close:
            c.close()


def get_context_clusters(facet_id: str | None = None,
                         conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        if facet_id:
            rows = c.execute(
                "SELECT * FROM context_clusters WHERE facet_id = ? ORDER BY weight DESC",
                (facet_id,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM context_clusters ORDER BY facet_id, weight DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        if conn is None:
            c.close()


def delete_context_clusters(facet_id: str, conn: sqlite3.Connection | None = None) -> None:
    c = conn or get_db()
    close = conn is None
    try:
        c.execute("DELETE FROM context_clusters WHERE facet_id = ?", (facet_id,))
        if close:
            c.commit()
    finally:
        if close:
            c.close()


# ── Memory Layer Queries ────────────────────────────────────────────────────

def get_entities(entity_type: str | None = None, limit: int = 50,
                 conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        if entity_type:
            rows = c.execute(
                "SELECT * FROM entities WHERE entity_type = ? ORDER BY mention_count DESC LIMIT ?",
                (entity_type, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM entities ORDER BY mention_count DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []  # table doesn't exist yet
    finally:
        if conn is None:
            c.close()


def get_episodes(episode_type: str | None = None, active_only: bool = True,
                 limit: int = 50, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        conditions = []
        params: list[Any] = []
        if episode_type:
            conditions.append("episode_type = ?")
            params.append(episode_type)
        if active_only:
            conditions.append("active = 1")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = c.execute(
            f"SELECT * FROM episodes {where} ORDER BY extracted_at DESC LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        if conn is None:
            c.close()


def get_decisions(domain: str | None = None, limit: int = 50,
                  conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    c = conn or get_db()
    try:
        if domain:
            rows = c.execute(
                "SELECT * FROM decisions WHERE domain = ? ORDER BY extracted_at DESC LIMIT ?",
                (domain, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM decisions ORDER BY extracted_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        if conn is None:
            c.close()


def get_full_memory_summary(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Combined personality model + memory layer summary."""
    c = conn or get_db()
    try:
        model = get_model_summary(c)

        # Memory layer counts (safe if tables don't exist yet)
        memory: dict[str, Any] = {}
        try:
            entity_rows = c.execute(
                "SELECT entity_type, COUNT(*) as cnt FROM entities GROUP BY entity_type"
            ).fetchall()
            memory["entities"] = {r["entity_type"]: r["cnt"] for r in entity_rows}

            episode_rows = c.execute(
                "SELECT episode_type, COUNT(*) as cnt FROM episodes WHERE active=1 GROUP BY episode_type"
            ).fetchall()
            memory["episodes"] = {r["episode_type"]: r["cnt"] for r in episode_rows}

            dec_row = c.execute("SELECT COUNT(*) as cnt FROM decisions").fetchone()
            memory["decisions"] = dec_row["cnt"] if dec_row else 0

            rel_row = c.execute("SELECT COUNT(*) as cnt FROM entity_relations").fetchone()
            memory["relations"] = rel_row["cnt"] if rel_row else 0

            met_row = c.execute(
                "SELECT COUNT(DISTINCT platform || ':' || chat_id) as cnt FROM communication_metrics"
            ).fetchone()
            memory["chats_with_metrics"] = met_row["cnt"] if met_row else 0
        except sqlite3.OperationalError:
            memory = {"note": "memory tables not initialized"}

        model["memory"] = memory
        return model
    finally:
        if conn is None:
            c.close()


def apply_pending_corrections(conn: sqlite3.Connection) -> int:
    """Apply unapplied corrections as high-strength counter-observations (IMP-02).

    Each correction becomes a strength=1.0 observation and is marked applied.
    Returns number of corrections applied.
    """
    rows = conn.execute(
        "SELECT id, facet_id, correction_note, created_at FROM corrections WHERE applied_at IS NULL"
    ).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    applied = 0
    for row in rows:
        facet_id = row["facet_id"]
        note = row["correction_note"]
        # Determine implied direction: if note contains 'not', 'low', 'bassa' etc. → 0.1
        # Otherwise use midpoint 0.5 as neutral correction (synthesizer will re-weight).
        # This is intentionally conservative: the note is stored for human review.
        add_observation(
            facet_id=facet_id,
            source_type="subject_correction",
            source_ref=f"correction:{row['id']}",
            content=note,
            signal_strength=1.0,
            context="Manual subject correction — overrides inferred signal",
            conn=conn,
        )
        conn.execute(
            "UPDATE corrections SET applied_at=? WHERE id=?",
            (now, row["id"]),
        )
        applied += 1
    return applied


# ── CLI ─────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="Soulkiller DB management")
    parser.add_argument("--init", action="store_true", help="Initialize DB + seed facets")
    parser.add_argument("--db", default=str(DB_PATH), help="Database path")

    sub = parser.add_subparsers(dest="command")

    rc = sub.add_parser("record-checkin", help="Record a check-in exchange")
    rc.add_argument("--facet", required=True, help="Facet ID")
    rc.add_argument("--question", required=True, help="Question text")
    rc.add_argument("--message-id", default="", help="Telegram message ID")

    cr = sub.add_parser("capture-reply", help="Capture a reply to a check-in exchange")
    cr.add_argument("--exchange-id", required=True, type=int, help="Exchange ID")
    cr.add_argument("--reply", required=True, help="Reply text")

    summary_cmd = sub.add_parser("summary", help="Print model summary")

    trace_cmd = sub.add_parser("trace", help="Trace observations for a facet (show evidence chain)")
    trace_cmd.add_argument("--facet", required=True, help="Facet ID to trace")
    trace_cmd.add_argument("--json", action="store_true", help="Output as JSON")

    clusters_cmd = sub.add_parser("clusters", help="Show contextual clusters for a facet")
    clusters_cmd.add_argument("--facet", required=True, help="Facet ID")
    clusters_cmd.add_argument("--json", action="store_true", help="Output as JSON")

    correct_cmd = sub.add_parser("correct", help="Inject a subject correction for a facet (IMP-02)")
    correct_cmd.add_argument("--facet", required=True, help="Facet ID to correct")
    correct_cmd.add_argument("--note", required=True, help="Correction note describing the correct state")

    log_cmd = sub.add_parser("log", help="Log an offline observation directly (IMP-18)")
    log_cmd.add_argument("--facet", required=True, help="Facet ID")
    log_cmd.add_argument("--text", required=True, help="Observation text")
    log_cmd.add_argument("--strength", type=float, default=0.5, help="Signal strength (default 0.5)")
    log_cmd.add_argument("--context", default="offline", help="Context label (default: offline)")

    args = parser.parse_args()
    db_path = Path(args.db)

    if args.init:
        init_db(db_path)
        summary = get_model_summary(get_db(db_path))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "record-checkin":
        conn = get_db(db_path)
        try:
            row_id = record_checkin(args.facet, args.question, args.message_id, conn)
            conn.commit()
            # Write signal file for hook-based follow-up
            signal_path = Path(__file__).resolve().parents[1] / "soulkiller" / "pending-checkin.json"
            signal_path.write_text(json.dumps({
                "exchange_id": row_id,
                "facet_id": args.facet,
                "question_text": args.question,
                "asked_at": datetime.now(timezone.utc).isoformat(),
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            # Log to stderr to keep stdout clean JSON
            import sys as _sys
            print(json.dumps({"level": "info", "script": SCRIPT, "event": "checkin_recorded",
                              "facet": args.facet, "exchange_id": row_id}), file=_sys.stderr)
            print(json.dumps({"ok": True, "exchange_id": row_id}))
        finally:
            conn.close()
        return 0

    if args.command == "capture-reply":
        conn = get_db(db_path)
        try:
            capture_reply(args.exchange_id, args.reply, conn)
            # Also create an observation from the reply
            row = conn.execute(
                "SELECT facet_id, question_text FROM checkin_exchanges WHERE id = ?",
                (args.exchange_id,),
            ).fetchone()
            if row and row["facet_id"]:
                add_observation(
                    facet_id=row["facet_id"],
                    source_type="checkin_reply",
                    source_ref=f"checkin:{args.exchange_id}",
                    content=args.reply[:500],
                    extracted_signal=f"Direct reply to check-in about {row['facet_id']}",
                    signal_strength=0.7,
                    context=f"Reply to: {row['question_text'][:200]}",
                    conn=conn,
                )
            conn.commit()
            import sys as _sys
            print(json.dumps({"level": "info", "script": SCRIPT, "event": "reply_captured",
                              "exchange_id": args.exchange_id}), file=_sys.stderr)
            print(json.dumps({"ok": True, "exchange_id": args.exchange_id}))
        finally:
            conn.close()
        return 0

    if args.command == "summary":
        summary = get_model_summary(get_db(db_path))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "trace":
        conn = get_db(db_path)
        try:
            facet = get_facet(args.facet, conn)
            if not facet:
                print(json.dumps({"error": f"Facet '{args.facet}' not found"}))
                return 1

            observations = get_observations_for_facet(args.facet, conn=conn)
            trait = get_trait(args.facet, conn)

            if args.json:
                output = {
                    "facet": facet,
                    "trait": trait,
                    "observations": observations,
                    "total_observations": len(observations),
                }
                print(json.dumps(output, ensure_ascii=False, indent=2))
            else:
                # Human-readable trace
                spectrum = ""
                if facet.get("spectrum_low") and facet.get("spectrum_high"):
                    spectrum = f"{facet['spectrum_low']} ↔ {facet['spectrum_high']}"

                trait_status = (trait or {}).get("status", "insufficient_data")
                print(f"\n{'='*60}")
                print(f"FACET: {facet['id']}")
                print(f"Categoria: {facet['category']}")
                print(f"Spettro: {spectrum}")
                print(f"Status: {trait_status}")
                print(f"{'='*60}")

                if trait and trait.get('confidence', 0) > 0:
                    print(f"\n📊 TRAIT ATTUALE:")
                    print(f"   Confidenza: {trait['confidence']:.2f}")
                    print(f"   Osservazioni: {trait['observation_count']}")
                    if trait.get('value_position') is not None:
                        pos = trait['value_position']
                        print(f"   Posizione: {pos:.2f} (su scala 0-1)")
                    if trait.get('notes'):
                        print(f"   Note: {trait['notes'][:200]}...")

                print(f"\n📋 OSSERVAZIONI ({len(observations)} totali):")
                if not observations:
                    print("   Nessuna osservazione ancora.")
                else:
                    for i, obs in enumerate(observations, 1):
                        print(f"\n   [{i}] {obs['created_at'][:10]}")
                        print(f"       Fonte: {obs['source_type']} → {obs['source_ref']}")
                        print(f"       Contenuto: {obs['content'][:100]}")
                        print(f"       Segnale estratto: {obs['extracted_signal']}")
                        print(f"       Forza: {obs['signal_strength']:.2f} | Posizione: {obs.get('signal_position', 'N/A')}")
                        # Show context_metadata if present
                        ctx_meta_raw = obs.get("context_metadata")
                        if ctx_meta_raw:
                            try:
                                cm = json.loads(ctx_meta_raw) if isinstance(ctx_meta_raw, str) else ctx_meta_raw
                                parts = []
                                if cm.get("interlocutor_type"):
                                    parts.append(cm["interlocutor_type"])
                                if cm.get("day_of_week"):
                                    parts.append(cm["day_of_week"])
                                if cm.get("hour") is not None:
                                    parts.append(f"{cm['hour']:02d}:00")
                                if cm.get("tone"):
                                    parts.append(f"tono: {cm['tone']}")
                                if parts:
                                    print(f"       Contesto: {', '.join(parts)}")
                            except (json.JSONDecodeError, TypeError):
                                pass

                print(f"\n{'='*60}\n")
            return 0
        finally:
            conn.close()

    if args.command == "clusters":
        conn = get_db(db_path)
        try:
            facet = get_facet(args.facet, conn)
            if not facet:
                print(json.dumps({"error": f"Facet '{args.facet}' not found"}))
                return 1

            clusters = get_context_clusters(args.facet, conn)
            trait = get_trait(args.facet, conn)

            if args.json:
                print(json.dumps({"facet": facet, "trait": trait, "clusters": clusters},
                                 ensure_ascii=False, indent=2))
            else:
                spectrum = ""
                if facet.get("spectrum_low") and facet.get("spectrum_high"):
                    spectrum = f"{facet['spectrum_low']} ↔ {facet['spectrum_high']}"

                print(f"\n{'='*60}")
                print(f"CLUSTERS: {facet['id']}")
                print(f"Spettro: {spectrum}")
                if trait:
                    print(f"Globale: {trait.get('value_position', 'N/A'):.2f} (conf: {trait.get('confidence', 0):.2f}, {trait.get('observation_count', 0)} obs)")
                print(f"{'='*60}")

                if not clusters:
                    print("\nNessun cluster (servono >= 20 obs con context_metadata)")
                else:
                    print(f"\n{'Contesto':<20} {'Posizione':>10} {'Confidenza':>11} {'Osservazioni':>13} {'Peso':>6}")
                    print("-" * 62)
                    for cl in clusters:
                        print(f"{cl['cluster_label']:<20} {cl['value_position']:>10.2f} {cl['confidence']:>11.2f} {cl['observation_count']:>13} {cl['weight']:>5.0%}")
                print()
            return 0
        finally:
            conn.close()

    if args.command == "correct":
        conn = get_db(db_path)
        try:
            facet = get_facet(args.facet, conn)
            if not facet:
                print(json.dumps({"error": f"Facet '{args.facet}' not found"}))
                return 1
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO corrections (facet_id, correction_note, created_at) VALUES (?, ?, ?)",
                (args.facet, args.note, now),
            )
            conn.commit()
            print(json.dumps({"ok": True, "facet": args.facet, "note": args.note}))
        finally:
            conn.close()
        return 0

    if args.command == "log":
        conn = get_db(db_path)
        try:
            facet = get_facet(args.facet, conn)
            if not facet:
                print(json.dumps({"error": f"Facet '{args.facet}' not found"}))
                return 1
            obs_id = add_observation(
                facet_id=args.facet,
                source_type="self_report_offline",
                source_ref=f"offline:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
                content=args.text,
                signal_strength=args.strength,
                context=args.context,
                conn=conn,
            )
            conn.commit()
            print(json.dumps({"ok": True, "facet": args.facet, "observation_id": obs_id}))
        finally:
            conn.close()
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
