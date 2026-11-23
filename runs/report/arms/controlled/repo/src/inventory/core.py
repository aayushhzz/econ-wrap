"""Inventory helpers."""


def total_value(items):
    return round(sum(i["price"] * i["qty"] for i in items), 2)


def low_stock(items, threshold=5):
    return [i["sku"] for i in items if i["qty"] < threshold]


def find_item(items, sku):
    for i in items:
        if i["sku"] == sku:
            return i
    return None


def restock(items, sku, amount):
    if amount <= 0:
        raise ValueError("amount must be positive")
    item = find_item(items, sku)
    if item is None:
        raise KeyError(sku)
    item["qty"] += amount
    return item["qty"]


def categories(items):
    return sorted({i["category"] for i in items})


def apply_discount(price, percent):
    return round(price * (1 - percent / 100), 2)
