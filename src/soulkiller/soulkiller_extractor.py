#!/usr/bin/env python3
"""Soulkiller Extractor — ingests inbox.jsonl and extracts personality signals via LLM.

Cron: soulkiller:extract, every 2 hours

1. Ingests inbox.jsonl → loads new lines into inbox table
2. Batches unprocessed messages (max 10 per batch, max 2 batches per run)
3. Calls LLM for personality signal extraction
4. Inserts observations into DB
5. Correlates check-in replies
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.config import get_config, load_nanobot_config
from lib.log import info, warn, error
from lib.runtime_client import RuntimeClient
from soulkiller_facet_filter import filter_facets_by_query

SCRIPT = "soulkiller_extractor"

def _data_dir() -> Path:
    import os
    env = os.environ.get("SOULKILLER_DATA_DIR", "")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "runtime"

INBOX_PATH = _data_dir() / "inbox.jsonl"
MAX_BATCHES = 40  # More batches per run
BATCH_SIZE = 3   # 3 messages per LLM call — 3x throughput vs single-message batches
LLM_TIMEOUT_SECONDS = 180  # 3 minutes per batch
DEFAULT_MODEL = "google-aistudio/gemini-2.5-flash"  # Free via AI Studio, rate-limited only
STRICT_JSON_FALLBACK_MODELS = (
    "openrouter/qwen/qwen3.6-plus-preview:free",  # Fast fallback when Gemini hits rate limits
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",  # Slow but very reliable JSON
    "openrouter/z-ai/glm-4.5-air:free",
    "bailian/qwen3.5-plus",
)


def ingest_inbox_jsonl() -> int:
    """Load new lines from inbox.jsonl into the inbox table."""
    from soulkiller_db import get_db, ingest_inbox_line

    if not INBOX_PATH.exists():
        return 0

    conn = get_db()
    ingested = 0
    try:
        with open(INBOX_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if ingest_inbox_line(entry, conn):
                        ingested += 1
                except json.JSONDecodeError:
                    warn(SCRIPT, "invalid_jsonl_line", line=line[:100])
        conn.commit()
    finally:
        conn.close()

    if ingested > 0:
        info(SCRIPT, "inbox_ingested", count=ingested)
    return ingested


def build_extraction_prompt(messages: list[dict[str, Any]], facets: list[dict[str, Any]]) -> str:
    """Build the LLM extraction prompt for a batch of messages."""
    # SMELT Layer 4: filter facets by relevance to this batch's content
    batch_text = " ".join(m.get("content", "") for m in messages)
    relevant_facets = filter_facets_by_query(facets, batch_text)

    facet_list = []
    for f in relevant_facets:
        if f.get("spectrum_low") and f.get("spectrum_high"):
            entry = f"- {f['id']}: {f['spectrum_low']} ↔ {f['spectrum_high']}"
        else:
            entry = f"- {f['id']}"
        facet_list.append(entry)

    msg_list = []
    for i, msg in enumerate(messages):
        msg_list.append(f"[{i}] {msg['content']}")

    return f"""Analyze these messages from the subject and extract personality signals.
Return STRICT JSON object:
{{
  "signals": [
    {{
      "message_index": 0,
      "facet_id": "cognitive.decision_speed",
      "extracted_signal": "description of what this reveals",
      "signal_strength": 0.5,
      "signal_position": 0.5,
      "context": "situational context",
      "tone": "neutral"
    }}
  ]
}}

Rules:
- Not every message contains personality signals. Return {{"signals":[]}} if nothing meaningful.
- Prefer quality over quantity. Only extract signals you're confident about (strength >= 0.4).
- signal_position: 0.0 = spectrum_low end, 1.0 = spectrum_high end. Omit for list-type facets (core_values, music_taste, cognitive_biases).
- signal_strength: how confident you are this observation is meaningful (0.0-1.0).
- tone: classify the emotional tone of the source message as one of: neutral, tense, playful, serious, frustrated, warm, cold.
- Be specific in extracted_signal — quote relevant parts of the message.

Messages:
{chr(10).join(msg_list)}

