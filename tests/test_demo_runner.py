import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_demo_runner_creates_expected_outputs(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "soulkiller.demo_runner",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert "Demo complete" in result.stdout
    assert (tmp_path / "model_profile.md").exists()
    assert (tmp_path / "model_portrait.md").exists()
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "demo_console.html").exists()

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["message_count"] == 15
    assert summary["subject_name"] == "Demo Subject"
    assert summary["facet_count"] == 24
    assert summary["observation_count_demo_pass"] >= 1
    assert summary["hypothesis_count"] == 3


def test_demo_fixtures_exist():
    assert (ROOT / "demo" / "inbox.sample.jsonl").exists()
    assert (ROOT / "demo" / "profile.seed.json").exists()
    assert (ROOT / "demo" / "config.example.env").exists()
