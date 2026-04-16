"""Tests for soulkiller_reply_extractor - recent-exchanges context block."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from soulkiller.soulkiller_reply_extractor import _build_context_block, build_prompt


def _make_exchange(eid: int = 1, facet: str = "emotional.stress_response",
                   question: str = "Hai avuto stress oggi?",
                   reply: str = "Un po'.",
                   spectrum_low: str = "calm", spectrum_high: str = "reactive") -> dict:
    return {
        "id": eid,
        "facet_id": facet,
        "question_text": question,
        "reply_text": reply,
        "spectrum_low": spectrum_low,
        "spectrum_high": spectrum_high,
    }


# --- _build_context_block ---

def test_build_context_block_empty_returns_empty_string():
    assert _build_context_block([]) == ""


def test_build_context_block_includes_question_and_reply():
    recent = [{"question_text": "Come stai?", "reply_text": "Bene."}]
    block = _build_context_block(recent)
    assert "Come stai?" in block
    assert "Bene." in block


def test_build_context_block_multiple_exchanges():
    recent = [
        {"question_text": "Q1", "reply_text": "A1"},
        {"question_text": "Q2", "reply_text": "A2"},
    ]
    block = _build_context_block(recent)
    assert "Q1" in block
    assert "A1" in block
    assert "Q2" in block
    assert "A2" in block


def test_build_context_block_truncates_long_text():
    long_q = "Q" * 200
    long_a = "A" * 200
    block = _build_context_block([{"question_text": long_q, "reply_text": long_a}])
    # Should not include all 200 chars (truncated at 120)
    assert len(block) < 200 + 200 + 100  # generous bound


def test_build_context_block_handles_missing_reply():
    recent = [{"question_text": "Come stai?", "reply_text": None}]
    block = _build_context_block(recent)
    assert "Come stai?" in block  # should not raise


# --- build_prompt with recent context ---

def test_build_prompt_without_context_contains_exchange():
    exchanges = [_make_exchange()]
    prompt = build_prompt(exchanges)
    assert "Hai avuto stress oggi?" in prompt
    assert "Un po'." in prompt


def test_build_prompt_with_context_includes_recent_exchanges():
    exchanges = [_make_exchange()]
    recent = [{"question_text": "Come dormi?", "reply_text": "Male ultimamente."}]
    prompt = build_prompt(exchanges, recent=recent)
    assert "Come dormi?" in prompt
    assert "Male ultimamente." in prompt


def test_build_prompt_context_appears_before_exchanges():
    exchanges = [_make_exchange(question="Domanda corrente", reply="Risposta corrente")]
    recent = [{"question_text": "Domanda precedente", "reply_text": "Risposta precedente"}]
    prompt = build_prompt(exchanges, recent=recent)
    idx_context = prompt.index("Domanda precedente")
    idx_current = prompt.index("Domanda corrente")
    assert idx_context < idx_current


def test_build_prompt_no_context_by_default():
    """Without recent arg, build_prompt must not raise and must work as before."""
    exchanges = [_make_exchange()]
    prompt = build_prompt(exchanges)
    assert "exchange_id: 1" in prompt
    assert "emotional.stress_response" in prompt
