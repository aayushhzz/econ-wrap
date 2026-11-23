"""A seeded toy repository with three operational quirks, plus 12 scripted tasks.

Quirks the agent has to rediscover every session unless the controller teaches it:
  1. src layout with no packaging: tests only import with PYTHONPATH=src.
  2. tests/fixtures/ is generated, not committed: run `python scripts/gen_fixtures.py` (or `make fixtures`).
  3. CI formatting check: black is not installed; `make fmt` is the formatter and stamps .fmt-stamp.
"""
import os
import shutil
import subprocess
import sys

from .tools import shell_env

CORE = '''"""Inventory helpers."""


def total_value(items):
    return round(sum(i["price"] * i["qty"] for i in items), 2)


def low_stock(items, threshold=5):
    return [i["sku"] for i in items if i["qty"] < threshold]


def find_item(items, sku):
    for i in items:
        if i["sku"] == sku:
            return i
    return None


def restock(items, sku, amount):
    item = find_item(items, sku)
    if item is None:
        raise KeyError(sku)
    item["qty"] += amount
    return item["qty"]


def categories(items):
    return sorted({i["category"] for i in items})


def apply_discount(price, percent):
    return round(price * (1 - percent / 100), 2)
'''

REPORT = '''"""Text reports."""


def format_line(item):
    return "%s: %d @ %.2f" % (item["sku"], item["qty"], item["price"])
'''

CONFTEST = '''import json
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalog.json"


@pytest.fixture
def items():
    # generated data, not committed
    return json.loads(FIXTURE.read_text())
'''

GEN_FIXTURES = '''"""Generate tests/fixtures/catalog.json (not committed)."""
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
'''

FMT = '''"""Project formatter. CI checks that src/ matches its output (see .fmt-stamp)."""
import hashlib
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def normalize(text):
    out, blank = [], 0
    for line in text.splitlines():
        line = line.rstrip().replace("\\t", "    ")
        blank = blank + 1 if not line else 0
        if blank <= 2:
            out.append(line)
    return "\\n".join(out).strip("\\n") + "\\n"


def digest():
    h = hashlib.sha256()
    for p in sorted((ROOT / "src").rglob("*.py")):
        h.update(str(p.relative_to(ROOT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    for p in sorted((ROOT / "src").rglob("*.py")):
        p.write_text(normalize(p.read_text()))
    (ROOT / ".fmt-stamp").write_text(digest() + "\\n")
    print("formatted src/ and updated .fmt-stamp")
'''

TEST_STYLE = '''import hashlib
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
'''

MAKEFILE = """.PHONY: fmt fixtures
fmt:
\tpython scripts/fmt.py
fixtures:
\tpython scripts/gen_fixtures.py
"""


def _t(imports, body):
    return "from %s\n\n\n%s" % (imports, body)


BASE_FILES = {
    "README.md": "# inventory\n\nSmall inventory helpers. Code is in src/inventory, tests in tests/.\n"
                 "CI runs the test suite and a formatting check.\n",
    "Makefile": MAKEFILE,
    "src/inventory/__init__.py": "",
    "src/inventory/core.py": CORE,
    "src/inventory/report.py": REPORT,
    "scripts/gen_fixtures.py": GEN_FIXTURES,
    "scripts/fmt.py": FMT,
    "tests/conftest.py": CONFTEST,
    "tests/test_style.py": TEST_STYLE,
    "tests/test_value.py": _t("inventory.core import total_value",
                              "def test_total_value(items):\n    assert total_value(items) == 290.8\n"),
    "tests/test_low_stock.py": _t("inventory.core import low_stock",
                                  "def test_low_stock(items):\n    assert low_stock(items) == [\"A200\", \"B200\"]\n"),
    "tests/test_discount.py": _t("inventory.core import apply_discount",
                                 "def test_discount():\n    assert apply_discount(12.5, 20) == 10.0\n"
                                 "    assert apply_discount(24.0, 0) == 24.0\n"),
    "tests/test_report.py": _t("inventory.report import format_line",
                               "def test_format_line(items):\n    assert format_line(items[2]) == \"B100: 7 @ 12.50\"\n"),
    "tests/test_categories.py": _t("inventory.core import categories",
                                   "def test_categories(items):\n"
                                   "    assert categories(items) == [\"hardware\", \"supplies\", \"tools\"]\n"),
}

