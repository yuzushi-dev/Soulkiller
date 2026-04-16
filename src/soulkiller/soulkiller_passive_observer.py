#!/usr/bin/env python3
"""Soulkiller Passive Observer - extracts personality signals from session transcripts.

Scans OpenClaw relational-agent session transcripts for behavioral signals in the subject's messages
and interaction patterns. Focuses on meta-signals (communication patterns,
decision signals, emotional markers) rather than raw content already captured
by the inbox hook.

Cron: soulkiller:passive-scan, every 6 hours
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from lib.config import get_config, load_nanobot_config, openclaw_home
from lib.log import info, warn, error
from lib.runtime_client import RuntimeClient

SCRIPT = "soulkiller_passive_observer"
DEFAULT_RELATIONAL_AGENT_IDS: list[str] = []  # set via SOULKILLER_RELATIONAL_AGENT env var
STATE_FILE = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "passive-observer-state.json"
LOOKBACK_HOURS = 12
MAX_MESSAGES_PER_RUN = 20
MAX_MESSAGES_PER_SESSION = 10
MAX_SESSION_SIZE_MB = 3  # Skip session files larger than this
RUN_TIMEOUT_SECONDS = 500  # 8+ minutes hard cap for the loop (cron timeout=600s, leave room for in-flight LLM)
LLM_TIMEOUT_SECONDS = 120  # Per-call LLM timeout
BATCH_SIZE = 10
# Direct LLM model - NEVER route extractions through the relational agent session:
# doing so injects prompts into the main conversation and causes context bloat.
PASSIVE_DEFAULT_MODEL = "google-aistudio/gemini-2.5-flash"


def resolve_session_dirs() -> list[Path]:
    raw = os.environ.get("SOULKILLER_RELATIONAL_AGENT_IDS", "")
    agent_ids = [part.strip() for part in raw.split(",") if part.strip()] or DEFAULT_RELATIONAL_AGENT_IDS
    return [openclaw_home() / "agents" / agent_id / "sessions" for agent_id in agent_ids]


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"processed_sessions": {}, "last_run_at": None}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"processed_sessions": {}, "last_run_at": None}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    # Prune entries older than 7 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    processed = state.get("processed_sessions", {})
    state["processed_sessions"] = {
        k: v for k, v in processed.items()
        if v.get("scanned_at", "") > cutoff
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def extract_user_messages(session_path: Path, last_offset: int = 0) -> list[dict[str, Any]]:
    """Extract user messages from a session JSONL file, starting from offset."""
    messages: list[dict[str, Any]] = []
    current_offset = 0

    with session_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            current_offset += 1
            if current_offset <= last_offset:
                continue

            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("_type") == "metadata":
                continue

            if obj.get("type") == "message":
                msg = obj.get("message", {})
            else:
                msg = obj
            if msg.get("role") != "user":
                continue

            # Extract text content
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                content = " ".join(text_parts)

            if not content or len(content.strip()) < 10:
                continue

            # Strip injected context prefixes to find actual user content
            stripped = content.strip()
            # Remove amber context block
            if stripped.startswith("<!-- amber-ctx-start -->"):
                end_marker = "<!-- amber-ctx-end -->"
                idx = stripped.find(end_marker)
                if idx != -1:
                    stripped = stripped[idx + len(end_marker):].strip()
            # Remove cron prefix
            if stripped.startswith("[cron:"):
                bracket_end = stripped.find("]")
                if bracket_end != -1:
                    stripped = stripped[bracket_end + 1:].strip()
            # Remove other system/injected prefixes
            for prefix in ["[system", "<system", "[AMBER", "System:", "Read HEARTBEAT", "Continue where you left off", "You are the"]:
                if stripped.startswith(prefix):
                    stripped = ""
                    break
            
            # Skip if no actual user content remains
            if not stripped or len(stripped) < 10:
                continue

            messages.append({
                "content": content[:1000],
                "offset": current_offset,
                "session_id": session_path.stem,
                "timestamp": obj.get("timestamp", ""),
            })

    return messages


def extract_behavioral_patterns(session_path: Path, last_offset: int = 0) -> list[dict[str, Any]]:
    """Extract behavioral meta-signals from a session (accept/reject, corrections, etc.)."""
    patterns: list[dict[str, Any]] = []
    prev_assistant_action = None
    current_offset = 0

    with session_path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            current_offset += 1
            if current_offset <= last_offset:
                continue

            line = line.strip()
            if not line or not line.startswith("{"):
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if obj.get("_type") == "metadata":
                continue

            if obj.get("type") == "message":
                msg = obj.get("message", {})
            else:
                msg = obj
            role = msg.get("role", "")

            # Track assistant proposals (tool calls)
            if role == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "toolCall":
                            prev_assistant_action = block.get("name", "unknown")
                elif isinstance(content, str) and content.strip():
                    prev_assistant_action = "assistant_reply"
                continue

            # Detect user responses to assistant actions
            if role == "user" and prev_assistant_action:
                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = [b.get("text", "") if isinstance(b, dict) else str(b) for b in content]
                    content = " ".join(text_parts)

                if content:
                    # Skip system/injected content
                    stripped = content.strip()
                    if (stripped.startswith("<!-- amber") or stripped.startswith("[cron:")
                            or stripped.startswith("[AMBER") or stripped.startswith("[system")
                            or stripped.startswith("<system") or stripped.startswith("System:")
                            or stripped.startswith("Read HEARTBEAT") or stripped.startswith("Continue where you left off")
                            or stripped.startswith("You are the")):
                        prev_assistant_action = None
                        continue

                    patterns.append({
                        "type": "user_response_to_action",
                        "action": prev_assistant_action,
                        "response": content[:500],
                        "offset": current_offset,
                        "session_id": session_path.stem,
                    })
                prev_assistant_action = None

    return patterns


def build_passive_prompt(messages: list[dict[str, Any]], patterns: list[dict[str, Any]],
                         facets: list[dict[str, Any]]) -> str:
    """Build the LLM prompt for passive behavioral signal extraction."""
    facet_list = []
    for f in facets:
        if f.get("spectrum_low") and f.get("spectrum_high"):
            entry = f"- {f['id']}: {f['spectrum_low']} ↔ {f['spectrum_high']}"
        else:
            entry = f"- {f['id']}"
        facet_list.append(entry)

    msg_section = ""
    if messages:
        msg_lines = [f"[msg-{i}] {m['content'][:300]}" for i, m in enumerate(messages)]
        msg_section = "User messages:\n" + "\n".join(msg_lines)

    pattern_section = ""
    if patterns:
        pat_lines = [f"[pat-{i}] After assistant {p['action']}: \"{p['response'][:200]}\"" for i, p in enumerate(patterns)]
        pattern_section = "\nBehavioral patterns (user responses to agent actions):\n" + "\n".join(pat_lines)

    return f"""Analyze these interaction signals from the subject and extract personality/behavioral signals.
