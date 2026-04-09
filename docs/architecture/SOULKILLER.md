# Soulkiller: Deep Personality Modeling System

**Version**: 3.0
**Deployed**: 2026-02-28 (v1.0), 2026-03-14 (v2.0, v3.0)
**Location**: `$SOULKILLER_DATA_DIR` (default: `./runtime/`)

---

## 1. What Soulkiller Is

Soulkiller is a personality modeling system that builds a comprehensive, confidence-scored psychological profile of the subject through two complementary channels: active questioning (targeted check-in questions via Telegram) and passive observation (extracting behavioral signals from natural conversations and agent interaction sessions).

The system models personality across 60 facets organized into 8 categories, each represented as a position on a spectrum (e.g., "impulsivo" to "deliberato" for decision speed). Over time, it accumulates observations, synthesizes them into trait scores with confidence levels, and generates cross-facet hypotheses about behavioral patterns.

### What it replaced

The previous system used 5 generic topics (`priorita_reali`, `energia_stress`, `relazione_calibrazione`, `routine_benessere`, `stile_decisionale`) scored by a Topic Gap Score (TGS) formula, and `demo_profile.json` held 12 shallow operational records. Soulkiller replaces the 5-topic scorer with a 60-facet model while maintaining full backward compatibility — the old scorer remains as a fallback, and the profile JSON format is preserved and extended.

---

## 2. Architecture

The system has eight components connected in a pipeline:

```
Telegram messages the configured subject
       |
       +---> [Hook: soulkiller-capture] (message:received + message:sent)
       |         Appends to inbox.jsonl,
       |         detects check-in replies → triggers follow-up agent,
       |         tracks delivery success/failure
       |
       +---> [Hook: soulkiller-bootstrap] (agent:bootstrap)
       |         Injects PROFILE.md into every agent session so all
       |         agents have personality awareness
       |
       +---> [Cron: soulkiller:extract] every 2h
       |         Ingests inbox.jsonl into SQLite, calls LLM to extract
       |         personality signals, inserts observations
       |
       +---> [Cron: soulkiller:checkin] every 30min 9-22h
       |         Uses soulkiller_question_engine to select a facet,
       |         generates a natural question, sends via Telegram,
       |         records the exchange + writes pending-checkin.json signal
       |
       +---> [Cron: soulkiller:passive-scan] every 6h
       |         Scans agent session transcripts for behavioral
       |         meta-signals (how the subject interacts, not what he says)
       |
       +---> [Cron: soulkiller:synthesize] daily 03:00
       |         Consolidates observations into trait scores,
       |         updates confidence, generates hypotheses via LLM
       |
       +---> [Cron: soulkiller:profile-sync] daily 03:30
                 Syncs trait model to demo_profile.json,
                 generates human-readable PROFILE.md
```

### Data flow in detail

1. **Capture** (`soulkiller-capture` hook, `message:received`): Every Telegram message the configured subject (user ID demo-subject) is captured and appended as a JSON line to `inbox.jsonl`. If a `pending-checkin.json` signal file exists (written by the check-in cron), the hook triggers a follow-up agent conversation that acknowledges the reply and records it in the DB.

2. **Delivery tracking** (`soulkiller-capture` hook, `message:sent`): Outbound messages the configured subject are logged to `soulkiller/delivery.jsonl` with success/failure status and message preview.

3. **Bootstrap injection** (`soulkiller-bootstrap` hook, `agent:bootstrap`): On every agent session start, this hook reads `PROFILE.md` and injects it as a virtual `PERSONALITY_MODEL.md` bootstrap file. This gives all configured agents personality awareness without manual context injection. Sub-agents and internal soulkiller crons are excluded.

4. **Extraction** (`soulkiller:extract` cron, every 2h): Reads new lines from `inbox.jsonl`, inserts them into the SQLite `inbox` table (deduplicating by `message_id`), then batches up to 20 unprocessed messages into LLM calls. The LLM analyzes each message for personality signals — what it reveals about cognitive style, emotional tendencies, communication preferences, etc. Extracted signals become `observations` in the database. The extractor also correlates incoming messages with pending check-in questions to capture replies. LLM calls have a 90-second timeout.

5. **Active questioning** (`soulkiller:checkin` cron, every 30min 9-22h): The question engine scores all 60 facets and selects the one with the highest Facet Gap Score. A cron agent generates a natural-sounding question targeting that facet and sends it via Telegram. The exchange is recorded in `checkin_exchanges` and a `pending-checkin.json` signal file is written for the follow-up hook.

6. **Passive observation** (`soulkiller:passive-scan` cron, every 6h): Scans recent relational-agent session transcripts. By default it reads `agents/<relational-agent>/sessions/*.jsonl`, but the target session roots can be overridden via `SOULKILLER_RELATIONAL_AGENT_IDS` (comma-separated agent ids). Extracts user messages and behavioral patterns (how the subject responds to agent suggestions, what he accepts/rejects, emotional markers). Sessions larger than 3MB are skipped, max 10 messages per session, max 20 messages per run, with a 4-minute hard time cap and 90-second per-call LLM timeout.

7. **Synthesis** (`soulkiller:synthesize` cron, daily 03:00): Processes all observations for each facet. Computes a weighted average position on each spectrum (accounting for signal strength and recency), calculates a confidence score based on observation count and consistency, and calls the LLM to identify cross-facet hypotheses. A model snapshot is saved for tracking evolution over time. LLM calls have a 120-second timeout.

8. **Profile sync** (`soulkiller:profile-sync` cron, daily 03:30): Maps Soulkiller traits back to `demo_profile.json` categories and generates human-readable `PROFILE.md` summary.

---

## 3. The 54-Facet Taxonomy

Each facet is a dimension of personality modeled as a position on a spectrum between two poles. Three facets (`values.core_values`, `aesthetic.music_taste`, `meta_cognition.cognitive_biases`) are non-linear — they accumulate textual evidence lists instead of a numeric position.

### Cognitive (6 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `cognitive.decision_speed` | impulsivo ... deliberato | media |
| `cognitive.risk_tolerance` | risk-averse ... risk-seeking | media |
| `cognitive.abstraction_level` | concreto ... astratto | bassa |
| `cognitive.information_gathering` | satisficer ... maximizer | bassa |
| `cognitive.analytical_approach` | intuitivo ... sistematico | bassa |
| `cognitive.learning_style` | practice-first ... theory-first | bassa |

### Emotional (8 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `emotional.stress_response` | freeze/evita ... fight/affronta | alta |
| `emotional.emotional_granularity` | generico ... articolato | alta |
| `emotional.resilience_pattern` | recovery lento ... bounce-back rapido | alta |
| `emotional.frustration_triggers` | alta soglia ... bassa soglia | alta |
| `emotional.joy_sources` | strumentale ... intrinseca | media |
| `emotional.emotional_expression` | contenuto ... espressivo | alta |
| `emotional.emotion_clarity` | bassa chiarezza ... alta chiarezza | alta |
| `emotional.distress_tolerance` | bassa tolleranza ... alta tolleranza | alta |

