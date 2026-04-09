from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "hooks"
FORBIDDEN_MARKERS = [
    "/home/cristina",
    "178069551",
]


def iter_hook_sources() -> list[Path]:
    return sorted(ROOT.rglob("*.ts"))


def test_hook_sources_do_not_contain_local_paths_or_real_ids():
    offenders: list[str] = []
    for path in iter_hook_sources():
        text = path.read_text(encoding="utf-8")
        for marker in FORBIDDEN_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(ROOT.parent)} -> {marker}")

    assert offenders == []
