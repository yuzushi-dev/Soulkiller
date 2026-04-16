#!/usr/bin/env python3
"""Soulkiller Idiolect Model - Linguistic fingerprint profiling.

Computes the subject's unique linguistic signature from inbox messages:
vocabulary richness, sentence patterns, style markers, n-grams,
formulaic sequences, code-switching.

NO LLM - purely programmatic analysis (like LIWC).

Cron: soulkiller:idiolect, monthly 1st 04:00 Europe/Rome

Usage:
  python3 soulkiller_idiolect.py [--period YYYY-MM] [--all] [--dry-run]
"""
from __future__ import annotations
import os

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lib.log import info, warn

SCRIPT = "soulkiller_idiolect"
DB_PATH = Path(os.environ.get("SOULKILLER_DATA_DIR") or str(Path(__file__).resolve().parents[1] / "soulkiller")) / "soulkiller.db"
SUBJECT_FROM_ID = "demo-subject"

# Common Italian filler/opener patterns
FILLER_RE = re.compile(
    r'\b(comunque|praticamente|tipo|insomma|boh|mah|vabbe|vabbè|cioè|diciamo|'
    r'appunto|fondamentalmente|basically|ecco|niente)\b', re.I
)
# Reply-header patterns to strip before computing any metric (§31.1 bug fix)
REPLY_HEADER_RE = re.compile(
    r'^(In risposta a .+:|Replying to .+:|>)',
    re.IGNORECASE
)

EMOJI_RE = re.compile(
    r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF'
    r'\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF'
    r'\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]'
)
ENGLISH_WORDS = {
    "the", "is", "are", "was", "were", "have", "has", "had", "do", "does",
    "did", "will", "would", "could", "should", "can", "may", "might",
    "this", "that", "these", "those", "with", "from", "about", "into",
    "through", "during", "before", "after", "above", "below", "between",
    "but", "and", "or", "not", "just", "also", "very", "really", "actually",
    "anyway", "like", "well", "right", "ok", "okay", "yes", "no", "yeah",
    "cool", "nice", "great", "good", "bad", "sure", "maybe", "please",
    "thanks", "sorry", "because", "so", "then", "now", "here", "there",
    "what", "when", "where", "who", "how", "why", "which",
    "think", "know", "want", "need", "feel", "look", "make", "take",
    "come", "go", "get", "give", "say", "tell", "see", "find",
    "work", "try", "let", "help", "keep", "start", "run", "set",
    "bug", "fix", "code", "debug", "deploy", "push", "pull", "merge",
    "branch", "commit", "test", "build", "config", "setup", "update",
    "file", "server", "client", "api", "endpoint", "request", "response",
    "database", "query", "cache", "token", "key", "value", "string",
    "array", "object", "function", "class", "method", "module", "package",
    "import", "export", "return", "async", "await", "callback", "promise",
}


def _strip_reply_headers(text: str) -> str:
    """Remove Telegram reply-header lines before metric computation (§31.1 fix)."""
    lines = text.split('\n')
    clean = [l for l in lines if not REPLY_HEADER_RE.match(l.strip())]
    return '\n'.join(clean).strip()


def get_db():
    import sqlite3
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in re.findall(r'\b[a-zA-ZàèéìòùÀÈÉÌÒÙ]+\b', text)]


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r'[.!?]+', text) if s.strip()]


def _bigrams(words: list[str]) -> list[tuple[str, str]]:
    return [(words[i], words[i+1]) for i in range(len(words) - 1)]


def _trigrams(words: list[str]) -> list[tuple[str, str, str]]:
    return [(words[i], words[i+1], words[i+2]) for i in range(len(words) - 2)]


