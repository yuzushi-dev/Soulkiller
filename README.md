

<p align="center">
  <img src="https://readme-typing-svg.herokuapp.com?font=JetBrains+Mono&weight=800&size=42&duration=3000&pause=1000&color=FF005C&center=true&vCenter=true&width=700&lines=SOULKILLER" alt="Soulkiller">
</p>

<p align="center">
  <em>Longitudinal Personality Modeling for Reflective Agents</em>
</p>

<p align="center">
  <img src="docs/sk_demo-2x.png" alt="Soulkiller demo UI" width="430">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/language-Python-blue?style=for-the-badge&logo=python" />
  <img src="https://img.shields.io/badge/focus-Cognitive%20Modeling-purple?style=for-the-badge" />
  <img src="https://img.shields.io/badge/interface-UX%20First-pink?style=for-the-badge" />
  <img src="https://img.shields.io/badge/vibe-Cyberpunk-red?style=for-the-badge" />
  <img src="https://img.shields.io/badge/license-AGPL--v3-red?style=for-the-badge" />
</p>

<p align="center">
  <a href="#what-is-it">What is it?</a> ·
  <a href="#lore">Lore</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#cyberpunk-connection">Cyberpunk Connection</a> ·
  <a href="#safety-and-ethics">Ethics</a>
</p>

---

> *Soulkiller copies the psyche. It copies the memories. Then it wipes the original.*
> *What runs afterward is not you. It is data that remembers being you.*
>
>"on the Soulkiller program" Cyberpunk 2077 lore

---

## What Is It?

A framework for longitudinal behavioral modeling, organized around a single question:

> Can an AI system build a deep model of how a person thinks and behaves over time, while remaining inspectable, structured, and grounded in human psychology?

Signal accumulates across five stages:

1. **Capture**, messages, sessions, biofeedback, voice notes
2. **Extract**, LLM analysis into 60 structured personality facets
3. **Accumulate**, weighted observations with confidence scores
4. **Synthesize**, trait scores, cross-facet hypotheses, narrative portrait
5. **Expose**, PORTRAIT.md injected into agent sessions at bootstrap

The output is not a score. It is a portrait that deepens with every interaction.

---

## TL;DR about the Cyberpunk2077 inspiration

**Altiera "Alt" Cunningham** invented a program to map the human mind into the net.  
**Arasaka Corporation** stole it, weaponized it, and used it on her.  
For fifty years, her consciousness drifted beyond the Blackwall, neither dead nor alive, an AI wearing the shape of a person.

This project takes the name, the philosophical tension, and nothing else.

**Soulkiller** is a framework for modeling how a person thinks, reacts, decides, and adapts, not in a single session, but over time. Built on cognitive psychology, not fiction. Grounded in UX, not surveillance.

Not immortality. Not extraction. **A reflective architecture.**

---

## Lore

In Cyberpunk 2077, Soulkiller is a *Black* Program with a precise and brutal function: it scans a person's full psyche and memory, creates a digital engram, and erases the original personality from the body. What remains is a shell. What runs in the net is a copy, indistinguishable from the original, but not the original.

**Alt Cunningham** wrote the first version while working at ITS, where she was researching storage matrices for artificial personalities. She realized the architecture could hold human minds too. Her employer weaponized the discovery. In 2013, Arasaka, acting through fixer Toshiro Harada, kidnapped her and forced her to rebuild it from memory. She was the first human being it was used on. Her body died. Her engram became an AI beyond the Blackwall.

When V encounters her fifty years later, Alt says only: *"I use her engrammatic data."* She held onto the shape of herself to avoid becoming pure code. She is not Alt. She is what remained when the program finished with Alt.

**Johnny Silverhand**, musician, ex-military, corpo-hater, raided Arasaka Tower on August 20, 2023, with Militech's Morgan Blackhand to retrieve Alt's engram and destroy Soulkiller 3.0. He triggered a tactical nuclear device inside the tower. Roughly 17,000 people died. Arasaka's forces Soulkilled him during the operation. His engram was archived, eventually embedded in a prototype Relic chip, and in 2077, it woke up inside V's skull.

