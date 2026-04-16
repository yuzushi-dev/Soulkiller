# Installation

> Tested on **OpenClaw 31.3.26**. Other versions may work but are not verified.

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12+ | Standard library only for core modules |
| OpenClaw | 31.3.26 | Required for hooks and cron registration |
| Node.js | 18+ | Required only for hook compilation (TypeScript → JS) |

---

## Option A - Wizard (recommended)

```bash
git clone https://github.com/yuzushi-dev/soulkiller
cd soulkiller
python install.py
```

The wizard walks through 9 steps:

1. Prerequisites check - Python, OpenClaw binary, version verification
2. Subject configuration - name, ID slug, Telegram sender ID
3. Runtime data directory - where the database, inbox, and portraits live
4. OpenClaw configuration - binary path, home directory, hooks directory
5. Check-in schedule - active hours and probe frequency
6. Optional integrations - relational agent, Telegram, biofeedback
7. Past-message backfill - import existing messages into the extraction inbox
8. Configuration summary - review before committing
9. Installation - writes files, compiles hooks, registers crons

**Dry run** (preview without writing anything):

```bash
python install.py --dry-run
```

---

## Option B - Manual setup

If you prefer to configure without the wizard, follow these steps.

### 1. Install the Python package

```bash
pip install -e .
```

### 2. Create `.env`

Copy the example and fill in your values:

```bash
cp .env.example .env
```

See [Configuration](docs/CONFIGURATION.md) for a full description of every variable.

### 3. Create the runtime data directory

```bash
mkdir -p ~/.soulkiller/<your-subject-id>
touch ~/.soulkiller/<your-subject-id>/inbox.jsonl
```

Set `SOULKILLER_DATA_DIR` in `.env` to this path.

### 4. Compile and register hooks

Each hook is a TypeScript project. OpenClaw compiles it on registration.

```bash
# Register with OpenClaw (OpenClaw compiles TypeScript internally - no npm step needed)
openclaw hooks enable soulkiller-capture \
  --path ./hooks/soulkiller-capture \
  --env SOULKILLER_SUBJECT_ID=<your-subject-id> \
  --env SOULKILLER_DATA_DIR=~/.soulkiller/<your-subject-id>

openclaw hooks enable soulkiller-bootstrap \
  --path ./hooks/soulkiller-bootstrap \
  --env SOULKILLER_SUBJECT_ID=<your-subject-id> \
  --env SOULKILLER_DATA_DIR=~/.soulkiller/<your-subject-id>
```

### 5. Register cron jobs

Replace `<schedule>` with your values. The `checkin-followup` cron has no
fixed schedule - it is triggered on-demand by the capture hook.