def compute_idiolect(messages: list[str]) -> dict[str, Any] | None:
    if not messages:
        return None

    full_text = " ".join(messages)
    all_words = _tokenize(full_text)
    total_words = len(all_words)

    if total_words < 100:
        return None

    word_freq = Counter(all_words)
    unique_words = len(word_freq)
    ttr = round(unique_words / total_words, 4)
    hapax = sum(1 for c in word_freq.values() if c == 1)
    hapax_pct = round(hapax / unique_words * 100, 2) if unique_words > 0 else 0

    # Sentence stats
    all_sentences = []
    for m in messages:
        all_sentences.extend(_sentences(m))
    sent_lengths = [len(_tokenize(s)) for s in all_sentences if _tokenize(s)]
    avg_sent_len = round(sum(sent_lengths) / len(sent_lengths), 2) if sent_lengths else 0
    sent_std = round(
        math.sqrt(sum((l - avg_sent_len) ** 2 for l in sent_lengths) / max(1, len(sent_lengths))), 2
    ) if sent_lengths else 0

    # Fragment detection (messages < 5 words)
    fragment_count = sum(1 for m in messages if len(_tokenize(m)) < 5)
    fragment_pct = round(fragment_count / len(messages) * 100, 2)

    # Style markers (per 100 chars)
    total_chars = max(1, len(full_text))
    ellipsis_count = full_text.count("...") + full_text.count("\u2026")
    exclamation_count = full_text.count("!")
    question_count = full_text.count("?")
    emoji_count = len(EMOJI_RE.findall(full_text))
    caps_words = sum(1 for w in full_text.split() if w.isupper() and len(w) > 1)

    ellipsis_rate = round(ellipsis_count / total_chars * 100, 4)
    exclamation_rate = round(exclamation_count / total_chars * 100, 4)
    question_rate = round(question_count / total_chars * 100, 4)
    emoji_rate = round(emoji_count / total_chars * 100, 4)
    caps_rate = round(caps_words / max(1, total_words) * 100, 4)

    # Filler phrases
    filler_counts: dict[str, int] = {}
    for match in FILLER_RE.findall(full_text):
        w = match.lower()
        filler_counts[w] = filler_counts.get(w, 0) + 1
    top_fillers = sorted(filler_counts.items(), key=lambda x: -x[1])[:15]

    # Opening patterns (first 1-2 words of each message)
    openers: dict[str, int] = {}
    for m in messages:
        words = _tokenize(m.strip())
        if words:
            opener = words[0] if len(words) == 1 else f"{words[0]} {words[1]}"
            openers[opener] = openers.get(opener, 0) + 1
    top_openers = sorted(openers.items(), key=lambda x: -x[1])[:10]

    # English word percentage
    english_count = sum(1 for w in all_words if w in ENGLISH_WORDS)
    english_pct = round(english_count / total_words * 100, 2)

    # N-grams
    bg = Counter(_bigrams(all_words))
    tg = Counter(_trigrams(all_words))
    top_bg = bg.most_common(30)
    top_tg = tg.most_common(30)

    return {
        "unique_words": unique_words,
        "total_words": total_words,
        "type_token_ratio": ttr,
        "hapax_legomena_pct": hapax_pct,
        "top_words": json.dumps(word_freq.most_common(50)),
        "avg_sentence_length": avg_sent_len,
        "sentence_length_std": sent_std,
        "fragment_pct": fragment_pct,
        "ellipsis_rate": ellipsis_rate,
        "exclamation_rate": exclamation_rate,
        "question_rate": question_rate,
        "emoji_rate": emoji_rate,
        "caps_rate": caps_rate,
        "filler_phrases": json.dumps(top_fillers),
        "opening_patterns": json.dumps(top_openers),
        "closing_patterns": json.dumps([]),
        "english_word_pct": english_pct,
        "top_bigrams": json.dumps([(" ".join(k), v) for k, v in top_bg]),
        "top_trigrams": json.dumps([(" ".join(k), v) for k, v in top_tg]),
    }


