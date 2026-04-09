from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src" / "soulkiller"
FORBIDDEN_MARKERS = [
    "Daniele",
    "Barbara",
    "Cristina",
    "daniele.veri.pe@gmail.com",
    "Daniele@8",
]


def iter_python_files() -> list[Path]:
    return sorted(ROOT.rglob("*.py"))


def test_python_sources_do_not_contain_personal_names_or_inline_secrets():
    offenders: list[str] = []
    for path in iter_python_files():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT.parent)} -> {marker}")

    assert offenders == []
