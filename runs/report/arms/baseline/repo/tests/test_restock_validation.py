from inventory.core import restock

import pytest


def test_rejects_non_positive(items):
    with pytest.raises(ValueError):
        restock(items, "A100", 0)
    assert restock(items, "A100", 5) == 405
