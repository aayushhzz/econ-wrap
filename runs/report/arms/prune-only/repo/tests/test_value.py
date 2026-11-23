from inventory.core import total_value


def test_total_value(items):
    assert total_value(items) == 290.8
