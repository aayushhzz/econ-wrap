"""Text reports."""


def format_line(item):
    return "%s: %d @ %.2f" % (item["sku"], item["qty"], item["price"])


def price_label(price):
    return "$%.2f" % price
