# Configuration Reference

All configuration is driven by environment variables. The installer writes
them to `.env` at the repo root. Load it however you prefer:

```bash
source .env          # bash
set -a && source .env && set +a    # export all
```

OpenClaw crons receive their env vars directly via `--env` flags at
registration time (set by the installer). You do not need to load `.env`
for cron jobs.

---

## Subject

```env
SOULKILLER_SUBJECT_ID=alice
```
Slug used as a key in logs, database records, and file names. No spaces.
Must match the ID portion of the `from` field in captured messages
(`telegram:<SOULKILLER_SUBJECT_ID>`).

```env
SOULKILLER_SUBJECT_NAME=Alice
```
Display name used in portrait headers and console output.

```env
SOULKILLER_TELEGRAM_ID=123456789
```
Numeric Telegram sender ID of the subject. Used by the check-in delivery
system to route outbound questions to the correct Telegram user. Obtain it
from your Telegram client or by inspecting a raw message event in OpenClaw.
Optional — leave blank if you are not using Telegram check-ins.

Note: message filtering in the capture hook is controlled by
`SOULKILLER_SUBJECT_ID`, not this field. The capture hook matches the
`from` field of incoming events against `SOULKILLER_SUBJECT_ID`.

---

## Paths

```env
SOULKILLER_DATA_DIR=~/.soulkiller/alice
```
Root directory for all runtime state. Created by the installer.

Expected contents after first run:

```
~/.soulkiller/alice/
├── inbox.jsonl              ← inbound messages captured by hook
├── soulkiller.db            ← SQLite: observations, facets, hypotheses
├── PORTRAIT.md              ← human-readable behavioral portrait (injected into agents)
├── subject_profile.json     ← machine-readable trait snapshot
├── pending-checkin.json     ← signal file: check-in reply window active
└── delivery.jsonl           ← outbound delivery log
```

```env
OPENCLAW_HOME=~/.openclaw
```
Root directory of your OpenClaw installation. Used to resolve the default
hooks directory and OpenClaw runtime paths.

```env
OPENCLAW_BIN=openclaw
```
Path or name of the OpenClaw CLI binary. Override if it is not in `PATH`.

---

## Check-in schedule

These variables are informational — they document the schedule the installer
burned into the cron registration. To change the schedule, re-register the
cron with a new `--schedule` flag.

```env
SOULKILLER_CHECKIN_HOUR_START=9
SOULKILLER_CHECKIN_HOUR_END=22
SOULKILLER_CHECKIN_INTERVAL_MIN=30
# Resulting cron: */30 9-22 * * *
```

```env
SOULKILLER_FOLLOWUP_CRON=soulkiller:checkin-followup
```
OpenClaw cron name that the capture hook invokes when a check-in reply is
detected. Must match the cron name registered with OpenClaw. Default is
`soulkiller:checkin-followup`.

---

## Cron identifiers (reference)

**Core pipeline**

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:extract` | `0 */2 * * *` | LLM extraction pass on new inbox messages |
| `soulkiller:checkin` | `*/30 9-22 * * *` | Gap-score probe → sends question via Telegram |
| `soulkiller:passive-scan` | `0 */6 * * *` | Scans relational-agent session transcripts |
| `soulkiller:reply-extract` | `0 */6 * * *` | LLM extraction on captured check-in replies |
| `soulkiller:synthesize` | `0 3 * * *` | Consolidates observations → trait scores + hypotheses |
| `soulkiller:profile-sync` | `30 3 * * *` | Syncs model to `subject_profile.json` and `PORTRAIT.md` |
| `soulkiller:checkin-followup` | on-demand | Acknowledges a check-in reply; triggered by capture hook |

**Daily enrichment**

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:entity-extract` | `0 4 * * *` | Entity graph extraction (people, places, topics) |
| `soulkiller:decisions` | `15 4 * * *` | Decision extraction and coherence scoring |
| `soulkiller:healthcheck` | `0 4 * * *` | Database integrity and cron freshness check |
| `soulkiller:memory` | `0 5 * * 0` | Weekly communication metrics consolidation |

**Biofeedback** (enable if hardware present)

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:biofeedback-pull` | `5 4 * * *` | Zepp/Amazfit wearable data ingestion |
| `soulkiller:biofeedback-gadgetbridge` | `10 4 * * *` | Gadgetbridge (Helio Ring) data ingestion |
| `soulkiller:biofeedback-gb-ingest` | on-demand | Single Gadgetbridge file import |
| `soulkiller:muse-aggregate` | `30 4 * * *` | Muse 2 EEG daily session aggregation |
| `soulkiller:muse-recorder` | on-demand | Muse 2 EEG single session recorder |

**Weekly analysis**

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:liwc` | `0 3 * * 0` | Psycholinguistic (LIWC) word-category analysis |
| `soulkiller:stress-index` | `0 6 * * 1` | Composite daily stress index computation |