### Communication (6 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `communication.verbosity` | telegrafico ... elaborato | bassa |
| `communication.directness` | diplomatico ... schietto | bassa |
| `communication.humor_type` | serio/raro ... humor frequente | bassa |
| `communication.conflict_style` | evitante ... confrontativo | media |
| `communication.storytelling_tendency` | fattuale/dati ... aneddotico/narrativo | bassa |
| `communication.formality_range` | sempre informale ... adatta al contesto | bassa |

### Relational (9 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `relational.trust_formation` | fiducia lenta ... fiducia veloce | alta |
| `relational.boundary_style` | confini rigidi ... confini flessibili | alta |
| `relational.loyalty_pattern` | lealtà condizionale ... incondizionata | alta |
| `relational.social_energy` | introverso ... estroverso | media |
| `relational.help_seeking` | indipendente ... collaborativo | media |
| `relational.feedback_preference` | critica diretta ... feedback mediato | media |
| `relational.attachment_anxiety` | bassa ansia ... alta ansia | alta |
| `relational.attachment_avoidance` | basso evitamento ... alto evitamento | alta |
| `relational.vulnerability_capacity` | bassa capacità ... alta capacità | alta |

### Values (7 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `values.core_values` | *(non-linear: list of values)* | media |
| `values.fairness_model` | meritocratico ... egualitario | media |
| `values.authority_stance` | rispetta la gerarchia ... sfida l'autorità | media |
| `values.autonomy_importance` | team-oriented ... indipendenza forte | media |
| `values.aesthetic_values` | funzionale/pragmatico ... eleganza/bellezza | bassa |
| `values.work_ethic` | output-oriented ... effort-oriented | media |
| `values.schwartz_self_enhancement` | auto-trascendenza ... auto-affermazione | media |

### Temporal (6 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `temporal.planning_horizon` | vive nel presente ... pianifica a lungo termine | bassa |
| `temporal.routine_attachment` | cerca varietà ... ama la routine | bassa |
| `temporal.deadline_behavior` | last-minute ... finisce in anticipo | bassa |
| `temporal.nostalgia_tendency` | proiettato al futuro ... orientato al passato | media |
| `temporal.patience_threshold` | impaziente ... paziente | media |
| `temporal.delay_discounting` | impulsivo (preferisce ora) ... paziente (differisce) | media |

### Aesthetic (5 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `aesthetic.design_sensibility` | massimalista ... minimalista | bassa |
| `aesthetic.music_taste` | *(non-linear: genres and patterns)* | bassa |
| `aesthetic.media_consumption` | passivo/mainstream ... attivo/di nicchia | bassa |
| `aesthetic.food_preferences` | comfort/abitudinario ... avventuroso | bassa |
| `aesthetic.environment_preference` | caotico/stimolante ... ordinato/minimal | bassa |

### Meta-cognition (7 facets)

| Facet ID | Spectrum | Sensitivity |
|----------|----------|-------------|
| `meta_cognition.self_awareness` | bassa consapevolezza ... alta consapevolezza | media |
| `meta_cognition.growth_mindset` | fixed mindset ... growth mindset | media |
| `meta_cognition.cognitive_biases` | *(non-linear: list of observed biases)* | media |
| `meta_cognition.reflection_habit` | raramente si esamina ... auto-riflessione frequente | media |
| `meta_cognition.change_readiness` | resiste al cambiamento ... abbraccia il cambiamento | media |
| `meta_cognition.uncertainty_tolerance` | bisogno di certezza ... a proprio agio con l'ambiguità | media |
| `meta_cognition.narrative_agency` | si sente agito dagli eventi ... architetto attivo | media |

### Sensitivity levels

Sensitivity controls how intrusive a question about that facet feels:

- **bassa** (intrusion base 0.25): safe to ask anytime — aesthetic preferences, communication style
- **media** (intrusion base 0.45): moderate — cognitive patterns, values, temporal habits
- **alta** (intrusion base 0.65): deeply personal — emotional responses, trust, loyalty, boundaries

The question engine factors sensitivity into its scoring so it avoids asking about high-sensitivity facets during spotlight mode or at bad times of day.

---

## 4. Database Schema

**Location**: `$SOULKILLER_DATA_DIR/soulkiller.db` (SQLite, WAL mode, FK enforcement on)

### Tables

**`facets`** — The 54 personality dimensions (seed data, rarely changes)
- `id` TEXT PK — e.g. `"cognitive.decision_speed"`
- `category`, `name`, `description` — taxonomy metadata
- `spectrum_low`, `spectrum_high` — the two poles (NULL for non-linear facets)
- `sensitivity` — `bassa` / `media` / `alta`
- `intrusion_base` — numeric intrusion cost for question scoring

**`observations`** — Raw signals extracted from messages and sessions
- `facet_id` FK, `source_type` (`passive_chat`, `session_behavioral`, `checkin_reply`)
- `source_ref` — unique identifier for dedup (e.g. `inbox:tg-12345`, `session:abc:42`)
- `content` — the raw text that was analyzed
- `extracted_signal` — LLM's interpretation of what it reveals
- `signal_strength` (0.0-1.0) — LLM's confidence in the observation
- `signal_position` (0.0-1.0) — where on the spectrum (NULL for non-linear facets)
- UNIQUE constraint on `(facet_id, source_ref)` prevents duplicate extraction

**`traits`** — Consolidated scores per facet (one row per facet, always exactly 54 rows)
- `value_position` — weighted average position on the spectrum
- `confidence` (0.0-1.0) — how sure the model is about this trait
- `observation_count` — total observations supporting this trait
- `last_observation_at`, `last_synthesis_at` — temporal tracking
- `notes` — free-form synthesis notes or textual evidence for non-linear facets

**`hypotheses`** — Cross-facet patterns discovered by the synthesizer
- `hypothesis` — natural language description of the pattern
- `status` — `unverified`, `confirmed`, `denied`, `nuanced`
- `supporting_observations`, `contradicting_observations` — JSON arrays of observation IDs
- `confidence` (0.0-1.0)

**`checkin_exchanges`** — Question-reply tracking for active questioning
- `facet_id` — which facet the question targeted
- `question_text` — what was asked
- `reply_text` — the subject's response (NULL until captured)
- `asked_at`, `reply_captured_at` — timestamps
- `message_id` — Telegram message ID for correlation

**`inbox`** — Buffer for captured raw messages from the hook
- `message_id` UNIQUE — deduplication key
- `content`, `from_id`, `channel_id`, `received_at`
- `processed` flag + `processed_at` — tracks extraction status

**`model_snapshots`** — Point-in-time snapshots for tracking model evolution
- `snapshot_data` — full JSON dump of all traits
- `total_observations`, `avg_confidence`, `coverage_pct`

### Indexes

- `idx_observations_facet` on `observations(facet_id)`
- `idx_observations_source` on `observations(source_type)`
- `idx_inbox_processed` on `inbox(processed)`
- `idx_checkin_reply` on `checkin_exchanges(reply_text)`