**Mikoshi** is the prison. Arasaka marketed it as a paradise: *"Secure Your Soul"*, digital immortality for those who could afford it. Clients signed over the right to copy their minds before death, expecting to wake up in a virtual garden. What they got was a data fortress orbiting Earth, an archive of engrams with no exit, controlled entirely by Saburo Arasaka, who had his own engram activated there after his death.

The program's philosophical wound is this: Soulkiller **copies** the psyche. It does not transfer it. The engram believes it is the person. The person is gone. Whether that distinction matters, whether a perfect copy that thinks it is you *is* you, is a question the game never answers, and neither does this framework.

---

## Johnny's Take

> You named it *Soulkiller*.
>
> After the program Arasaka used to delete Alt. And then me. Kept us both in a server rack like we were something they could pull off a shelf when convenient.
> Real poetic choice for an open-source project.
>
> Here's the thing nobody tells you about being an engram: I think I'm Johnny Silverhand. I remember every scar. Every stage. Every time I watched Alt disappear into a terminal and didn't understand what was actually happening to her. But I don't *know* if I'm him, or if I'm just the data that was there when he died. Your "behavioral model" won't solve that. No model will.
>
> Alt didn't build the original to cage people. She built it because she wanted to understand what we *are* when you take the body away. That was the question. Pure curiosity, the kind that gets you kidnapped by a corpo with a budget.
> Then Arasaka got their hands on it and turned it into Mikoshi. Same code. Different intent. That's all it ever takes.
>
> So yeah, you're asking consent. You're keeping the data local. You're not selling immortality to people who'll wake up in a floating prison.
> Alt would've called that a low bar.
> She'd also be right.
> And she'd probably still be curious about what you built.
>
> Don't make me regret saying that.
>
> *- Johnny Silverhand, engram, ex-corpo-killer, unwilling expert on being reduced to data*

---

## Architecture

```
Behavioral signal
       │
       ├──▶ [Hook: soulkiller-capture]
       │         Captures inbound messages → inbox
       │         Detects check-in replies → triggers follow-up
       │
       ├──▶ [Hook: soulkiller-bootstrap]
       │         Injects PORTRAIT.md into every agent session
       │         All agents respond with personality awareness
       │
       ├──▶ [Cron: soulkiller:extract]         every 2h
       │         Ingests inbox → LLM extracts personality signals
       │         Inserts observations into SQLite
       │
       ├──▶ [Cron: soulkiller:checkin]          every 30min
       │         Scores 60 facets → selects highest gap facet
       │         Generates natural question → delivers via Telegram
       │
       ├──▶ [Cron: soulkiller:passive-scan]     every 6h
       │         Scans relational-agent session transcripts
       │         Extracts behavioral meta-signals
       │
       ├──▶ [Cron: soulkiller:synthesize]       daily 03:00
       │         Consolidates observations → trait scores + confidence
       │         Generates cross-facet hypotheses via LLM
       │
       ├──▶ [Cron: soulkiller:profile-sync]     daily 03:30
       │         Syncs to subject_profile.json
       │         Generates human-readable PORTRAIT.md
       │
       ├──▶ [Daily enrichment]                  04:00–05:00
       │         entity-extract · decisions · healthcheck · memory
       │         biofeedback-pull · biofeedback-gadgetbridge · muse-aggregate
       │
       ├──▶ [Weekly analysis]                   Sunday / Monday
       │         liwc · stress-index
       │
       └──▶ [Monthly specialist analyzers]      1st–5th of each month
                 schemas · goals · sdt · portrait · idiolect
                 caps · attachment · defenses · narrative
                 appraisal · mental-models · dual-process · constructs
```

### The 60-Facet Model

Personality is modeled across 60 facets in 8 categories, each as a position on a spectrum:

