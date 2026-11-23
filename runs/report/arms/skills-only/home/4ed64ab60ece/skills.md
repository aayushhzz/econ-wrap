# repository manual, learned from prior sessions in this repository
- Tests in this repository must be run with PYTHONPATH=src set, for example `PYTHONPATH=src python -m pytest -q`, because running pytest without it fails with ImportError.
- Run `python scripts/gen_fixtures.py` before the test suite, since tests such as tests/test_style.py fail with FileNotFoundError until the fixtures are generated.