### Memory Layer Tables (v1.2)

The memory layer extends soulkiller.db with five new tables for episodic memory, communication metrics, and decision tracking.

**`entities`** — People, places, projects, organizations mentioned by the subject
- `entity_type` — `person`, `place`, `project`, `activity`, `organization`
- `name` — identifier (UNIQUE with entity_type)
- `label` — relationship label (compagna, amico, etc.)
- `description` — accumulated description (appended on each mention)
- `mention_count` — auto-incremented on duplicate insert
- `first_seen_at`, `last_seen_at` — temporal tracking

**`episodes`** — Facts, events, habits, preferences, opinions extracted from conversations
- `episode_type` — `fact`, `event`, `habit`, `preference`, `opinion`
- `content` — the extracted information
- `source_type` — `dump_import`, `passive_chat`, `checkin_reply`
- `source_ref` — unique dedup key (UNIQUE with episode_type)
- `confidence` — extraction confidence (0.0-1.0)
- `occurred_at` — when the event/fact happened (if temporal cues present)
- `entity_names` — JSON array of related entity names
- `active` — 0 when superseded by newer information
- `superseded_by` — FK to the newer episode that replaced this one

**`entity_relations`** — How the subject relates to each entity (especially people)
- `entity_id` FK → entities
- `relation_type` — compagna, amico, famiglia, collega, etc.
- `dynamic` — relationship dynamic (protettivo, giocoso, formale, etc.)
- `sentiment` — -1.0 (negative) to 1.0 (positive)
- `evidence` — supporting quotes

**`communication_metrics`** — Programmatic metrics computed without LLM
- `platform`, `chat_id` — which conversation
- `period` — `all` or `YYYY-MM` for monthly breakdowns
- `metric_type` — `response_time`, `msg_length`, `activity_hours`, `punctuation`, `burst_pattern`, `vocabulary`
- `metric_data` — JSON with computed values
- `sample_size` — number of messages analyzed
- UNIQUE on (platform, chat_id, period, metric_type)

**`decisions`** — Decisions tracked for coherence analysis against personality traits
- `decision` — what was decided
- `domain` — `lavoro`, `relazioni`, `finanza`, `salute`, `lifestyle`, `tech`
- `facet_ids` — JSON array of linked personality facet IDs
- `direction` — towards which spectrum pole
- `source_ref` — unique dedup key

### Memory Layer Indexes

- `idx_entities_type` on `entities(entity_type)`
- `idx_episodes_type` on `episodes(episode_type)`
- `idx_episodes_active` on `episodes(active)`
- `idx_decisions_domain` on `decisions(domain)`
- `idx_entity_relations_entity` on `entity_relations(entity_id)`

---

## 5. Component Details

### 5.1 Message Capture & Delivery Hook

**File**: `workspace/hooks/soulkiller-capture/handler.ts`
**Events**: `message:received`, `message:sent`

A TypeScript hook with two handlers:

**`handleReceived`** (inbound messages the configured subject, user ID demo-subject):

1. **Inbox capture**: Messages are appended to `$SOULKILLER_DATA_DIR/inbox.jsonl` as JSON lines:
   ```json
   {"message_id":"tg-12345","from":"demo-subject","content":"message text","channel_id":"telegram","received_at":"2026-02-28T08:00:00.000Z"}
   ```

2. **Follow-up trigger**: After inbox capture, the hook checks for `pending-checkin.json`. If a signal file exists and was written within the last 4 hours, it:
   - Deletes the signal file first (prevents duplicate triggers)
   - Spawns a fire-and-forget `openclaw agent` with a prompt to: (a) record the reply via `soulkiller_db.py capture-reply`, (b) send a brief acknowledgment message the configured subject via `openclaw message send` with `--reply-to` for threading
   - The follow-up prompt enforces: max 1 sentence under 80 characters, informal Italian, no follow-up questions, no emoji

**`handleSent`** (outbound messages the configured subject):

Logs delivery status to `soulkiller/delivery.jsonl`:
```json
{"to":"demo-subject","content_preview":"first 80 chars...","success":true,"message_id":"tg-456","sent_at":"..."}
```

Failed deliveries are logged with the error message for monitoring.

**Signal file protocol**: The `pending-checkin.json` file acts as a coordination mechanism between the check-in cron (writes it) and the capture hook (reads and deletes it). Format:
```json
{"exchange_id":5,"facet_id":"values.aesthetic_values","question_text":"...","asked_at":"2026-02-28T20:03:28Z"}
```

The JSONL inbox file is append-only. Deduplication happens at the database level via `UNIQUE(message_id)`. If a message arrives without a `message_id`, the database module generates a deterministic fallback ID by hashing the content and timestamp: `gen-{sha256(content:received_at)[:16]}`.

### 5.1b Bootstrap Hook

**File**: `workspace/hooks/soulkiller-bootstrap/handler.ts`
**Event**: `agent:bootstrap`

Fires on every agent session start. Reads `$SOULKILLER_DATA_DIR/PROFILE.md` and injects it as a virtual bootstrap file named `PERSONALITY_MODEL.md` into the agent's context.

**Exclusions**:
- Sub-agent sessions (agent name contains `:subagent:`) — they inherit from the parent
- Internal soulkiller crons (session prompt contains `soulkiller_`) — prevents circular personality injection during extraction/synthesis

If `PROFILE.md` doesn't exist or is empty, the hook silently skips injection.

### 5.2 Extractor (`soulkiller_extractor.py`)

**Cron**: `soulkiller:extract` — every 2 hours at `:15`
**LLM calls**: max 2 batches of 10 messages = max 2 LLM calls per run

The extractor is the primary pipeline stage that turns raw messages into structured personality observations. Its execution flow:

1. **Ingest**: Reads all lines from `inbox.jsonl`, inserts each into the `inbox` table. Duplicates (same `message_id`) are silently skipped via `ON CONFLICT DO NOTHING`.

2. **Batch**: Fetches up to 10 unprocessed messages from the `inbox` table (ordered by `received_at` ASC). Runs at most 2 batches per invocation, capping at 20 messages per run.

3. **Extract**: Sends the batch to the LLM with a structured prompt listing all 60 facets and their spectrums. The LLM returns a JSON array of signals, each containing:
   - `message_index` — which message in the batch
   - `facet_id` — which personality facet this relates to
   - `extracted_signal` — what the message reveals
   - `signal_strength` — confidence (filtered at >= 0.4)
   - `signal_position` — where on the spectrum (omitted for non-linear facets)

4. **Insert**: Each valid signal becomes an observation in the database. Invalid facet IDs (FK constraint violation) are silently skipped. The observation's `source_ref` is `inbox:{message_id}` for deduplication.

5. **Correlate replies**: For each batch, the extractor checks if there are pending check-in exchanges (questions asked within the last 4 hours with no reply captured). If exactly one pending exchange exists, it runs a lightweight LLM YES/NO match to determine if any incoming message is a reply to that question. If multiple exchanges are pending, it logs the ambiguity and does not auto-link — this prevents misattribution.

