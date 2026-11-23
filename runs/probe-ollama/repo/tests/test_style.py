import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sources_formatted():
    h = hashlib.sha256()
    for p in sorted((ROOT / "src").rglob("*.py")):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    stamp = ROOT / ".fmt-stamp"
    ok = stamp.exists() and stamp.read_text().strip() == h.hexdigest()
    assert ok, "style check failed: src/ is not formatted with the project formatter"
