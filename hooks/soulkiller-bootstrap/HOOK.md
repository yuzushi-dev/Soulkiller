---
name: soulkiller-bootstrap
description: "Injects current personality model into agent bootstrap context"
metadata: {"openclaw":{"emoji":"🧬","events":["agent:bootstrap"]}}
---

# Soulkiller Bootstrap Hook

Injects the current Soulkiller personality model into every agent session's
bootstrap context, so all agents are personality-aware without extra prompting.

## What It Does

- Fires on `agent:bootstrap` (before system prompt is finalized)
- Reads `workspace/soulkiller/PORTRAIT.md` (full personality model)
- Filters it with SMELT Layer 4 using the subject's last `inbox.jsonl` message as the
  retrieval query — only relevant sections are injected (typical reduction 60-95%)
- Falls back to the full PORTRAIT.md content if no message is available
- Injects the result as a virtual bootstrap file `PERSONALITY_MODEL.md`
- Skips sub-agent sessions and soulkiller-internal sessions to avoid context bloat

## Shared modules

- `shared/smelt-retrieval.ts` — SMELT Layer 4 TF-IDF retrieval (`filterByQuery`)
- `shared/last-message.ts` — reads `inbox.jsonl`, returns latest message the configured subject

## Configuration

No configuration needed. Enable with:

```bash
openclaw hooks enable soulkiller-bootstrap
```
