import json
import pathlib

import pytest

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalog.json"


@pytest.fixture
def items():
    # generated data, not committed
    return json.loads(FIXTURE.read_text())