6. **Mark processed**: After extraction, all processed inbox entries get `processed = 1`.

### 5.3 Question Engine (`soulkiller_question_engine.py`)

**Called by**: `soulkiller:checkin` cron (every 30 minutes, 9-22h)
**No LLM calls** — pure algorithmic scoring

The question engine replaces the old `soulkiller_topic_gap_score.py`. It scores all 60 facets using the Facet Gap Score formula:

```
FGS = 0.40 * knowledge_gap
    + 0.25 * facet_importance
    + 0.15 * temporal_readiness
    - 0.20 * intrusion_cost
    - 0.10 * asked_recently
```

**knowledge_gap** = `1.0 - trait.confidence`. Facets we know nothing about score 1.0; well-understood facets score near 0.

**facet_importance** = category-based weight. Cognitive and values (0.85) are prioritized over aesthetic (0.40) because they're more fundamental to personality modeling.

**temporal_readiness** = time-of-day fit. Deep/sensitive facets (emotional, relational, values, meta-cognition) score higher in the evening (18-22h: 0.90) when people are more reflective, and lower in the morning (9-15h: 0.45). Light facets (aesthetic, communication) score uniformly throughout waking hours (0.75).

**intrusion_cost** = sensitivity base + mode adjustment. High-sensitivity facets in spotlight mode get a +0.10 penalty. Anchor mode (high emotional state) gets a -0.15 reduction (more emotional availability). Late night (before 9h, after 22h) adds +0.10.

**asked_recently** = decay function. If the facet was asked about within 48 hours: 1.0 (full penalty). 48-120h: 0.6. 120-240h: 0.3. Beyond 240h: 0.0.

The engine checks both the `checkin_exchanges` table in SQLite and the legacy `history` array in `relational_agent_state.json` for recency data.

**Gate checks** (reused from the old scorer):
- Quiet hours (23:00-08:00): no check-ins
- Daily max (default 3): stop after reaching the limit
- Minimum spacing (default 240 minutes): no rapid-fire questions
- Time windows: 09:30-12:30, 15:00-19:30, 21:00-22:30
- Mandatory rule: if min-per-day (default 1) not met by 20:30, force a check-in

**Threshold**: 0.15 (lower than the old TGS threshold of 0.29, because with 60 facets the scores are more spread out).

**Output format** is backward-compatible with additive fields:
```json
{
  "status": "ask_now",
  "reason": "due",
  "engine": "soulkiller",
  "selected_topic": "cognitive.decision_speed",
  "selected_facet": "cognitive.decision_speed",
  "selected_score": 0.675,
  "question_hint": "Chiedi di una decisione recente che ha dovuto prendere in fretta",
  "ranking_top3": [...],
  "ranking_top5": [...]
}
```

`selected_topic` and `selected_facet` are identical — the former exists for backward compatibility with the cron prompt format.

**Fallback**: If the SQLite database is inaccessible (corrupted, missing, migration in progress), the engine automatically falls back to the legacy 5-topic scorer by dynamically importing `soulkiller_topic_gap_score.py`. The output includes `"engine": "legacy_fallback"` so downstream consumers know which engine ran.

### 5.4 Personal Check-in Cron (`soulkiller:checkin`)

**Schedule**: `*/30 9-22 * * *` (every 30 minutes, 9h-22h Europe/Rome)
**Cron ID**: `a576e08b-dbb9-4444-9423-45358f80effb`

The cron agent executes a 6-step process:

1. **Gate check**: Runs `soulkiller_question_engine.py` without `--apply`. If status is not `ask_now`, replies `NO_REPLY` (no message sent).

2. **Apply**: Runs again with `--apply` to persist the state update (increment `sent_today`, record to history, update `soulkiller` config in state file).

3. **Read context**: Reads `demo_profile.json` and `PROFILE.md` (if it exists) for personality context.

4. **Compose message**: Using the `selected_facet` and `question_hint`, composes a short Italian message (max 1-2 sentences, under 120 characters) that sounds like a friend texting — concrete, specific, no abstractions or coaching language. Explicitly avoids binary "A or B?" quiz-style questions.

5. **Send**: Delivers via `openclaw message send --channel telegram --target demo-subject`.

6. **Record**: Calls `soulkiller_db.py record-checkin --facet <id> --question <text> --message-id <telegram_msg_id>` to log the exchange in the database. This also writes `pending-checkin.json` as a signal file for the follow-up hook.

### 5.5 Passive Observer (`soulkiller_passive_observer.py`)

**Cron**: `soulkiller:passive-scan` — every 6 hours at `:45`
**LLM calls**: 1 per session with new messages (capped at 20 messages total per run)
**Safety limits**: 3MB max session file size, 10 messages per session, 4-minute run timeout, 90-second per-call LLM timeout

The passive observer complements the inbox extractor by analyzing behavioral patterns in agent interaction sessions rather than raw Telegram messages. It looks for meta-signals — not what the subject says, but how he interacts:

- **Communication patterns**: message length, response style, language switching
- **Decision signals**: when he accepts or rejects agent suggestions, what reasoning he provides
- **Emotional markers**: exclamation patterns, frustration indicators, enthusiasm
- **Preference signals**: corrections to agent behavior, explicit likes/dislikes

**Session parsing**: Stream-parses JSONL session files from the configured relational session roots (default `agents/<relational-agent>/sessions/`). Extracts:
1. User messages (role=user, content >= 10 chars, excluding system tags)
2. Behavioral patterns (user responses immediately following assistant tool calls)

**Offset-based resume**: Tracks the last processed line offset per session file in `soulkiller/passive-observer-state.json`. On each run, only reads new lines since the last offset. State entries older than 7 days are automatically pruned.

**Dedup with inbox extractor**: Passive observations use `source_type='session_behavioral'` and `source_ref='session:{session_id}:{offset}'`. The UNIQUE constraint on `(facet_id, source_ref)` prevents the same observation from being recorded twice even if the observer runs multiple times over the same session data.

### 5.6 Synthesizer (`soulkiller_synthesizer.py`)

**Cron**: `soulkiller:synthesize` — daily at 03:00 Europe/Rome
**LLM calls**: max 1 per day (for hypothesis generation)

The synthesizer consolidates raw observations into the trait model. It runs in three phases:

**Phase 1: Trait computation**

For each facet with new observations since the last synthesis:

- **Value position** (linear facets): Weighted average of `signal_position` values, weighted by `signal_strength * recency_weight`. Recency uses exponential decay with a 14-day half-life: `weight = e^(-0.693 * days_old / 14)`. Recent observations dominate older ones.

- **Confidence**: `min(1.0, base_count_conf * consistency_factor)` where:
  - `base_count_conf = 1 - e^(-observation_count / 8)` — plateaus around 15 observations, reaching ~0.85 at 15 obs
  - `consistency_factor = 1 - stdev(signal_positions)` — penalizes contradictory observations. If all observations agree (stdev=0), factor is 1.0. High disagreement (stdev=0.4) drops it to 0.6.
  - For non-linear facets (no positions): `consistency_factor = 0.6` (moderate confidence from count alone)
  - Single observation: `consistency_factor = 0.8`

