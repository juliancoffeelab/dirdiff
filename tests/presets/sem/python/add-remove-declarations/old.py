LIMIT = 3


def legacy_label(name):
    """Format the legacy label for a customer name."""
    return "Customer: " + name


def keep(value):
    """Return the caller's value unchanged."""
    return value