**Optional integrations**

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:budget-bridge` | `20 4 * * *` | Actual Budget financial signal ingestion |
| `soulkiller:voicenote` | on-demand | Voice note transcription → inbox |
| `soulkiller:domain-prober` | on-demand | Targeted domain-coverage probe |
| `soulkiller:backfill` | on-demand | Past-message JSONL import |
| `soulkiller:motives` | on-demand | Implicit motive pattern inference |

**Monthly specialist analyzers**

| Cron name | Schedule | What it runs |
|---|---|---|
| `soulkiller:schemas` | `0 5 1 * *` | Early Maladaptive Schema (EMS) detection |
| `soulkiller:goals` | `30 5 1 * *` | Goal architecture and personal projects |
| `soulkiller:sdt` | `0 6 1 * *` | Self-Determination Theory need assessment |
| `soulkiller:portrait` | `0 6 1 * *` | Full narrative portrait synthesis |
| `soulkiller:idiolect` | `0 4 1 * *` | Idiolect and linguistic fingerprint |
| `soulkiller:caps` | `30 5 2 * *` | CAPS if-then behavioral signatures |
| `soulkiller:attachment` | `0 5 3 * *` | Attachment style analysis |
| `soulkiller:defenses` | `30 5 3 * *` | Defense mechanism detection |
| `soulkiller:narrative` | `0 6 3 * *` | Narrative identity and self-story structure |
| `soulkiller:appraisal` | `30 4 5 * *` | Cognitive appraisal pattern analysis |
| `soulkiller:mental-models` | `0 5 5 * *` | Mental model and reasoning heuristic extraction |
| `soulkiller:dual-process` | `30 5 5 * *` | System 1/System 2 balance estimation |
| `soulkiller:constructs` | `0 6 5 * *` | Personal constructs (Kelly's Repertory Grid) |

---

## LLM provider

```env
SOULKILLER_MODEL=claude-opus-4-6
SOULKILLER_PROVIDER=anthropic
```

The `ProviderLLMClient` stub in `src/lib/provider_llm_client.py` reads these
values but does not implement the actual call — you must provide an adapter.
See [Adapters](ADAPTERS.md) for the implementation contract.

If `SOULKILLER_MODEL` is empty, any cron that calls the LLM raises a clear
`RuntimeError` instead of a silent failure.

---

## Relational agent

```env
SOULKILLER_RELATIONAL_AGENT=my-agent
```
OpenClaw agent name used for passive observation (`soulkiller:passive-scan`)
and stress probes (`soulkiller:checkin`). The passive scanner reads this
agent's session transcripts. Leave blank to disable passive observation.

```env
SOULKILLER_RELATIONAL_AGENT_IDS=my-agent,alt-agent
```
Comma-separated list of additional agent IDs whose session directories are
also scanned. If omitted, only `SOULKILLER_RELATIONAL_AGENT` is scanned.

---

## Optional integrations

```env
SOULKILLER_ENABLE_TELEGRAM=true
```
Enables live check-in delivery via Telegram. Requires a Telegram channel
configured in OpenClaw. When `false`, the check-in cron generates the
question but does not send it.

```env
SOULKILLER_ENABLE_BIOFEEDBACK=false
```
Enables physiological signal ingestion from Zepp/Amazfit wearables.
Requires credentials below.

```env
SOULKILLER_ENABLE_MUSE=false
```
Enables Muse 2 EEG session aggregation via `soulkiller:muse-aggregate`.
Requires `TELEGRAM_BOT_TOKEN` and the Telegram log channel vars below.

```env
SOULKILLER_ZEPP_EMAIL=
SOULKILLER_ZEPP_PASSWORD=
```
Zepp/Amazfit account credentials for the biofeedback adapter. Only read
when `SOULKILLER_ENABLE_BIOFEEDBACK=true`. Store these in `.env` and keep
`.env` out of version control (it is in `.gitignore`).

```env
TELEGRAM_BOT_TOKEN=
```
Bot token for outbound Telegram messages (check-ins and Muse aggregate
notifications). Create a bot via [@BotFather](https://t.me/BotFather).
Required when `SOULKILLER_ENABLE_TELEGRAM=true` or `SOULKILLER_ENABLE_MUSE=true`.

```env
TELEGRAM_LOGS_CHAT_ID=
TELEGRAM_LOGS_THREAD_ID=
```
Telegram group/channel ID and optional topic thread ID where the Muse
aggregator sends daily EEG digests. Leave blank to suppress those
notifications without disabling the aggregator.

---

## Full `.env` example

```env
# Subject
SOULKILLER_SUBJECT_ID=alice
SOULKILLER_SUBJECT_NAME=Alice
SOULKILLER_TELEGRAM_ID=123456789

# Paths
SOULKILLER_DATA_DIR=/home/alice/.soulkiller/alice
OPENCLAW_HOME=/home/alice/.openclaw
OPENCLAW_BIN=openclaw

# Check-in schedule
SOULKILLER_CHECKIN_HOUR_START=9
SOULKILLER_CHECKIN_HOUR_END=22
SOULKILLER_CHECKIN_INTERVAL_MIN=30
SOULKILLER_FOLLOWUP_CRON=soulkiller:checkin-followup

# LLM provider (see docs/ADAPTERS.md)
SOULKILLER_MODEL=claude-opus-4-6
SOULKILLER_PROVIDER=anthropic

# Integrations
SOULKILLER_ENABLE_TELEGRAM=true
SOULKILLER_ENABLE_BIOFEEDBACK=false
SOULKILLER_ENABLE_MUSE=false
SOULKILLER_RELATIONAL_AGENT=my-agent
SOULKILLER_RELATIONAL_AGENT_IDS=

# Biofeedback credentials (only if ENABLE_BIOFEEDBACK=true)
SOULKILLER_ZEPP_EMAIL=
SOULKILLER_ZEPP_PASSWORD=

# Telegram bot (required if ENABLE_TELEGRAM=true or ENABLE_MUSE=true)
TELEGRAM_BOT_TOKEN=
TELEGRAM_LOGS_CHAT_ID=
TELEGRAM_LOGS_THREAD_ID=
```