def store_idiolect(db, period: str, metrics: dict, sample_size: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO idiolect_profile
            (period, unique_words, total_words, type_token_ratio,
             hapax_legomena_pct, top_words, avg_sentence_length,
             sentence_length_std, fragment_pct,
             ellipsis_rate, exclamation_rate, question_rate,
             emoji_rate, caps_rate,
             filler_phrases, opening_patterns, closing_patterns,
             english_word_pct, top_bigrams, top_trigrams,
             sample_size, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(period) DO UPDATE SET
            unique_words=excluded.unique_words,
            total_words=excluded.total_words,
            type_token_ratio=excluded.type_token_ratio,
            hapax_legomena_pct=excluded.hapax_legomena_pct,
            top_words=excluded.top_words,
            avg_sentence_length=excluded.avg_sentence_length,
            sentence_length_std=excluded.sentence_length_std,
            fragment_pct=excluded.fragment_pct,
            ellipsis_rate=excluded.ellipsis_rate,
            exclamation_rate=excluded.exclamation_rate,
            question_rate=excluded.question_rate,
            emoji_rate=excluded.emoji_rate,
            caps_rate=excluded.caps_rate,
            filler_phrases=excluded.filler_phrases,
            opening_patterns=excluded.opening_patterns,
            closing_patterns=excluded.closing_patterns,
            english_word_pct=excluded.english_word_pct,
            top_bigrams=excluded.top_bigrams,
            top_trigrams=excluded.top_trigrams,
            sample_size=excluded.sample_size,
            computed_at=excluded.computed_at
    """, (
        period,
        metrics["unique_words"], metrics["total_words"], metrics["type_token_ratio"],
        metrics["hapax_legomena_pct"], metrics["top_words"],
        metrics["avg_sentence_length"], metrics["sentence_length_std"],
        metrics["fragment_pct"],
        metrics["ellipsis_rate"], metrics["exclamation_rate"], metrics["question_rate"],
        metrics["emoji_rate"], metrics["caps_rate"],
        metrics["filler_phrases"], metrics["opening_patterns"], metrics["closing_patterns"],
        metrics["english_word_pct"], metrics["top_bigrams"], metrics["top_trigrams"],
        sample_size, now
    ))


def derive_observations(db, period: str, metrics: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    source_ref = f"idiolect:{period}"

    # language.verbal_complexity:
    # High TTR + long sentences + low fragment% = high complexity
    ttr_norm = min(1.0, metrics["type_token_ratio"] / 0.5)
    sent_norm = min(1.0, metrics["avg_sentence_length"] / 20.0)
    frag_inv = 1.0 - min(1.0, metrics["fragment_pct"] / 100.0)
    complexity = ttr_norm * 0.4 + sent_norm * 0.35 + frag_inv * 0.25

    db.execute("""
        INSERT INTO observations
            (facet_id, signal_position, signal_strength, content,
             source_type, source_ref, created_at)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(facet_id, source_ref) DO UPDATE SET
            signal_position=excluded.signal_position,
            signal_strength=excluded.signal_strength,
            content=excluded.content
    """, (
        "language.verbal_complexity", round(complexity, 3), 0.55,
        f"Idiolect {period}: TTR={metrics['type_token_ratio']:.3f}, "
        f"avg_sent={metrics['avg_sentence_length']:.1f}w, "
        f"fragments={metrics['fragment_pct']:.0f}%, "
        f"hapax={metrics['hapax_legomena_pct']:.1f}%",
        "idiolect_analysis", source_ref, now
    ))


def load_messages_by_month(db) -> dict[str, list[str]]:
    rows = db.execute(
        "SELECT content, received_at FROM inbox WHERE from_id=? ORDER BY received_at ASC",
        (SUBJECT_FROM_ID,)
    ).fetchall()
    by_month: dict[str, list[str]] = {}
    for r in rows:
        month = r["received_at"][:7]
        by_month.setdefault(month, []).append(_strip_reply_headers(r["content"]))
    return by_month


def run(period: str | None = None, all_periods: bool = False,
        dry_run: bool = False) -> None:
    db = get_db()
    try:
        by_month = load_messages_by_month(db)
        if not by_month:
            info(SCRIPT, "no_messages")
            return

        if period:
            periods = [period] if period in by_month else []
        elif all_periods:
            periods = sorted(by_month.keys())
        else:
            periods = sorted(by_month.keys())[-3:]

        info(SCRIPT, "run_start", periods=len(periods))

        # Also compute an "all" profile from all messages
        all_msgs = []
        for p in sorted(by_month.keys()):
            all_msgs.extend(by_month[p])

        for p in periods + ["all"]:
            msgs = all_msgs if p == "all" else by_month.get(p, [])
            metrics = compute_idiolect(msgs)
            if not metrics:
                continue

            if dry_run:
                print(f"\n[{p}] {len(msgs)} msgs | TTR={metrics['type_token_ratio']:.3f} | "
                      f"hapax={metrics['hapax_legomena_pct']:.1f}% | "
                      f"avg_sent={metrics['avg_sentence_length']:.1f}w | "
                      f"fragments={metrics['fragment_pct']:.0f}% | "
                      f"english={metrics['english_word_pct']:.1f}% | "
                      f"emoji={metrics['emoji_rate']:.3f}/100ch")
                continue

            store_idiolect(db, p, metrics, len(msgs))
            derive_observations(db, p, metrics)
            info(SCRIPT, "period_computed", period=p, msgs=len(msgs),
                 ttr=metrics["type_token_ratio"],
                 avg_sent=metrics["avg_sentence_length"],
                 english_pct=metrics["english_word_pct"])

        if not dry_run:
            db.commit()
            info(SCRIPT, "run_complete")
    finally:
        db.close()


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Soulkiller Idiolect Model")
    p.add_argument("--period", help="Specific YYYY-MM period")
    p.add_argument("--all", action="store_true", dest="all_periods")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    run(period=args.period, all_periods=args.all_periods, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
