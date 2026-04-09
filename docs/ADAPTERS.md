# Adapters

Soulkiller's extraction, check-in, and synthesis crons all call an LLM via
`ProviderLLMClient` in `src/lib/provider_llm_client.py`. The client dispatches
to the correct backend based on `SOULKILLER_PROVIDER` (or inferred from the
model name). No code changes needed — just set env vars.

---

## Quick setup

### Ollama — recommended (local, no API key, no extra deps)

The default provider. Works with any model you have pulled locally.
No API key. No Python SDK. All network calls use stdlib `urllib`.

[Install Ollama](https://ollama.com), then:

```bash
ollama serve                    # start the server (if not already running)
ollama pull llama3              # or any supported model
```

```env
# .env
SOULKILLER_MODEL=llama3
SOULKILLER_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434   # default, can omit
```

If `SOULKILLER_PROVIDER` is not set and the model name is not recognized,
Ollama is the fallback — so `SOULKILLER_MODEL=llama3` alone is enough.

### Anthropic (Claude)

```bash
pip install -e ".[anthropic]"
export ANTHROPIC_API_KEY=sk-ant-...
```

```env
SOULKILLER_MODEL=claude-opus-4-6
SOULKILLER_PROVIDER=anthropic
```

### OpenAI (GPT / o-series)

```bash
pip install -e ".[openai]"
export OPENAI_API_KEY=sk-...
```

```env
SOULKILLER_MODEL=gpt-4o
SOULKILLER_PROVIDER=openai
```

### OpenClaw (delegates to CLI)

If your OpenClaw instance manages model access:

```env
SOULKILLER_MODEL=claude-opus-4-6        # passed to openclaw agent run
SOULKILLER_PROVIDER=openclaw
SOULKILLER_RELATIONAL_AGENT=my-agent   # optional: specific agent to use
OPENCLAW_BIN=openclaw                   # path to binary
```

---

## Provider inference

If `SOULKILLER_PROVIDER` is not set, the client infers the provider from the
model name:

| Model prefix | Inferred provider |
|---|---|
| `claude-*` | `anthropic` |
| `gpt-*`, `o1-*`, `o3-*`, `o4-*` | `openai` |
| `llama*`, `mistral*`, `gemma*`, `phi*`, `qwen*`, `deepseek*` | `ollama` |
| anything else | error — set `SOULKILLER_PROVIDER` explicitly |

---

## What each cron uses the LLM for

| Cron | Purpose | Typical prompt length |
|---|---|---|
| `soulkiller:extract` | Analyze a message for personality signals → structured observations | 400–800 tokens |
| `soulkiller:checkin` | Generate a natural check-in question targeting a specific facet | 300–600 tokens |
| `soulkiller:passive-scan` | Extract behavioral meta-signals from session transcripts | 600–1200 tokens |
| `soulkiller:synthesize` | Cross-facet hypothesis generation from accumulated observations | 800–2000 tokens |

`soulkiller:profile-sync` does not call the LLM — it formats existing data.

---

## Verifying the adapter

After configuration, test the connection:

```bash
PYTHONPATH=src python -c "
from lib.provider_llm_client import ProviderLLMClient
c = ProviderLLMClient()
print(c.complete('Reply with exactly: adapter ok'))
"
```

Run the demo pipeline (no LLM, no network) to confirm imports are clean:

```bash
python -m soulkiller.demo_runner --output-dir demo/generated
```

---

## Custom providers

If you need a provider not listed above, subclass or replace `ProviderLLMClient`.
The only interface the crons use is:

```python
def complete(self, prompt: str, **kwargs) -> str:
    ...  # return the model response as a plain string
```