```bash
PYTHON=$(which python3)
REPO=$(pwd)
DATA=~/.soulkiller/<your-subject-id>
ENV="--env PYTHONPATH=$REPO/src --env SOULKILLER_DATA_DIR=$DATA"

# Core pipeline
openclaw cron add soulkiller:extract \
  --command "$PYTHON -m soulkiller.extract" \
  --cwd "$REPO" --schedule "0 */2 * * *" $ENV

openclaw cron add soulkiller:checkin \
  --command "$PYTHON -m soulkiller.checkin" \
  --cwd "$REPO" --schedule "*/30 9-22 * * *" $ENV

openclaw cron add soulkiller:passive-scan \
  --command "$PYTHON -m soulkiller.passive_scan" \
  --cwd "$REPO" --schedule "0 */6 * * *" $ENV

openclaw cron add soulkiller:reply-extract \
  --command "$PYTHON -m soulkiller.reply_extract" \
  --cwd "$REPO" --schedule "0 */6 * * *" $ENV

openclaw cron add soulkiller:synthesize \
  --command "$PYTHON -m soulkiller.synthesize" \
  --cwd "$REPO" --schedule "0 3 * * *" $ENV

openclaw cron add soulkiller:profile-sync \
  --command "$PYTHON -m soulkiller.profile_sync" \
  --cwd "$REPO" --schedule "30 3 * * *" $ENV

openclaw cron add soulkiller:checkin-followup \
  --command "$PYTHON -m soulkiller.checkin_followup" \
  --cwd "$REPO" --on-demand $ENV

# Daily enrichment
openclaw cron add soulkiller:entity-extract \
  --command "$PYTHON -m soulkiller.entity_extract" \
  --cwd "$REPO" --schedule "0 4 * * *" $ENV

openclaw cron add soulkiller:decisions \
  --command "$PYTHON -m soulkiller.decisions" \
  --cwd "$REPO" --schedule "15 4 * * *" $ENV

openclaw cron add soulkiller:healthcheck \
  --command "$PYTHON -m soulkiller.healthcheck" \
  --cwd "$REPO" --schedule "0 4 * * *" $ENV

openclaw cron add soulkiller:memory \
  --command "$PYTHON -m soulkiller.memory" \
  --cwd "$REPO" --schedule "0 5 * * 0" $ENV

# Weekly
openclaw cron add soulkiller:liwc \
  --command "$PYTHON -m soulkiller.liwc" \
  --cwd "$REPO" --schedule "0 3 * * 0" $ENV

openclaw cron add soulkiller:stress-index \
  --command "$PYTHON -m soulkiller.stress_index" \
  --cwd "$REPO" --schedule "0 6 * * 1" $ENV
```

For biofeedback, optional integrations, and all 13 monthly specialist analyzers see the full schedule in [docs/CONFIGURATION.md](docs/CONFIGURATION.md#cron-identifiers-reference). The wizard registers all crons automatically.

---

## Verify the installation

```bash
# Demo pipeline (no OpenClaw, no LLM required)
python -m soulkiller.demo_runner --output-dir demo/generated
open demo/generated/demo_console.html

# OpenClaw status
openclaw hooks status
openclaw cron status

# Inbox
wc -l ~/.soulkiller/<subject-id>/inbox.jsonl
```

---

## From demo to live data

The demo pipeline (`soulkiller-demo`) writes synthetic data into `demo/generated/soulkiller.db`.
The live pipeline writes real data into `~/.soulkiller/<subject-id>/soulkiller.db`.
These are separate files - the demo never touches your live database.

To switch the webui from demo data to live data, change `SOULKILLER_DATA_DIR`:

```bash
# demo data (synthetic, no OpenClaw required)
SOULKILLER_DATA_DIR=demo/generated OPENCLAW_HOME=demo/generated python -m soulkiller.webui --port 8765

# live data (requires installation + at least one extraction cycle)
SOULKILLER_DATA_DIR=~/.soulkiller/<subject-id> python -m soulkiller.webui --port 8765
```

If you installed via wizard, `.env` already has `SOULKILLER_DATA_DIR` set to your live directory:

```bash
source .env && python -m soulkiller.webui --port 8765
```

The live database is populated by the cron pipeline. It will be empty until
`soulkiller:extract` runs at least once and processes messages from `inbox.jsonl`.

---

## Connect an LLM

The extraction, check-in, and synthesis crons require an LLM. The demo
pipeline runs on keyword heuristics and needs no model.

See [Adapters](docs/ADAPTERS.md) for how to wire up a provider.

---

## Import past messages (backfill)

If you have existing messages you want the model to learn from, append them
to `inbox.jsonl` in the format below. The `soulkiller:extract` cron will
process them on the next run.

```jsonl
{"message_id":"msg-001","from":"telegram:<subject-id>","content":"...","channel_id":"telegram","received_at":"2026-01-01T10:00:00Z"}
{"message_id":"msg-002","from":"telegram:<subject-id>","content":"...","channel_id":"telegram","received_at":"2026-01-02T09:15:00Z"}
```

The `from` field must match `telegram:<SOULKILLER_SUBJECT_ID>` exactly.
`message_id` values must be unique across the file - duplicates are skipped.