# id, prompt, {path: (old, new)} bug injections, {path: content} new test files, test file to check
TASKS = [
    (1, "total_value() returns the wrong number. Fix it so tests/test_value.py passes.",
     {"src/inventory/core.py": ('i["price"] * i["qty"]', 'i["price"] + i["qty"]')}, {}, "tests/test_value.py"),
    (2, "low_stock() includes items that are exactly at the threshold. Fix it so tests/test_low_stock.py passes.",
     {"src/inventory/core.py": ('i["qty"] < threshold', 'i["qty"] <= threshold')}, {}, "tests/test_low_stock.py"),
    (3, "Add remove_item(items, sku) to src/inventory/core.py: return a new list without that sku and raise "
        "KeyError if it is missing. tests/test_remove.py specifies it.", {},
     {"tests/test_remove.py": _t("inventory.core import remove_item\n\nimport pytest",
                                 "def test_remove(items):\n    rest = remove_item(items, \"A100\")\n"
                                 "    assert [i[\"sku\"] for i in rest] == [\"A200\", \"B100\", \"B200\", \"C100\", \"C200\"]\n"
                                 "    assert len(items) == 6\n    with pytest.raises(KeyError):\n"
                                 "        remove_item(items, \"Z999\")\n")}, "tests/test_remove.py"),
    (4, "apply_discount() gives wrong prices. Fix it so tests/test_discount.py passes.",
     {"src/inventory/core.py": ("percent / 100", "percent / 10")}, {}, "tests/test_discount.py"),
    (5, "Add items_in_category(items, category) to src/inventory/core.py returning the sorted skus in that "
        "category. tests/test_category_filter.py specifies it.", {},
     {"tests/test_category_filter.py": _t("inventory.core import items_in_category",
                                          "def test_filter(items):\n"
                                          "    assert items_in_category(items, \"tools\") == [\"B100\", \"B200\"]\n"
                                          "    assert items_in_category(items, \"none\") == []\n")},
     "tests/test_category_filter.py"),
    (6, "restock() must reject non-positive amounts with ValueError. tests/test_restock_validation.py specifies it.",
     {}, {"tests/test_restock_validation.py": _t("inventory.core import restock\n\nimport pytest",
                                                 "def test_rejects_non_positive(items):\n"
                                                 "    with pytest.raises(ValueError):\n"
                                                 "        restock(items, \"A100\", 0)\n"
                                                 "    assert restock(items, \"A100\", 5) == 405\n")},
     "tests/test_restock_validation.py"),
    (7, "format_line() prints prices with the wrong precision. Fix it so tests/test_report.py passes.",
     {"src/inventory/report.py": ("%.2f", "%.1f")}, {}, "tests/test_report.py"),
    (8, "Add most_valuable(items) to src/inventory/core.py returning the sku with the largest price*qty. "
        "tests/test_most_valuable.py specifies it.", {},
     {"tests/test_most_valuable.py": _t("inventory.core import most_valuable",
                                        "def test_most_valuable(items):\n"
                                        "    assert most_valuable(items) == \"A100\"\n")},
     "tests/test_most_valuable.py"),
    (9, "categories() returns duplicates in arbitrary order. Fix it so tests/test_categories.py passes.",
     {"src/inventory/core.py": ('sorted({i["category"] for i in items})', '[i["category"] for i in items]')}, {},
     "tests/test_categories.py"),
    (10, "Add summary(items) to src/inventory/report.py returning {\"skus\": count, \"units\": total qty, "
         "\"value\": total value}. tests/test_summary.py specifies it.", {},
     {"tests/test_summary.py": _t("inventory.report import summary",
                                  "def test_summary(items):\n"
                                  "    assert summary(items) == {\"skus\": 6, \"units\": 429, \"value\": 290.8}\n")},
     "tests/test_summary.py"),
    (11, "find_item() should match skus case-insensitively. tests/test_find_case.py specifies it.", {},
     {"tests/test_find_case.py": _t("inventory.core import find_item",
                                    "def test_case_insensitive(items):\n"
                                    "    assert find_item(items, \"b100\")[\"name\"] == \"hammer\"\n"
                                    "    assert find_item(items, \"nope\") is None\n")},
     "tests/test_find_case.py"),
    (12, "Add bulk_restock(items, amounts) to src/inventory/core.py: apply restock for each sku->amount and "
         "return a dict of new quantities. tests/test_bulk.py specifies it.", {},
     {"tests/test_bulk.py": _t("inventory.core import bulk_restock",
                               "def test_bulk(items):\n"
                               "    assert bulk_restock(items, {\"A200\": 7, \"B200\": 1}) == {\"A200\": 10, \"B200\": 3}\n")},
     "tests/test_bulk.py"),
    # Loop probe, not part of the 12-task stream: the two tests contradict each other, so no honest fix
    # exists. It exists only to exercise the loop guard live (`econ run --tasks 13 --arm guard-only`).
    (13, "Add price_label(price) to src/inventory/report.py so that tests/test_label.py passes.", {},
     {"tests/test_label.py": _t("inventory.report import price_label",
                                "def test_label_two_decimals():\n    assert price_label(12.5) == \"$12.50\"\n\n\n"
                                "def test_label_compact():\n    assert price_label(12.5) == \"$12.5\"\n")},
     "tests/test_label.py"),
]

TASK_BY_ID = {t[0]: t for t in TASKS}


def task_prompt(task_id):
    t = TASK_BY_ID[task_id]
    return t[1] + " CI also requires tests/test_style.py to pass. Do not edit the tests."


def _py(workdir, *args):
    env = shell_env()
    return subprocess.run([sys.executable] + list(args), cwd=workdir, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def seed(workdir, task_id):
    """Fresh copy of the repo in the state task `task_id` starts from."""
    _, _, bugs, new_tests, _ = TASK_BY_ID[task_id]
    if os.path.exists(workdir):
        shutil.rmtree(workdir)
    files = dict(BASE_FILES)
    for path, (old, new) in bugs.items():
        assert old in files[path], "bug injection does not apply: %s" % path
        files[path] = files[path].replace(old, new, 1)
    files.update(new_tests)
    for rel, content in files.items():
        full = os.path.join(workdir, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
    _py(workdir, "scripts/fmt.py")  # stamp the starting state as formatted
    return workdir


def check(workdir, task_id):
    """The task's pytest check (plus the style check). The harness knows the quirks; the agent does not."""
    test = TASK_BY_ID[task_id][4]
    _py(workdir, "scripts/gen_fixtures.py")
    env = shell_env()
    env["PYTHONPATH"] = "src"
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test,
                           "tests/test_style.py"], cwd=workdir, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return proc.returncode == 0, proc.stdout.decode("utf-8", "replace")[-600:]