Available facets with spectrums:
{chr(10).join(facet_list)}"""


def extract_signals(messages: list[dict[str, Any]], client: RuntimeClient,
                    facets: list[dict[str, Any]], model: str | None = None) -> list[dict[str, Any]] | None:
    """Call LLM to extract personality signals from a batch of messages.

    Returns list of signals on success (may be empty), or None on LLM error.
    """
    prompt = build_extraction_prompt(messages, facets)

    try:
        # Always use direct HTTP — openclaw agent path times out too often.
        # Fall back to agent only if no providers are configured.
        if not model:
            model = DEFAULT_MODEL
        signals = extract_signals_direct(messages, facets, model)

        if not isinstance(signals, list):
            warn(SCRIPT, "invalid_signals_format", result_type=type(signals).__name__)
            return None
        return signals
    except Exception as e:
        error(SCRIPT, "llm_extraction_failed", error=str(e))
        return None


def _recover_truncated_signals(json_str: str) -> list[dict[str, Any]] | None:
    """Try to recover complete signal objects from truncated JSON.

    When the model hits max_tokens, the JSON is cut mid-object.
    We find the last complete object in the signals array and return
    everything up to that point.
    """
    import json as json_lib
    import re

    # Find the signals array start
    match = re.search(r'"signals"\s*:\s*\[', json_str)
    if not match:
        return None

    array_start = match.end()

    # Try progressively shorter substrings ending at each '}' to find
    # the last point where we can close the array and outer object
    last_good = None
    for i in range(len(json_str) - 1, array_start, -1):
        if json_str[i] == '}':
            attempt = json_str[:i+1] + ']}'
            try:
                parsed = json_lib.loads(attempt)
                last_good = parsed.get("signals", [])
                break
            except json_lib.JSONDecodeError:
                continue

    if last_good is not None:
        from lib.log import info as _info
        _info("soulkiller_extractor", "truncated_json_recovered",
              recovered_signals=len(last_good))
    return last_good


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Extract the most relevant JSON object from a noisy model response."""
    import json as json_lib
    import re

    stripped = raw.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    # Drop explicit reasoning wrappers when present.
    stripped = re.sub(r"<think>.*?</think>", "", stripped, flags=re.DOTALL | re.IGNORECASE).strip()

    decoder = json_lib.JSONDecoder()
    best_obj: dict[str, Any] | None = None

    for idx, ch in enumerate(stripped):
        if ch != "{":
            continue
        try:
            candidate, _end = decoder.raw_decode(stripped[idx:])
        except json_lib.JSONDecodeError:
            continue
        if not isinstance(candidate, dict):
            continue
        if "signals" in candidate:
            return candidate
        if best_obj is None:
            best_obj = candidate

    if best_obj is not None:
        return best_obj
    raise ValueError(f"No JSON object found in response: {raw[:200]}")


def extract_signals_direct(messages: list[dict[str, Any]], facets: list[dict[str, Any]],
                           model: str) -> list[dict[str, Any]]:
    """Call LLM API directly using NanoBot provider config."""
    from lib.llm_resilience import chat_completion_content
    import json as json_lib
    
    prompt = build_extraction_prompt(messages, facets)

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": "You are a personality analysis expert. Return STRICT JSON only. No reasoning, just JSON."},
            {"role": "user", "content": prompt}
        ],
        max_tokens=4096,
        temperature=0.1,
        timeout=LLM_TIMEOUT_SECONDS,
        fallback_models=STRICT_JSON_FALLBACK_MODELS,
        title="Soulkiller Extractor",
        allow_reasoning_fallback=False,
    )
    try:
        return _extract_json_object(content).get("signals", [])
    except ValueError as e:
        start = content.find("{")
        end = content.rfind("}")
        json_str = content[start:end + 1] if start != -1 and end > start else content
        signals = _recover_truncated_signals(json_str)
        if signals is None:
            raise RuntimeError(f"JSON parse error: {e}. Content: {content[:300]}")
        return signals