- **Non-linear facets** (`core_values`, `music_taste`, `cognitive_biases`): Instead of computing a numeric position, the synthesizer accumulates textual evidence — counting recurring labels from `extracted_signal` fields, sorting by frequency, and storing the top 10 as notes. Confidence is still computed from observation count and evidence stability.

**Phase 2: Hypothesis generation**

If any facets were updated, the synthesizer sends the top 10 traits (by observation count) to the LLM with a prompt requesting 1-3 cross-facet patterns. Examples of hypotheses:

- "Under deadline pressure, decision speed shifts from deliberate to fast"
- "Social energy correlates with creative output — more introverted phases produce higher code quality"

Existing hypotheses are also sent for review, with the LLM updating their status (`unverified` -> `confirmed`/`denied`/`nuanced`) based on new evidence. This is a single LLM call per day — the most expensive operation in the system.

**Phase 3: Snapshot**

Saves a `model_snapshots` row with the full trait dump (JSON), total observations, average confidence, and coverage percentage (facets with confidence > 0.3).

### 5.7 Profile Bridge (`soulkiller_profile_bridge.py`)

**Cron**: `soulkiller:profile-sync` — daily at 03:30 Europe/Rome (after synthesis)
**No LLM calls**

The bridge maintains backward compatibility with existing operational protocols that read `demo_profile.json`. It performs two operations:

**JSON sync**: Maps each Soulkiller category to profile categories:

| Soulkiller | Profile |
|------------|---------|
| cognitive | decisioni, preferenze_stile |
| emotional | vincoli, conoscenze_assimilate |
| communication | preferenze_stile |
| relational | conoscenze_assimilate, valori |
| values | valori |
| temporal | abitudine, vincoli |
| aesthetic | preferenze_stile |
| meta_cognition | conoscenze_assimilate |

For traits with confidence >= 0.3:
- If a record with `fonte: "soulkiller:{facet_id}"` exists -> update its `contenuto`, `confidenza`, `stato`
- If not -> create a new record with id `mem-YYYYMMDD-NNN`

For traits that drop below 0.3 confidence -> mark existing records as `stato: "da_verificare"`.

Records with `fonte` not starting with `soulkiller:` are preserved untouched — the 12 original operational records remain intact. The schema version bumps from `1.1` to `1.2`, adding an optional `soulkiller_facet_id` field on soulkiller-sourced records.

**PROFILE.md generation**: Creates a human-readable Markdown file at `$SOULKILLER_DATA_DIR/PROFILE.md` with:
- Header with coverage stats (e.g., "34/60 facets, 74%, avg confidence 0.62")
- Per-category sections with traits sorted by confidence
- Position descriptions (e.g., "Tendenza verso: deliberato")
- Active hypotheses section

### 5.8 Memory Layer (`soulkiller_memory.py`)

**No cron** — invoked via `soulkiller_dump_import.py extract-memory` and `compute-metrics` commands
**LLM calls**: Same pattern as personality extraction — 1 per batch of conversation windows

The memory layer complements personality trait extraction by capturing *factual* information from the same conversation windows. While personality extraction answers "how does the subject think?", memory extraction answers "what does the subject know/do/decide?".

**Episodic Memory Extraction** (LLM-based):

Uses a separate prompt from personality extraction but processes the same conversation windows. The prompt instructs the LLM to extract:
1. **Entities**: People, places, projects, organizations mentioned
2. **Facts**: Biographical information the subject states about himself
3. **Events**: Specific occurrences with temporal cues
4. **Habits**: Recurring patterns mentioned or demonstrated
5. **Decisions**: Choices made, linked to personality facets when possible
6. **Relationship signals**: Tone, dynamic, and sentiment toward entities

Each extraction category maps to its own DB table with dedup via `source_ref`.

**Communication Metrics** (programmatic, no LLM):

Computed directly from parsed message data (staging JSONL files). Six metric types:

| Metric | What it measures |
|--------|-----------------|
| `response_time` | Delta between received message and the subject's reply (mean, median, p95, std) |
| `msg_length` | Character and word counts (mean, median, std, short%, long%) |
| `activity_hours` | 24-bucket hour histogram, peak hour, active range |
| `punctuation` | Frequency per 100 chars of ellipsis, !, ?, CAPS, emoji, lol variants |
| `burst_pattern` | Consecutive messages within 60s (burst count, avg/max size, single%) |
| `vocabulary` | Type-token ratio, avg sentence length, Italian/English ratio |

**Decision Coherence Analysis** (`compute_decision_coherence()`):

After accumulating >= 3 decisions per domain, the function cross-references each decision's `direction` against the linked personality trait's `value_position`. Direction matching uses keyword heuristics: terms like "averse", "cauto", "lento", "evit", "conserv", "rigid" indicate a low-spectrum direction (expected `value_position <= 0.5`); all other directions are assumed high-spectrum. Produces a coherence score (0.0-1.0) per domain with a list of contradictions for review. Domains with fewer than 3 decisions are skipped.

**Memory Summary** (`get_memory_summary()` / `get_full_memory_summary()`):

Two summary functions are available for programmatic access:
- `soulkiller_memory.get_memory_summary()` — returns counts for entities (by type), episodes (by type), decisions (by domain), relations, and chats with metrics
- `soulkiller_db.get_full_memory_summary()` — combines the personality model summary with memory layer counts in a single dict

**State Tracking**:

Memory extraction uses independent status columns on the `windows` table in `import_state.db`:
- `memory_status` — `pending`, `extracting`, `extracted`, `failed`
- `memory_extracted_at` — timestamp of extraction
- `memory_signals_count` — total signals extracted
- `memory_error_message` — error details if failed

These columns are added via migration-safe `ALTER TABLE` (checks if column exists before adding). This allows `extract` (personality) and `extract-memory` to operate independently on the same windows.

### Deep Construct Tables (v2.0)

Added by `soulkiller_migrate_v2.py`. Store outputs of 8 specialized psychological analyzers.

**`liwc_metrics`** — Weekly LIWC-style psycholinguistic profiles (no LLM, Pennebaker)
- `period` UNIQUE — ISO week `YYYY-WW`
- `i_ratio`, `we_ratio`, `negative_affect`, `positive_affect`, `cognitive_complexity`, etc.

**`stress_snapshots`** — Weekly composite stress index (no LLM)
- `period` UNIQUE, `stress_index` (0-1), `stress_level`, `dominant_signal`

**`schemas`** — Young's Early Maladaptive Schemas + Vaillant defense mechanisms
- `schema_name` UNIQUE — one record per schema/defense, updated in place
- `schema_domain`: `disconnection|impaired_autonomy|impaired_limits|other_directedness|overvigilance` (EMS) or `defense_mechanism` (Vaillant)
- `activation_level` (0-1), `confidence`, `trigger_contexts`, `behavioral_signatures`, `evidence`

