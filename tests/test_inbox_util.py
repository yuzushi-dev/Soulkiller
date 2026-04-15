"""Tests for lib.inbox — generic inbox.jsonl append utility."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lib.inbox import append_to_inbox


def test_append_creates_file(tmp_path):
    entry = {"from": "assistant", "direction": "sent", "content": "Hello"}
    append_to_inbox(tmp_path, entry)
    inbox = tmp_path / "inbox.jsonl"
    assert inbox.exists()
    lines = inbox.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["content"] == "Hello"


def test_append_appends_multiple(tmp_path):
    append_to_inbox(tmp_path, {"content": "first"})
    append_to_inbox(tmp_path, {"content": "second"})
    lines = (tmp_path / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["content"] == "first"
    assert json.loads(lines[1])["content"] == "second"


def test_append_skips_empty_data_dir():
    # Must not raise when data_dir is empty/None
    append_to_inbox("", {"content": "x"})
    append_to_inbox(None, {"content": "x"})  # type: ignore[arg-type]


def test_append_preserves_unicode(tmp_path):
    entry = {"content": "Ciao! \U0001f49a è una bella giornata"}
    append_to_inbox(tmp_path, entry)
    lines = (tmp_path / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert "\U0001f49a" in json.loads(lines[0])["content"]


def test_append_each_entry_is_valid_json_line(tmp_path):
    for i in range(3):
        append_to_inbox(tmp_path, {"idx": i, "msg": f"message {i}"})
    lines = (tmp_path / "inbox.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert "idx" in obj
