from inventory.core import categories


def test_categories(items):
    assert categories(items) == ["hardware", "supplies", "tools"]