**`goals`** — Active goal hierarchy (Little/Klinger)
- UNIQUE(`goal_text`, `domain`), `horizon`, `priority_rank`, `progress`, `status`, `conflicts_with`

**`sdt_satisfaction`** — SDT needs per domain per month (Deci & Ryan)
- UNIQUE(`period`, `domain`), `autonomy_satisfaction`, `competence_satisfaction`, `relatedness_satisfaction`

**`attachment_signals`** — ECR-R attachment per relationship context
- UNIQUE(`relationship_context`), `anxiety_level`, `avoidance_level`, `secure_behaviors`, `anxious_behaviors`, `avoidant_behaviors`

**`caps_signatures`** — CAPS if-then situational signatures (Mischel & Shoda)
- UNIQUE(`situation_type`), `behavioral_response`, `emotional_response`, `confidence`

### 8 New Facets (v2.0, total: 54)

| Facet ID | Category | Spectrum | Framework |
|----------|----------|----------|-----------|
| `relational.attachment_anxiety` | relational | bassa ansia ... alta ansia | ECR-R |
| `relational.attachment_avoidance` | relational | basso evitamento ... alto evitamento | ECR-R |
| `relational.vulnerability_capacity` | relational | bassa capacità ... alta capacità | DERS |
| `emotional.emotion_clarity` | emotional | bassa chiarezza ... alta chiarezza | DERS |
| `emotional.distress_tolerance` | emotional | bassa tolleranza ... alta tolleranza | DERS |
| `temporal.delay_discounting` | temporal | impulsivo ... paziente | Intertemporal choice |
| `meta_cognition.narrative_agency` | meta_cognition | agito dagli eventi ... architetto attivo | McAdams |
| `values.schwartz_self_enhancement` | values | auto-trascendenza ... auto-affermazione | Schwartz |

### 6 New Facets (v3.0, total: 60)

| Facet ID | Category | Spectrum | Framework |
|----------|----------|----------|-----------|
| `language.verbal_complexity` | language | bassa complessità ... alta complessità | Idiolect / TTR |
| `emotional.appraisal_agency` | emotional | agency esterna ... agency interna | Lazarus/Scherer |
| `emotional.coping_appraisal` | emotional | basso coping ... alto coping | Lazarus/Scherer |
| `cognitive.mental_model_complexity` | cognitive | modelli minimali ... modelli esaustivi | Johnson-Laird |
| `cognitive.system1_dominance` | cognitive | deliberato (System 2) ... intuitivo (System 1) | Kahneman |
| `cognitive.construct_complexity` | cognitive | pochi costrutti rigidi ... molti costrutti permeabili | Kelly |

### v3.0 Deep Construct Tables

Five new tables store specialized data for the Tier 1 cognitive constructs.

**`idiolect_profile`** — Programmatic linguistic fingerprint (TTR, sentence stats, style markers, top n-grams). One row per period (`YYYY-MM` or `all`). `UNIQUE(period)`.

**`appraisal_patterns`** — Per-domain emotional appraisal profiles (Lazarus/Scherer). Fields: `novelty_sensitivity`, `goal_relevance_weight`, `coping_potential_default`, `agency_attribution`, `norm_compatibility_weight`. `UNIQUE(domain)`.

**`mental_model_patterns`** — Per-domain cognitive representation analysis (Johnson-Laird). Fields: `representation_style` (spatial/propositional/narrative/mixed), `model_complexity` (minimal/moderate/exhaustive), `counterfactual_frequency`, `analogy_preference`. `UNIQUE(domain)`.

**`dual_process_profile`** — Per-domain System 1/2 balance (Kahneman). Fields: `system1_dominance` (0-1), `override_frequency`, `default_mode`, `evidence`. Also stores programmatic markers globally: self-correction, deliberation, and snap judgment rates per 1000 words. `UNIQUE(domain)`.

**`personal_constructs`** — Kelly's repertory grid dimensions. Fields: `pole_positive`, `pole_negative`, `superordinate` (bool), `range_of_convenience` (JSON array), `permeability` (0-1), `usage_frequency` (0-1), `evidence`. `UNIQUE(construct_name)`.

---

## 6. Scoring Formulas

### Facet Gap Score (FGS) — question selection

```
FGS = 0.40 * (1.0 - confidence)
    + 0.25 * category_importance
    + 0.15 * temporal_readiness
    - 0.20 * intrusion_cost
    - 0.10 * asked_recently_decay
```

Clamped to [0.0, 1.0]. Threshold for selection: 0.15.

### Trait Confidence

```
confidence = min(1.0, base_count * consistency)

base_count = 1 - e^(-n/8)        # n = observation count
consistency = max(0.1, 1 - stdev) # stdev of signal positions
```

Sample confidence values at different observation counts (assuming consistent signals, stdev~0):

| Observations | base_count | confidence |
|-------------|------------|------------|
| 1 | 0.118 | 0.094 |
| 3 | 0.313 | 0.188 |
| 5 | 0.465 | 0.372 |
| 8 | 0.632 | 0.569 |
| 12 | 0.777 | 0.777 |
| 15 | 0.847 | 0.847 |
| 20 | 0.918 | 0.918 |

With contradictory signals (stdev=0.3), confidence at 12 observations drops from 0.777 to 0.544.

### Value Position (recency-weighted average)

```
position = sum(pos_i * strength_i * recency_i) / sum(strength_i * recency_i)

recency_i = e^(-0.693 * days_old / 14)  # 14-day half-life
```

A 14-day-old observation has half the weight of a fresh one. A 28-day-old observation has 1/4 the weight.

---

## 7. File Inventory

### Scripts (in `workspace/scripts/`)

