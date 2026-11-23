from inventory.core import low_stock


def test_low_stock(items):
    assert low_stock(items) == ["A200", "B200"]
