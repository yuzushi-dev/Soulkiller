from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src" / "soulkiller"
FORBIDDEN_MARKERS = [
    "/home/cristina",
    "178069551",
    "biofeedback_creds.json",
]


def iter_python_files() -> list[Path]:
    return sorted(ROOT.rglob("*.py"))


def test_python_sources_do_not_contain_forbidden_private_markers():
    offenders: list[str] = []
    for path in iter_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT.parent)} -> {marker}")

    assert offenders == []
