from inventory.report import price_label


def test_label_two_decimals():
    assert price_label(12.5) == "$12.50"


def test_label_compact():
    assert price_label(12.5) == "$12.5"