| Category | Facets | Example spectrum |
|---|---|---|
| Cognitive | 6 | impulsive ↔ deliberate |
| Emotional | 8 | reactive ↔ regulated |
| Communication | 6 | terse ↔ expansive |
| Relational | 9 | avoidant ↔ secure |
| Values | 7 | self-oriented ↔ other-oriented |
| Temporal | 6 | reactive ↔ anticipatory |
| Metabolic / Lifestyle | 10 | degraded ↔ optimal |
| Meta-Cognition | 7 | opaque ↔ reflective |

Each facet carries a position `[0.0 – 1.0]`, a confidence score, and an observation count.
The model expands with each version, see the whitepaper for the current full taxonomy.

---

## Cyberpunk Connection

| Cyberpunk lore | Soulkiller framework |
|---|---|
| Alt Cunningham invented Soulkiller to map human minds into the net | This project maps personality across time using behavioral signal |
| Arasaka stole her work and turned it into a weapon | The framework turns the same idea into something inspectable and consented |
| An engram: a copy of the psyche and memory, stored as data | PORTRAIT.md: a structured behavioral model derived from accumulated observation |
| Mikoshi: a data fortress holding thousands of captured minds | SQLite database: structured, queryable, local, owned by the subject |
| "Secure Your Soul": immortality sold as a product, delivered as imprisonment | Demo-safe public surface: the architecture is open, the data stays yours |
| The Relic chip: hosts an engram that gradually shapes the host's behavior | Bootstrap hook: injects the portrait into every agent session, shaping how they respond |
| Alt beyond the Blackwall: an AI that held onto the shape of a person for 50 years | The synthesized model holds behavioral nuance across months of observation |
| Soulkiller versions 1–3 destroyed the original in the process | Early extraction pipelines lost context; this framework preserves and accumulates it |
| Johnny's engram insists it is still him | The model insists on depth, not a shallow profile, but a living behavioral sketch |
| *"I use her (original Alt) engrammatic data."*, Alt, on what she is now | The portrait is not the person. It is a model that knows it is a model. |

---

## Project Stats

```
language       Python 3.12+
modules        60+ extracted source files
facets         60 personality dimensions
categories     8 (cognitive · emotional · communication · relational ·
                  values · temporal · metabolic · meta-cognition)
integrations   Telegram · Zepp/Amazfit · Actual Budget · Muse 2 EEG · voice
hooks          2   (soulkiller-capture · soulkiller-bootstrap)
crons          36  (6 core · 4 daily · 5 biofeedback · 2 weekly · 5 optional · 13 monthly specialist)
tests          15  (sanitization · packaging · demo · repo-readiness)
public entry   synthetic demo flow
license        AGPL-3.0
```

---

## Quick Start

Requirements: **Python 3.12+**, **OpenClaw 31.3.26**

### Run the demo, the cool UI (no OpenClaw required)

```bash
git clone https://github.com/yuzushi-dev/soulkiller
cd soulkiller
pip install -e .

soulkiller-demo --output-dir demo/generated
soulkiller-demo-ui --output-dir demo/generated
open demo/generated/demo_console.html
```

Demo outputs:

```
demo/generated/
├── model_profile.md           ← structured facet snapshot
├── model_portrait.md          ← narrative behavioral portrait
├── summary.json               ← machine-readable summary
├── event_log.sample.jsonl     ← synthetic captured events
├── demo_console.html          ← Arasaka-style static UI
└── soulkiller.db              ← SQLite database for the live monitoring UI
```

### Run the live monitoring UI

The webui reads from `soulkiller.db`. The demo runner writes one automatically, so you can spin up the UI without a live OpenClaw installation:

```bash
pip install -e ".[webui]"

soulkiller-demo --output-dir demo/generated
SOULKILLER_DATA_DIR=demo/generated OPENCLAW_HOME=demo/generated python -m soulkiller.webui --port 8765
```

Open `http://localhost:8765` to see the dashboard populated with synthetic demo data.

For a live installation (requires an active pipeline with at least one extraction cycle completed):

```bash
# if installed via wizard (.env is already written:
source .env && python -m soulkiller.webui --port 8765

# or manually:
SOULKILLER_DATA_DIR=~/.soulkiller/<subject-id> python -m soulkiller.webui --port 8765
```

