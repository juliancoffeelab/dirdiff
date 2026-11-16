def audit(value):
    """Return an audit message containing the supplied value."""
    return f"audit: {value}"


def submit(value):
    """Audit and return a submitted value."""
    audit(value)
    return value