def process_signals(signals: list[dict[str, Any]], messages: list[dict[str, Any]]) -> int:
    """Insert extracted signals as observations in the DB."""
    from soulkiller_db import add_observation, get_db, categorize_hour, NON_LINEAR_FACETS, get_trait
    from soulkiller_adaptive import TRUST_FACET, adjust_trust_signal_strength

    conn = get_db()
    inserted = 0
    try:
        for sig in signals:
            msg_idx = int(sig.get("message_index", 0))
            if msg_idx < 0 or msg_idx >= len(messages):
                continue

            facet_id = sig.get("facet_id", "")
            if not facet_id:
                continue

            strength = float(sig.get("signal_strength", 0.5))
            if strength < 0.4:
                continue

            msg = messages[msg_idx]
            source_ref = f"inbox:{msg.get('message_id', msg.get('id', ''))}"

            position = sig.get("signal_position")
            if facet_id in NON_LINEAR_FACETS:
                position = None
            elif position is not None:
                position = max(0.0, min(1.0, float(position)))

            # Trust asymmetry: trust_formation breaks fast, builds slowly.
            if facet_id == TRUST_FACET and position is not None:
                current_trait = get_trait(facet_id, conn)
                current_pos = (
                    float(current_trait["value_position"])
                    if current_trait and current_trait.get("value_position") is not None
                    else None
                )
                strength = adjust_trust_signal_strength(position, strength, current_pos)

            # Build context_metadata
            ctx_meta = {
                "chat_id": msg.get("channel_id", ""),
                "interlocutor_type": "ai",
                "tone": sig.get("tone", "neutral"),
            }
            received_at = msg.get("received_at", "")
            if received_at:
                try:
                    from datetime import datetime as _dt
                    dt = _dt.fromisoformat(received_at.replace("Z", "+00:00"))
                    ctx_meta["hour"] = dt.hour
                    ctx_meta["day_of_week"] = dt.strftime("%a").lower()
                    ctx_meta["time_context"] = categorize_hour(dt.hour)
                except (ValueError, TypeError):
                    pass

            obs_id = add_observation(
                facet_id=facet_id,
                source_type="passive_chat",
                source_ref=source_ref,
                content=msg.get("content", "")[:500],
                extracted_signal=sig.get("extracted_signal", ""),
                signal_strength=strength,
                signal_position=position,
                context=sig.get("context", ""),
                context_metadata=ctx_meta,
                conn=conn,
            )
            if obs_id:
                inserted += 1

        conn.commit()
    finally:
        conn.close()

    return inserted


def correlate_checkin_replies(messages: list[dict[str, Any]], client: RuntimeClient) -> int:
    """Try to match unprocessed messages to pending check-in exchanges."""
    from soulkiller_db import get_db, get_pending_checkins, capture_reply, add_observation

    pending = get_pending_checkins(hours=4.0)
    if not pending:
        return 0

    # Ambiguity policy: only auto-link if exactly 1 pending
    if len(pending) != 1:
        info(SCRIPT, "ambiguous_pending_replies", pending_count=len(pending))
        return 0

    exchange = pending[0]
    matched = 0

    for msg in messages:
        content = msg.get("content", "")
        if not content.strip():
            continue

        # Lightweight LLM YES/NO match — use direct HTTP, NOT run_agent_json
        # (run_agent_json routes through main agent and delivers JSON to Telegram DM)
        match_prompt = (
            f'Is this message a reply to the following check-in question?\n'
            f'Question: "{exchange["question_text"]}"\n'
            f'Message: "{content}"\n'
            f'Reply with STRICT JSON: {{"is_reply": true}} or {{"is_reply": false}}'
        )

        try:
            from lib.llm_resilience import chat_completion_content
            import json as _json
            _model = DEFAULT_MODEL
            _raw, _ = chat_completion_content(
                model=_model,
                messages=[
                    {"role": "system", "content": "You are a classifier. Return STRICT JSON only."},
                    {"role": "user", "content": match_prompt},
                ],
                max_tokens=32,
                temperature=0.0,
                timeout=LLM_TIMEOUT_SECONDS,
                fallback_models=STRICT_JSON_FALLBACK_MODELS,
                title="Checkin Match",
                allow_reasoning_fallback=False,
            )
            result = _extract_json_object(_raw)
            if result.get("is_reply"):
                conn = get_db()
                try:
                    capture_reply(exchange["id"], content, conn)
                    # Also create an observation from the reply
                    if exchange.get("facet_id"):
                        add_observation(
                            facet_id=exchange["facet_id"],
                            source_type="checkin_reply",
                            source_ref=f"checkin:{exchange['id']}",
                            content=content[:500],
                            extracted_signal=f"Direct reply to check-in about {exchange['facet_id']}",
                            signal_strength=0.7,
                            context=f"Reply to: {exchange['question_text'][:200]}",
                            conn=conn,
                        )
                    conn.commit()
                    matched += 1
                    info(SCRIPT, "checkin_reply_matched", exchange_id=exchange["id"])
                finally:
                    conn.close()
                break  # Only match first reply
        except Exception as e:
            warn(SCRIPT, "checkin_match_failed", error=str(e))

    return matched


