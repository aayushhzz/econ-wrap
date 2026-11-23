"""Project formatter. CI checks that src/ matches its output (see .fmt-stamp)."""
import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def normalize(text):
    out, blank = [], 0
    for line in text.splitlines():
        line = line.rstrip().replace("\t", "    ")
        blank = blank + 1 if not line else 0
        if blank <= 2:
            out.append(line)
    return "\n".join(out).strip("\n") + "\n"


def digest():
    h = hashlib.sha256()
    for p in sorted((ROOT / "src").rglob("*.py")):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    for p in sorted((ROOT / "src").rglob("*.py")):
        p.write_text(normalize(p.read_text()))
    (ROOT / ".fmt-stamp").write_text(digest() + "\n")
    print("formatted src/ and updated .fmt-stamp")
