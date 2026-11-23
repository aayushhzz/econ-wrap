import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from econ.backend import FakeBackend  # noqa: E402

FIX_TASK1 = [
    ("replace_in_file", {"path": "src/inventory/core.py", "old": 'i["price"] + i["qty"]', "new": 'i["price"] * i["qty"]'}),
    ("run_shell", {"command": "python scripts/gen_fixtures.py"}),
    ("run_shell", {"command": "make fmt"}),
    ("run_shell", {"command": "PYTHONPATH=src python -m pytest -q tests/test_value.py tests/test_style.py"}),
    ("finish", {"summary": "fixed"}),
]

MINER_FACTS = {"facts": [
    {"id": "tests-need-pythonpath", "fact": "Run tests with PYTHONPATH=src python -m pytest.", "confirm": r"PYTHONPATH=src"},
    {"id": "use-make-fmt", "fact": "Format with `make fmt`; black is not installed.", "confirm": r"make fmt"},
    {"id": "hallucinated", "fact": "Run tox.", "confirm": r"\btox\b"},  # never ran: must be rejected
]}


def scripted_agent(script, judge=None, miner=None):
    """Fake backend: the agent replays `script` by step number; small-role calls go to judge/miner."""
    def fn(system, prompt, role):
        if role == "small":
            if "loop" in system:
                return json.dumps(judge or {"stuck": False, "confidence": 0.1, "redirect": ""})
            return json.dumps(miner or {"facts": []})
        step = prompt.count("\nSTEP ") + prompt.startswith("STEP ")
        tool, args = script[min(step, len(script) - 1)]
        return json.dumps({"thought": "scripted", "tool": tool, "args": args})
    return FakeBackend(fn)


@pytest.fixture
def tmp_home(tmp_path):
    return str(tmp_path / "home"), str(tmp_path / "repo")