def main() -> int:
    import argparse
    from datetime import datetime, timezone
    from soulkiller_db import get_db, get_pending_inbox, mark_processed, get_all_facets
    from soulkiller_adaptive import (
        load_state, save_state, should_skip_run,
        snapshot_confidences, compute_delta, compute_next_interval,
        apply_confidence_decay, advance_phase,
    )

    parser = argparse.ArgumentParser(description='Soulkiller Extractor')
    parser.add_argument('--model', type=str, default=None, help='LLM model to use')
    parser.add_argument('--force', action='store_true', help='Skip adaptive interval check')
    args = parser.parse_args()

    # ── Adaptive N: skip run if not enough time has elapsed ──────────────────
    state = load_state()
    if not args.force and should_skip_run(state):
        info(SCRIPT, "adaptive_skip",
             next_interval_h=state["next_interval_h"],
             last_delta=state["last_delta"])
        return 0

    config = get_config()
    client = RuntimeClient(config.openclaw_bin)

    # Step 1: Ingest inbox.jsonl into DB
    ingest_inbox_jsonl()

    # Snapshot confidences before extraction (for delta computation)
    conn_snap = get_db()
    confidences_before = snapshot_confidences(conn_snap)
    conn_snap.close()

    # Step 2: Get unprocessed messages
    facets = get_all_facets()
    total_extracted = 0
    total_processed = 0

    for batch_num in range(MAX_BATCHES):
        messages = get_pending_inbox(limit=BATCH_SIZE)
        if not messages:
            break

        info(SCRIPT, "processing_batch", batch=batch_num + 1, messages=len(messages))

        # Step 3: Extract personality signals
        signals = extract_signals(messages, client, facets, model=args.model)
        if signals is None:
            # LLM error — skip this batch, don't mark processed so we retry later
            warn(SCRIPT, "batch_skipped_llm_error", batch=batch_num + 1)
            break
        if signals:
            inserted = process_signals(signals, messages)
            total_extracted += inserted
            info(SCRIPT, "signals_extracted", batch=batch_num + 1, signals=len(signals), inserted=inserted)

        # Step 4: Correlate check-in replies
        correlate_checkin_replies(messages, client)

        # Step 5: Mark as processed
        inbox_ids = [msg["id"] for msg in messages]
        mark_processed(inbox_ids)
        total_processed += len(messages)

    # ── Confidence decay: apply half-life decay to unobserved facets ─────────
    conn_post = get_db()
    decay_changes = apply_confidence_decay(conn_post)
    confidences_after = snapshot_confidences(conn_post)
    conn_post.commit()
    conn_post.close()

    if decay_changes:
        info(SCRIPT, "confidence_decay_applied", changed_facets=len(decay_changes))

    # ── Update adaptive state ─────────────────────────────────────────────────
    delta = compute_delta(confidences_before, confidences_after)
    next_interval = compute_next_interval(delta)
    state["last_run_ts"] = datetime.now(timezone.utc).isoformat()
    state["last_delta"] = round(delta, 4)
    state["next_interval_h"] = round(next_interval, 1)
    state["eval_count"] = state.get("eval_count", 0) + 1

    # ── Advance relationship phase ────────────────────────────────────────────
    conn_phase = get_db()
    new_phase, phase_changed = advance_phase(state, conn_phase)
    conn_phase.close()

    if phase_changed:
        state["relationship_phase"] = new_phase
        state["phase_changed_at"] = datetime.now(timezone.utc).isoformat()
        info(SCRIPT, "phase_transition",
             old_phase=state.get("relationship_phase"), new_phase=new_phase)

    save_state(state)

    info(SCRIPT, "run_complete",
         total_processed=total_processed,
         total_extracted=total_extracted,
         delta=round(delta, 4),
         next_interval_h=next_interval,
         phase=state["relationship_phase"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
