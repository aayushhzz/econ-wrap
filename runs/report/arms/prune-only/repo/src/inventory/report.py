"""Text reports."""


def format_line(item):
    return "%s: %d @ %.2f" % (item["sku"], item["qty"], item["price"])
