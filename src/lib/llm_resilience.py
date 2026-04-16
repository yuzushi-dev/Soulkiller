"""llm_resilience - resilient LLM call wrapper for Soulkiller OSS.

Wraps ProviderLLMClient with exponential backoff retry logic.
All soulkiller_* modules that need LLM completions import from here.

Usage:
    from lib.llm_resilience import chat_completion_content

    content, meta = chat_completion_content(
        model="claude-opus-4-6",
        messages=[{"role": "user", "content": "..."}],
        max_tokens=2048,
        temperature=0.2,
        timeout=60,
        title="My Script",
    )
"""
from __future__ import annotations

import time
from typing import Any

from lib.log import info, warn, error
from lib.provider_llm_client import ProviderLLMClient

# ── Retry config ───────────────────────────────────────────────────────────────

_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = [2, 4, 8]


def chat_completion_content(
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 2048,
    temperature: float = 0.2,
    timeout: int = 60,
    title: str = "soulkiller",
    fallback_models: list[str] | None = None,
    allow_reasoning_fallback: bool = True,
) -> tuple[str, dict[str, Any]]:
    """Call the configured LLM provider with retry and optional fallback models.

    Args:
        model:                   Primary model to use.
        messages:                Chat messages list (role/content dicts).
        max_tokens:              Maximum tokens in the response.
        temperature:             Sampling temperature.
        timeout:                 Per-attempt timeout in seconds (passed to provider).
        title:                   Script name for structured log output.
        fallback_models:         Ordered list of model names to try if primary fails.
        allow_reasoning_fallback: If False, skip fallback on reasoning-model errors.

    Returns:
        Tuple of (content: str, metadata: dict).
        metadata contains: model_used, attempts, fallback_used.
    """
    # Build the prompt string from messages (providers that need a single string)
    # ProviderLLMClient.complete() takes a prompt; we build it here.
    prompt = _messages_to_prompt(messages)

    models_to_try = [model] + (fallback_models or [])
    last_exc: Exception | None = None

    for model_candidate in models_to_try:
        client = ProviderLLMClient(model=model_candidate)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                info(
                    script=title,
                    event="llm_call",
                    model=model_candidate,
                    attempt=attempt,
                    max_tokens=max_tokens,
                )
                content = client.complete(prompt)
                meta: dict[str, Any] = {
                    "model_used": model_candidate,
                    "attempts": attempt,
                    "fallback_used": model_candidate != model,
                }
                return content, meta

            except Exception as exc:
                last_exc = exc
                is_last_attempt = attempt == _MAX_ATTEMPTS
                is_last_model = model_candidate == models_to_try[-1]

                if not allow_reasoning_fallback and _is_reasoning_error(exc):
                    warn(
                        script=title,
                        event="llm_reasoning_error",
                        model=model_candidate,
                        error=str(exc)[:200],
                    )
                    break  # skip retries for this model, move to fallback

                if is_last_attempt:
                    warn(
                        script=title,
                        event="llm_attempts_exhausted",
                        model=model_candidate,
                        error=str(exc)[:200],
                    )
                    break  # move to fallback model

                backoff = _BACKOFF_SECONDS[attempt - 1]
                warn(
                    script=title,
                    event="llm_retry",
                    model=model_candidate,
                    attempt=attempt,
                    backoff_s=backoff,
                    error=str(exc)[:200],
                )
                time.sleep(backoff)

    error(
        script=title,
        event="llm_all_models_failed",
        models_tried=models_to_try,
        error=str(last_exc)[:300],
    )
    raise RuntimeError(
        f"[{title}] All LLM attempts failed after trying {models_to_try}. "
        f"Last error: {last_exc}"
    ) from last_exc


# ── Helpers ────────────────────────────────────────────────────────────────────

def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
    """Flatten a chat messages list into a single prompt string.

    System message is prepended as-is; user/assistant turns are formatted
    with role labels so the model retains conversational context.
    """
    parts: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            parts.append(content)
        elif role == "assistant":
            parts.append(f"\nAssistant: {content}")
        else:
            parts.append(f"\nHuman: {content}")
    return "\n".join(parts).strip()


def _is_reasoning_error(exc: Exception) -> bool:
    """Return True if the exception looks like a reasoning-model refusal."""
    msg = str(exc).lower()
    return any(k in msg for k in ("thinking", "reasoning", "extended output", "budget"))