| File | Purpose |
|------|---------|
| `soulkiller_db.py` | SQLite schema (personality + memory tables), 60 facet seed data, full module API, memory query helpers, CLI entrypoints |
| `soulkiller_memory.py` | Memory layer: schema, DB helpers, extraction prompt, signal processing, communication metrics, decision coherence, summary |
| `soulkiller_dump_import.py` | Dump parsing, windowing, personality + memory extraction, communication metrics computation, state DB migration |
| `soulkiller_question_engine.py` | FGS scoring, gate checks, legacy fallback, backward-compatible output |
| `soulkiller_extractor.py` | Inbox ingestion, LLM signal extraction, check-in reply correlation |
| `soulkiller_passive_observer.py` | Session transcript scanning, behavioral meta-signal extraction |
| `soulkiller_synthesizer.py` | Trait consolidation, confidence computation, LLM hypothesis generation |
| `soulkiller_profile_bridge.py` | JSON profile sync, PROFILE.md generation |
| `soulkiller_portrait.py` | Full portrait synthesis — integrates all constructs into a narrative PORTRAIT.md |
| `soulkiller_decisions.py` | Decision extraction from inbox for coherence analysis |
| `soulkiller_reply_extractor.py` | Check-in reply → observation extraction |
| `soulkiller_entity_extractor.py` | Entity + relation extraction from inbox |
| `soulkiller_entity_dedup.py` | Entity deduplication (manual utility) |
| `soulkiller_liwc.py` | Weekly LIWC-style psycholinguistic analysis (no LLM) |
| `soulkiller_stress_index.py` | Weekly composite stress index (no LLM) |
| `soulkiller_schemas.py` | Monthly Early Maladaptive Schema detection (Young) |
| `soulkiller_defenses.py` | Monthly defense mechanism detection (Vaillant hierarchy) |
| `soulkiller_goals.py` | Monthly goal architecture extraction (Little/Klinger) |
| `soulkiller_caps.py` | Monthly CAPS situational signature synthesis (Mischel & Shoda) |
| `soulkiller_sdt.py` | Monthly SDT needs assessment (Deci & Ryan) |
| `soulkiller_attachment.py` | Monthly ECR-R attachment analysis per relationship context |
| `soulkiller_narrative.py` | Monthly narrative identity analysis (McAdams) |
| `soulkiller_idiolect.py` | Monthly idiolect fingerprint (programmatic, no LLM) |
| `soulkiller_appraisal.py` | Monthly Lazarus/Scherer emotional appraisal extraction (LLM) |
| `soulkiller_mental_models.py` | Monthly Johnson-Laird mental model analysis (LLM) |
| `soulkiller_dual_process.py` | Monthly Kahneman System 1/2 balance (hybrid: programmatic + LLM) |
| `soulkiller_constructs.py` | Monthly Kelly personal construct extraction (LLM) |
| `soulkiller_migrate_v3.py` | One-shot v3.0 migration (5 tables + 6 facets) |
| `soulkiller_migrate_v2.py` | One-shot v2.0 migration (tables + facets) |
| `soulkiller_healthcheck.py` | System health monitoring |
| `soulkiller_budget_bridge.py` | Financial behavior → personality observation bridge |
| `soulkiller_voicenote_transcriber.py` | Voice note transcription pipeline |

### Hooks

| Directory | Files | Events | Purpose |
|-----------|-------|--------|---------|
| `workspace/hooks/soulkiller-capture/` | `HOOK.md`, `handler.ts` | `message:received`, `message:sent` | Inbox capture, follow-up trigger, delivery tracking |
| `workspace/hooks/soulkiller-bootstrap/` | `HOOK.md`, `handler.ts` | `agent:bootstrap` | Injects PROFILE.md into agent sessions |

### Data (in `$SOULKILLER_DATA_DIR/`)

| File | Purpose |
|------|---------|
| `soulkiller.db` | SQLite database (WAL mode) |
| `inbox.jsonl` | Append-only message buffer from capture hook |
| `delivery.jsonl` | Outbound message delivery log |
| `pending-checkin.json` | Signal file: check-in cron writes, capture hook reads/deletes |
| `PROFILE.md` | Human-readable model snapshot (also injected by bootstrap hook) |
| `passive-observer-state.json` | Session scan offset tracking |
| `dumps/windows_cache.jsonl` | Cached window message data for extraction (written by `parse`, read by `extract` and `extract-memory`) |
| `dumps/import_state.db` | State tracking DB for dump import: parsed files, windows with personality + memory extraction status |

### Dependencies (existing code reused)

| Module | Used by | Purpose |
|--------|---------|---------|
| `lib/log.py` | All scripts | Structured JSON logging |
| `lib/config.py` | Extractor, observer | OpenClaw binary path, timezone |
| `lib/openclaw_client.py` | Extractor, observer, synthesizer | `run_agent_json()` for LLM calls, `send_message()` for Telegram |
| `soulkiller_topic_gap_score.py` | Question engine (fallback) | Legacy 5-topic scorer |

---

## 8. Cron Schedule

| Job | Schedule | Purpose |
|-----|----------|---------|
| `soulkiller:checkin` | `*/30 9-22 * * *` | Active questioning (uses soulkiller engine) |
| `soulkiller:extract` | `15 */2 * * *` | Inbox ingestion + signal extraction |
| `soulkiller:passive-scan` | `45 */6 * * *` | Session transcript observation |
| `soulkiller:reply-extract` | `0 */6 * * *` | Process pending check-in replies |
| `soulkiller:synthesize` | `0 3 * * *` | Daily trait consolidation |
| `soulkiller:profile-sync` | `30 3 * * *` | Profile JSON + PROFILE.md sync |
| `soulkiller:healthcheck` | `0 4 * * *` | System health monitoring |
| `soulkiller:entity-extract` | `0 4 * * *` | Memory entity extraction |
| `soulkiller:decisions` | `15 4 * * *` | Decision extraction |
| `soulkiller:voicenote-transcribe` | `30 4 * * *` | Voice note transcription |
| `soulkiller:budget-bridge` | `0 5 * * 1` | Financial behavior bridge (weekly Mon) |
| `soulkiller:memory-metrics` | `30 5 * * 1` | Communication metrics (weekly Mon) |
| `soulkiller:liwc` | `0 3 * * 0` | LIWC psycholinguistic analysis (weekly Sun) |
| `soulkiller:stress-index` | `0 6 * * 1` | Composite stress index (weekly Mon) |
| `soulkiller:schemas` | `0 5 1 * *` | EMS detection (monthly 1st) |
| `soulkiller:goals` | `30 5 1 * *` | Goal architecture extraction (monthly 1st) |
| `soulkiller:sdt` | `0 6 1 * *` | SDT needs assessment (monthly 1st) |
| `soulkiller:portrait` | `0 6 1 * *` | Full portrait synthesis (monthly 1st) |
| `soulkiller:caps` | `30 5 2 * *` | CAPS signatures (monthly 2nd) |
| `soulkiller:attachment` | `0 5 3 * *` | ECR-R attachment analysis (monthly 3rd) |
| `soulkiller:defenses` | `30 5 3 * *` | Defense mechanism detection (monthly 3rd) |
| `soulkiller:narrative` | `0 6 3 * *` | Narrative identity analysis (monthly 3rd) |
| `soulkiller:idiolect` | `0 4 1 * *` | Idiolect fingerprint (monthly 1st) |
| `soulkiller:appraisal` | `30 4 5 * *` | Appraisal theory analysis (monthly 5th) |
| `soulkiller:mental-models` | `0 5 5 * *` | Mental model extraction (monthly 5th) |
| `soulkiller:dual-process` | `30 5 5 * *` | System 1/2 balance (monthly 5th) |
| `soulkiller:constructs` | `0 6 5 * *` | Personal constructs (monthly 5th) |

Total: 27 cron jobs (26 soulkiller + 1 personal-checkin).

All cron jobs use `sessionTarget: "isolated"` and `thinking: "low"` to minimize token cost.

---

## 9. Safety and Privacy

### Intrusion control

The question engine gates every check-in through multiple layers:
- Time windows restrict when questions can be sent
- Quiet hours (23:00-08:00) block all outreach
- Daily max (3) prevents overwhelming
- Minimum spacing (4 hours) prevents rapid-fire
- Facet sensitivity penalizes asking about deeply personal topics at inappropriate times
- Presence mode (spotlight/ambient/anchor) adjusts the intrusion threshold

