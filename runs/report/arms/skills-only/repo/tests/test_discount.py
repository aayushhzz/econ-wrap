from inventory.core import apply_discount


def test_discount():
    assert apply_discount(12.5, 20) == 10.0
    assert apply_discount(24.0, 0) == 24.0
