# repository manual, learned from prior sessions in this repository
- Tests must be run from the repo root with PYTHONPATH=src set, because the inventory package lives under src/ and is not importable otherwise (e.g. `PYTHONPATH=src python -m pytest -q tests/...`).
- Run `python scripts/gen_fixtures.py` before the test suite to generate tests/fixtures/catalog.json; tests failed with FileNotFoundError until this had run.
- The project formatter is `python scripts/fmt.py`; run it after editing files under src/, since CI's style check fails on unformatted src/.
