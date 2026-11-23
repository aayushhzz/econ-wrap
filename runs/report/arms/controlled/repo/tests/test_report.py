from inventory.report import format_line


def test_format_line(items):
    assert format_line(items[2]) == "B100: 7 @ 12.50"
