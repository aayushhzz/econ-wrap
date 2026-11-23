# repository manual, learned from prior sessions in this repository
- Tests in this repository import the `src` package, so run pytest with `PYTHONPATH=src python -m pytest` rather than bare `python -m pytest`, which fails with an ImportError.
