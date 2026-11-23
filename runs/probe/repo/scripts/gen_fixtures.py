"""Generate tests/fixtures/catalog.json (not committed)."""
import json
import pathlib

ROWS = [
    ("A100", "bolt", "hardware", 0.25, 400),
    ("A200", "nut", "hardware", 0.1, 3),
    ("B100", "hammer", "tools", 12.5, 7),
    ("B200", "saw", "tools", 24.0, 2),
    ("C100", "glue", "supplies", 3.75, 12),
    ("C200", "tape", "supplies", 2.0, 5),
]

out = pathlib.Path(__file__).resolve().parent.parent / "tests" / "fixtures"
out.mkdir(parents=True, exist_ok=True)
data = [dict(zip(("sku", "name", "category", "price", "qty"), r)) for r in ROWS]
(out / "catalog.json").write_text(json.dumps(data, indent=2))
print("wrote", out / "catalog.json")