Focus on META-SIGNALS: communication style, decision patterns, emotional tone, preferences expressed through behavior.
Do NOT extract surface-level content (what they said) - extract HOW they said it and WHAT it reveals about personality.

Return STRICT JSON:
{{
  "signals": [
    {{
      "source_index": "msg-0 or pat-0",
      "facet_id": "communication.directness",
      "extracted_signal": "what this reveals about personality",
      "signal_strength": 0.5,
      "signal_position": 0.5,
      "context": "situational context",
      "tone": "neutral"
    }}
  ]
}}

Rules:
- Only extract signals with strength >= 0.4
- Focus on behavioral patterns, not raw content
- For list-type facets (core_values, music_taste, cognitive_biases), omit signal_position
- tone: classify the emotional tone of the source interaction as one of: neutral, tense, playful, serious, frustrated, warm, cold
- Return {{"signals":[]}} if no meaningful personality signals found

{msg_section}
{pattern_section}

Available personality facets:
{chr(10).join(facet_list)}"""


def process_passive_signals(signals: list[dict[str, Any]], messages: list[dict[str, Any]],
                           patterns: list[dict[str, Any]], session_id: str) -> int:
    """Insert passive observation signals into the DB."""
    from soulkiller_db import add_observation, get_db, categorize_hour, NON_LINEAR_FACETS

    conn = get_db()
    inserted = 0
    try:
        for sig in signals:
            facet_id = sig.get("facet_id", "")
            if not facet_id:
                continue

            strength = float(sig.get("signal_strength", 0.5))
            if strength < 0.4:
                continue

            source_index = sig.get("source_index", "")
            content = ""
            msg_timestamp = ""

            # Resolve source reference
            if source_index.startswith("msg-"):
                try:
                    idx = int(source_index.split("-")[1])
                    if 0 <= idx < len(messages):
                        msg = messages[idx]
                        content = msg.get("content", "")[:500]
                        offset = msg.get("offset", 0)
                        msg_timestamp = msg.get("timestamp", "")
                except (ValueError, IndexError):
                    offset = 0
            elif source_index.startswith("pat-"):
                try:
                    idx = int(source_index.split("-")[1])
                    if 0 <= idx < len(patterns):
                        pat = patterns[idx]
                        content = f"After {pat['action']}: {pat['response'][:300]}"
                        offset = pat.get("offset", 0)
                except (ValueError, IndexError):
                    offset = 0
            else:
                offset = 0

            source_ref = f"session:{session_id}:{offset}"

            position = sig.get("signal_position")
            if facet_id in NON_LINEAR_FACETS:
                position = None
            elif position is not None:
                position = max(0.0, min(1.0, float(position)))

            # Build context_metadata
            ctx_meta: dict[str, Any] = {
                "chat_id": f"agent:main",
                "interlocutor_type": "ai",
                "tone": sig.get("tone", "neutral"),
            }
            if msg_timestamp:
                try:
                    dt = datetime.fromisoformat(msg_timestamp.replace("Z", "+00:00"))
                    ctx_meta["hour"] = dt.hour
                    ctx_meta["day_of_week"] = dt.strftime("%a").lower()
                    ctx_meta["time_context"] = categorize_hour(dt.hour)
                except (ValueError, TypeError):
                    pass

            obs_id = add_observation(
                facet_id=facet_id,
                source_type="session_behavioral",
                source_ref=source_ref,
                content=content,
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


def check_caps_predictions(session_text: str) -> int:
    """Check active CAPS predictions against session content; update confirmations/disconfirmations (IMP-07)."""
    import re
    import sqlite3
    DB_PATH_LOCAL = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
    db = sqlite3.connect(str(DB_PATH_LOCAL))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    try:
        preds = db.execute("SELECT id, signature_id, pattern_regex FROM caps_predictions").fetchall()
        if not preds:
            db.close()
            return 0

        text_lower = session_text.lower()
        updated = 0
        for pred in preds:
            regex = pred["pattern_regex"]
            if not regex:
                continue
            # Pattern is pipe-separated Italian keywords
            pattern = "|".join(re.escape(kw.strip()) for kw in regex.split("|") if kw.strip())
            if not pattern:
                continue
            matched = bool(re.search(pattern, text_lower))
            if matched:
                db.execute(
                    "UPDATE caps_predictions SET confirmations=confirmations+1 WHERE id=?",
                    (pred["id"],),
                )
            else:
                db.execute(
                    "UPDATE caps_predictions SET disconfirmations=disconfirmations+1 WHERE id=?",
                    (pred["id"],),
                )
            updated += 1

        # Update caps_signatures.strength: strength_new = strength_old * 0.9 + confirmation_rate * 0.1
        sig_ids = {p["signature_id"] for p in preds}
        for sig_id in sig_ids:
            sig_preds = db.execute(
                "SELECT confirmations, disconfirmations FROM caps_predictions WHERE signature_id=?",
                (sig_id,),
            ).fetchall()
            total = sum(p["confirmations"] + p["disconfirmations"] for p in sig_preds)
            if total == 0:
                continue
            confirmed = sum(p["confirmations"] for p in sig_preds)
            rate = confirmed / total
            db.execute(
                "UPDATE caps_signatures SET confidence = confidence * 0.9 + ? * 0.1 WHERE id=?",
                (rate, sig_id),
            )

        db.commit()
        return updated
    finally:
        db.close()


def _call_llm_direct(prompt: str, model: str) -> dict:
    """Call LLM API directly using NanoBot provider config."""
    import json as json_lib
    from lib.llm_resilience import chat_completion_content

    content, _ = chat_completion_content(
        model=model,
        messages=[
            {"role": "system", "content": "You are a personality analysis expert. Return STRICT JSON only. No reasoning, just JSON."},
            {"role": "user", "content": prompt},
        ],
        max_tokens=4096,
        temperature=0.1,
        timeout=LLM_TIMEOUT_SECONDS,
        title="Soulkiller Passive Observer",
    )

    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.endswith("```"):
            stripped = stripped[:-3].strip()

    return json_lib.loads(stripped)


def extract_git_signals(repo_path: str | None = None) -> dict | None:
    """Extract git commit frequency as proxy for work rhythm and deadline behavior (IMP-13).

    Returns a dict with commit metrics if a repo path is configured, else None.
    """
    import subprocess
    import collections

    if not repo_path:
        return None

    try:
        result = subprocess.run(
            ["git", "-C", repo_path, "log", "--format=%ai %s", "--since=30 days ago", "--author-date-order"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None

        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        if not lines:
            return None

        hours: list[int] = []
        days_of_week: list[int] = []
        for line in lines:
            parts = line.split(" ", 2)
            if len(parts) < 2:
                continue
            try:
                dt = datetime.fromisoformat(parts[0] + "T" + parts[1].split("+")[0].split("-")[0])
                hours.append(dt.hour)
                days_of_week.append(dt.weekday())  # 0=Monday
            except (ValueError, IndexError):
                continue

        if not hours:
            return None

        hour_dist = collections.Counter(hours)
        dow_dist = collections.Counter(days_of_week)
        late_night = sum(1 for h in hours if h >= 22 or h < 5)

        return {
            "total_commits_30d": len(lines),
            "peak_hour": max(hour_dist, key=hour_dist.get),
            "late_night_commits_pct": round(100 * late_night / len(hours), 1),
            "weekend_commits_pct": round(100 * (dow_dist.get(5, 0) + dow_dist.get(6, 0)) / len(days_of_week), 1),
            "commits_per_week": round(len(lines) / 4.3, 1),
        }
    except Exception:
        return None


def main() -> int:
    import argparse
    import time as _time
    from soulkiller_db import get_all_facets

    parser = argparse.ArgumentParser(description='Soulkiller Passive Observer')
    parser.add_argument('--model', type=str, default=None, help='LLM model (e.g., nvidia_nim/z-ai/glm4.7)')
    parser.add_argument('--git-repo', type=str, default=None, help='Optional git repo path for commit frequency analysis (IMP-13)')
    args = parser.parse_args()

    run_start = _time.monotonic()

    config = get_config()
    client = RuntimeClient(config.openclaw_bin)
    state = load_state()

    # Find recent conversational session files from configured relational agents.
    configured_session_dirs = resolve_session_dirs()
    session_dirs = [p for p in configured_session_dirs if p.exists()]
    if not session_dirs:
        info(SCRIPT, "no_sessions_dir", configured=[str(p) for p in configured_session_dirs])
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    session_files: list[Path] = []
    for session_dir in session_dirs:
        session_files.extend(session_dir.glob("*.jsonl"))
    session_files = sorted(session_files, key=lambda p: p.stat().st_mtime, reverse=True)

    facets = get_all_facets()
    total_extracted = 0
    total_messages_scanned = 0
    processed_sessions = state.get("processed_sessions", {})

    for session_file in session_files:
        # Hard time cap
        if _time.monotonic() - run_start > RUN_TIMEOUT_SECONDS:
            warn(SCRIPT, "run_timeout", elapsed=int(_time.monotonic() - run_start))
            break

        if session_file.stat().st_mtime < cutoff.timestamp():
            break

        # Skip huge session files
        file_size_mb = session_file.stat().st_size / (1024 * 1024)
        if file_size_mb > MAX_SESSION_SIZE_MB:
            info(SCRIPT, "skipping_large_session",
                 session=session_file.stem, size_mb=round(file_size_mb, 1))
            continue

        session_id = session_file.stem
        last_offset = processed_sessions.get(session_id, {}).get("last_offset", 0)

        # Extract user messages and behavioral patterns
        messages = extract_user_messages(session_file, last_offset)
        patterns = extract_behavioral_patterns(session_file, last_offset)

        if not messages and not patterns:
            continue

        # Cap per session
        messages = messages[:MAX_MESSAGES_PER_SESSION]
        patterns = patterns[:MAX_MESSAGES_PER_SESSION]

        total_messages_scanned += len(messages)

        # Cap total messages per run
        if total_messages_scanned > MAX_MESSAGES_PER_RUN:
            break

        info(SCRIPT, "scanning_session", session=session_id,
             messages=len(messages), patterns=len(patterns))

        # Build and send extraction prompt
        prompt = build_passive_prompt(messages, patterns, facets)
        signals = []
        try:
            model = args.model if (args.model and '/' in args.model) else PASSIVE_DEFAULT_MODEL
            result = _call_llm_direct(prompt, model)
            signals = result.get("signals", [])
            if signals:
                inserted = process_passive_signals(signals, messages, patterns, session_id)
                total_extracted += inserted
                info(SCRIPT, "signals_extracted", session=session_id,
                     signals=len(signals), inserted=inserted)
        except Exception as e:
            warn(SCRIPT, "extraction_failed", session=session_id, error=str(e))

        # IMP-07: check CAPS predictions against session content
        if signals:
            session_text = " ".join(m.get("content", "") for m in messages)
            check_caps_predictions(session_text)

        # Update state with max offset
        max_offset = 0
        if messages:
            max_offset = max(m["offset"] for m in messages)
        if patterns:
            max_offset = max(max_offset, max(p["offset"] for p in patterns))

        processed_sessions[session_id] = {
            "last_offset": max(last_offset, max_offset),
            "scanned_at": datetime.now(timezone.utc).isoformat(),
        }

    state["processed_sessions"] = processed_sessions
    save_state(state)

    # IMP-13: optional git commit frequency analysis
    if args.git_repo:
        git_metrics = extract_git_signals(args.git_repo)
        if git_metrics:
            info(SCRIPT, "git_signals", **git_metrics)

    elapsed = int(_time.monotonic() - run_start)
    info(SCRIPT, "run_complete",
         elapsed_seconds=elapsed,
         sessions_scanned=len([s for s in processed_sessions.values()
                               if s.get("scanned_at", "") > cutoff.isoformat()]),
         messages_scanned=total_messages_scanned,
         signals_extracted=total_extracted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
