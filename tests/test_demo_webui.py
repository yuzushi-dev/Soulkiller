import json

from soulkiller.demo_runner import run_demo
from soulkiller.demo_webui import build_demo_console, write_all_demo_variants


def test_demo_webui_builds_demo_console_html(tmp_path):
    run_demo(tmp_path)

    html = build_demo_console(tmp_path)

    assert "Soulkiller" in html
    assert "ENGRAMMATIC TRANSFER SYSTEM" in html
    assert "Demo Subject" in html
    assert "Behavioral Signal Snapshot" in html
    assert "Synthetic Transcript" in html


def test_demo_webui_builds_console_from_demo_artifacts(tmp_path):
    run_demo(tmp_path)

    html = build_demo_console(tmp_path)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert summary["subject_name"] == "Demo Subject"
    assert "# Soulkiller — Personality Model" in html
    assert "# Portrait — Demo Subject" in html
    assert "event_log.sample.jsonl" in html
    assert "delivery_log.sample.jsonl" in html
    assert "Top Confidence Facets" in html
    assert "deliberate decision-making" in html


def test_demo_webui_requires_existing_demo_output(tmp_path):
    missing_dir = tmp_path / "missing"

    try:
        build_demo_console(missing_dir)
    except FileNotFoundError as exc:
        assert "Run the demo runner first" in str(exc)
    else:
        raise AssertionError("Expected FileNotFoundError for missing demo output")


def test_demo_webui_can_generate_all_visual_variants(tmp_path):
    run_demo(tmp_path)

    generated = write_all_demo_variants(tmp_path)

    assert len(generated) == 3
    names = {path.name for path in generated}
    assert "demo_console.executive.html" in names
    assert "demo_console.blacksite.html" in names
    assert "demo_console.directive.html" in names
