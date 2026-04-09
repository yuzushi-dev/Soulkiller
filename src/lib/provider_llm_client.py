"""ProviderLLMClient — multi-provider LLM adapter for Soulkiller OSS.

Dispatches to the correct provider based on SOULKILLER_PROVIDER (or inferred
from the model name). Supported providers:

  anthropic   — requires: pip install anthropic  + ANTHROPIC_API_KEY
  openai      — requires: pip install openai     + OPENAI_API_KEY
  ollama      — no extra deps (stdlib urllib)    + Ollama running locally
  openclaw    — delegates to the openclaw CLI    + OPENCLAW_BIN in PATH

Set in .env:

  SOULKILLER_MODEL=claude-opus-4-6
  SOULKILLER_PROVIDER=anthropic        # or openai / ollama / openclaw
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


# ── Provider inference ─────────────────────────────────────────────────────────

def _infer_provider(model: str) -> str:
    m = model.lower()
    # Local-first: common Ollama model prefixes
    if m.startswith(("llama", "mistral", "gemma", "phi", "qwen", "deepseek",
                      "codellama", "wizardlm", "dolphin", "nous")):
        return "ollama"
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    # Unknown model — default to ollama (works for any model pulled locally)
    return "ollama"


# ── Anthropic ──────────────────────────────────────────────────────────────────

def _complete_anthropic(model: str, prompt: str) -> str:
    try:
        import anthropic  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "anthropic SDK not installed. Run: pip install anthropic\n"
            "Then set ANTHROPIC_API_KEY in your environment or .env file."
        ) from None

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    message = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


# ── OpenAI ─────────────────────────────────────────────────────────────────────

def _complete_openai(model: str, prompt: str) -> str:
    try:
        import openai  # type: ignore[import]
    except ImportError:
        raise RuntimeError(
            "openai SDK not installed. Run: pip install openai\n"
            "Then set OPENAI_API_KEY in your environment or .env file."
        ) from None

    client = openai.OpenAI()  # reads OPENAI_API_KEY
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.choices[0].message.content or ""


# ── Ollama (stdlib, no extra deps) ─────────────────────────────────────────────

def _complete_ollama(model: str, prompt: str) -> str:
    base = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    url = f"{base}/api/generate"
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Ollama request failed: {exc}\n"
            f"Is Ollama running at {base}? Start it with: ollama serve"
        ) from exc
    return data.get("response", "")


# ── OpenClaw (delegates to CLI) ────────────────────────────────────────────────

def _complete_openclaw(model: str, prompt: str) -> str:
    import subprocess

    bin_path = os.environ.get("OPENCLAW_BIN", "openclaw")
    agent = os.environ.get("SOULKILLER_RELATIONAL_AGENT", "")
    cmd = [bin_path, "agent", "run"]
    if agent:
        cmd += ["--agent", agent]
    if model:
        cmd += ["--model", model]
    cmd += ["--message", prompt, "--json"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError(
            f"OpenClaw binary not found: '{bin_path}'. "
            "Set OPENCLAW_BIN in your .env file."
        ) from None
    if result.returncode != 0:
        raise RuntimeError(
            f"openclaw agent run failed (exit {result.returncode}): "
            f"{(result.stderr or result.stdout).strip()[:200]}"
        )
    try:
        return json.loads(result.stdout).get("response", result.stdout.strip())
    except json.JSONDecodeError:
        return result.stdout.strip()


# ── Public client ──────────────────────────────────────────────────────────────

class ProviderLLMClient:
    """Multi-provider LLM client. Reads SOULKILLER_MODEL and SOULKILLER_PROVIDER."""

    def __init__(self, model: str | None = None, provider: str | None = None) -> None:
        self.model = model or os.environ.get("SOULKILLER_MODEL", "")
        self.provider = provider or os.environ.get("SOULKILLER_PROVIDER", "")

    def complete(self, prompt: str, **kwargs: Any) -> str:
        """Send a completion request and return the response text."""
        if not self.model:
            raise RuntimeError(
                "SOULKILLER_MODEL is not set. "
                "Add it to your .env file (e.g. SOULKILLER_MODEL=claude-opus-4-6)."
            )

        provider = (self.provider or _infer_provider(self.model)).lower()

        if provider == "ollama":
            return _complete_ollama(self.model, prompt)
        if provider == "anthropic":
            return _complete_anthropic(self.model, prompt)
        if provider in ("openai", "openai-compatible"):
            return _complete_openai(self.model, prompt)
        if provider == "openclaw":
            return _complete_openclaw(self.model, prompt)

        raise RuntimeError(
            f"Unknown provider '{provider}' for model='{self.model}'.\n"
            f"Set SOULKILLER_PROVIDER to one of: ollama, anthropic, openai, openclaw.\n"
            f"See docs/ADAPTERS.md for setup instructions."
        )