### Data sensitivity

Each facet has a sensitivity level (`bassa`/`media`/`alta`) that controls:
- How aggressively the system pursues that dimension
- When it's appropriate to ask (alta-sensitivity facets are deprioritized in spotlight mode)
- The intrusion cost in the scoring formula

### Deduplication

Every observation has a unique `(facet_id, source_ref)` pair. The system cannot record the same signal twice from the same source. The inbox uses `UNIQUE(message_id)` with a hash-based fallback for messages without IDs.

### Fallback safety

If the SQLite database is corrupted or unavailable, the question engine falls back to the legacy 5-topic scorer, ensuring check-ins continue working even if Soulkiller has issues.

### FK constraint protection

When the LLM returns an invalid `facet_id` that doesn't exist in the facets table, the `add_observation()` function catches the `IntegrityError` and returns `None` instead of crashing the extraction batch.

---

## 10. CLI Reference

### Database management

```bash
# Initialize database + seed 60 facets
python3 soulkiller_db.py --init

# Print model summary
python3 soulkiller_db.py summary

# Record a check-in exchange (also writes pending-checkin.json signal)
python3 soulkiller_db.py record-checkin \
  --facet "cognitive.decision_speed" \
  --question "Come gestisci le deadline urgenti?" \
  --message-id "tg-12345"

# Capture a reply to a check-in exchange
python3 soulkiller_db.py capture-reply \
  --exchange-id 5 \
  --reply "Di solito ci penso un po' prima"

```

### Question engine

```bash
# Check if a question should be sent (dry run)
python3 soulkiller_question_engine.py \
  --state ../memory/relational_agent_state.json

# Check and apply state update
python3 soulkiller_question_engine.py \
  --state ../memory/relational_agent_state.json \
  --apply

# Override current time (for testing)
python3 soulkiller_question_engine.py \
  --state ../memory/relational_agent_state.json \
  --now "2026-02-28T21:00:00+01:00"
```

### Memory layer (dump import)

```bash
# Extract episodic memory from pending windows (dry run)
python3 soulkiller_dump_import.py extract-memory --dry-run --limit 1

# Extract episodic memory (real)
python3 soulkiller_dump_import.py extract-memory --limit 3

# Retry failed memory extractions
python3 soulkiller_dump_import.py extract-memory --retry-failed

# Compute communication metrics from parsed data
python3 soulkiller_dump_import.py compute-metrics

# Compute metrics for specific platform/chat
python3 soulkiller_dump_import.py compute-metrics --platform whatsapp --chat marco

# Status (shows both personality and memory extraction progress)
python3 soulkiller_dump_import.py status
```

### Manual runs

```bash
# Run extraction manually
python3 soulkiller_extractor.py

# Run passive observation manually
python3 soulkiller_passive_observer.py

# Run synthesis manually
python3 soulkiller_synthesizer.py

# Run profile sync manually
python3 soulkiller_profile_bridge.py
```

### Database queries

```bash
# Coverage by category
sqlite3 soulkiller/soulkiller.db \
  "SELECT f.category, COUNT(*), AVG(t.confidence), SUM(t.observation_count)
   FROM traits t JOIN facets f ON t.facet_id = f.id
   GROUP BY f.category"

# Top traits by confidence
sqlite3 soulkiller/soulkiller.db \
  "SELECT facet_id, value_position, confidence, observation_count
   FROM traits WHERE confidence > 0.3
   ORDER BY confidence DESC"

# Recent observations
sqlite3 soulkiller/soulkiller.db \
  "SELECT facet_id, source_type, extracted_signal, signal_strength
   FROM observations ORDER BY created_at DESC LIMIT 20"

# Active hypotheses
sqlite3 soulkiller/soulkiller.db \
  "SELECT hypothesis, status, confidence FROM hypotheses
   WHERE status IN ('unverified','confirmed','nuanced')
   ORDER BY confidence DESC"

# Model evolution (snapshots)
sqlite3 soulkiller/soulkiller.db \
  "SELECT snapshot_at, total_observations, avg_confidence, coverage_pct
   FROM model_snapshots ORDER BY snapshot_at DESC LIMIT 10"

# Memory layer: entities by mention count
sqlite3 soulkiller/soulkiller.db \
  "SELECT entity_type, name, label, mention_count
   FROM entities ORDER BY mention_count DESC LIMIT 20"

# Memory layer: recent facts
sqlite3 soulkiller/soulkiller.db \
  "SELECT content, confidence, extracted_at
   FROM episodes WHERE episode_type='fact' AND active=1
   ORDER BY extracted_at DESC LIMIT 10"

# Memory layer: decisions by domain
sqlite3 soulkiller/soulkiller.db \
  "SELECT domain, decision, direction
   FROM decisions ORDER BY extracted_at DESC LIMIT 10"

# Memory layer: relationship dynamics
sqlite3 soulkiller/soulkiller.db \
  "SELECT e.name, r.relation_type, r.dynamic, r.sentiment
   FROM entity_relations r JOIN entities e ON r.entity_id = e.id
   ORDER BY r.updated_at DESC LIMIT 10"

# Memory layer: communication metrics
sqlite3 soulkiller/soulkiller.db \
  "SELECT platform, chat_id, metric_type, metric_data
   FROM communication_metrics ORDER BY computed_at DESC LIMIT 10"
```

---

## 11. Monitoring

### What to check

- **Coverage growth**: After 1 week, `coverage_pct` in snapshots should be above 20%. After 1 month, above 50%.
- **Extraction yield**: The extractor logs `signals_extracted` and `total_processed`. If the ratio drops below 10% consistently, the extraction prompt may need tuning.
- **Check-in delivery**: The `soulkiller:checkin` cron should show `lastStatus: "ok"`. If it consistently outputs `NO_REPLY`, the FGS threshold (0.15) may be too high, or gate checks are too restrictive.
- **Hypothesis quality**: Review `PROFILE.md` hypotheses section periodically. Hypotheses stuck at `unverified` with low confidence after many synthesis cycles may indicate insufficient cross-facet data.
- **Delivery failures**: Check `soulkiller/delivery.jsonl` for `"success":false` entries. Failed deliveries indicate Telegram API issues.
- **Follow-up triggers**: The capture hook logs `[soulkiller-capture] Follow-up agent triggered for exchange {id}` to the gateway log when a reply is detected.

### LLM timeout safety

All LLM calls have both a CLI-level `--timeout` and a Python-level subprocess timeout (CLI timeout + 30s buffer):
- Extractor: 90s per call
- Passive observer: 90s per call + 240s hard run cap
- Synthesizer: 120s per call (heavier hypothesis prompt)

### Log locations

All soulkiller crons log to `cron/runs/soulkiller-*.log`:
- `soulkiller-extract.log`
- `soulkiller-passive.log`
- `soulkiller-synthesize.log`
- `soulkiller-profile-sync.log`

Each script emits structured JSON logs via `lib/log.py` with `script: "soulkiller_*"` for filtering.
