from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOC_DIRS = [
    ROOT / "docs",
    ROOT / "hooks",
]
FORBIDDEN_MARKERS = [
    "/home/cristina",
    "178069551",
    "Daniele",
    "Barbara",
    "Cristina",
]


def iter_doc_files() -> list[Path]:
    files: list[Path] = []
    for base in DOC_DIRS:
        files.extend(base.rglob("*.md"))
    return sorted(files)


def test_docs_do_not_contain_private_subject_markers():
    offenders: list[str] = []
    for path in iter_doc_files():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT)} -> {marker}")

    assert offenders == []
