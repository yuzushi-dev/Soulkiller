from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_license_exists_and_is_agpl():
    license_file = ROOT / "LICENSE"
    assert license_file.exists()
    text = license_file.read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text


def test_readme_contains_public_quickstart():
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    assert "Quick Start" in text
    assert "soulkiller-demo" in text
    assert "pip install -e ." in text


def test_webui_and_moodboard_assets_exist():
    assert (ROOT / "src" / "soulkiller" / "soulkiller_webui.html").exists()
    assert (ROOT / "docs" / "design" / "2026-04-08-arasaka-ui-moodboard.md").exists()
    assert (ROOT / "docs" / "design" / "arasaka-ui-moodboard.svg").exists()
