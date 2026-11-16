def audit(value):
    """Return an audit message containing the supplied value."""
    return f"audit: {value}"


def submit(value):
    """Return the result of passing the value to audit."""
    return audit(value)