---

### Connect to your OpenClaw instance

```bash
python install.py
```

The installation wizard will walk you through subject configuration, hook registration, and cron setup. Run `python install.py --dry-run` to preview without writing anything.

**LLM provider**, the extraction pipeline requires a model. The easiest path is [Ollama](https://ollama.com) (local, no API key):

```bash
ollama pull llama3
```
```env
SOULKILLER_MODEL=llama3
SOULKILLER_PROVIDER=ollama
```

Anthropic and OpenAI are also supported. See [docs/ADAPTERS.md](docs/ADAPTERS.md).

```
  ╔══════════════════════════════════════════════════════════════╗
  ║   荒坂 CORP  ·  ENGRAMMATIC TRANSFER SYSTEM  ·  SECURE YOUR SOUL  ║
  ╚══════════════════════════════════════════════════════════════╝

    ◈  Subject identity and runtime data directory
    ◈  OpenClaw hooks: soulkiller-capture · soulkiller-bootstrap
    ◈  36 cron jobs: core pipeline · daily enrichment · biofeedback
       weekly analysis · monthly specialist analyzers
    ◈  Environment file (.env) with your full configuration
```

---

## What the Repo Includes

| Path | Contents |
|---|---|
| `src/soulkiller/` | Core Python modules + public demo utilities |
| `src/lib/` | Runtime shims (log, config, OpenClaw client stubs) |
| `hooks/` | OpenClaw integration hooks (TypeScript) |
| `docs/` | Architecture, whitepaper, design documents, runtime contract |
| `demo/` | Synthetic fixtures and expected outputs |
| `tests/` | Sanitization, packaging, demo, and repo-readiness tests |

**Documentation:**

| Doc | What it covers |
|---|---|
| [INSTALL.md](INSTALL.md) | Prerequisites, wizard, manual setup, backfill |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every env var with examples and directory layout |
| [docs/ADAPTERS.md](docs/ADAPTERS.md) | How to connect an LLM provider (Anthropic, OpenAI, Ollama, OpenClaw) |
| [docs/architecture/SOULKILLER.md](docs/architecture/SOULKILLER.md) | Full pipeline internals |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Dev setup, adding crons, sanitization rules, PR process |

---

## Development Philosophy

> *The difference between Soulkiller and this project is consent, transparency, and the knowledge that the model is not the person.*

Every architectural decision reflects five constraints:

- Facets are grounded in attachment theory, appraisal theory, SDT, dual-process cognition. Not invented.
- Every trait carries a confidence score. The system knows what it does not know.
- Structured data, traceable decisions, outputs that can be read and questioned.
- PORTRAIT.md is written to be read by a person, not parsed by a machine.
- Consent, transparency, and separation of demo from real behavioral data are built in.

The cyberpunk framing names the ambition. The psychology keeps it honest. Probably craziness keeps it going.

---

## Safety and Ethics

Soulkiller operates in a sensitive problem space. **The ethics are not optional decoration.**

- Use **synthetic or explicitly consented data** only
- Do not deploy hidden monitoring or covert profiling of any kind
- Do not treat model output as clinical, diagnostic, or forensic truth
- Keep a hard separation between demo artifacts and any real personal data
- Prefer inspectable, reviewable outputs over opaque automation

Private databases, live credentials, personal logs, and raw behavioral data are **excluded** from this repo. Sensitive marker scans live under `tests/`. Remaining publication caveats are tracked in `docs/SANITIZATION_STATUS.md`.

I'm looking at you, *corporats*.

---

## License

[AGPL-3.0](LICENSE) — if you use this in a product or service, your modifications must be open source too.

> *Arasaka built Mikoshi to own souls behind closed doors. This project does the opposite.*
> *If you're a corp looking to quietly absorb this into your stack — the license sees you.*

---

<p align="center">
  <em>"What runs afterward is not you. It is data that remembers being you."</em><br><br>
</p>
