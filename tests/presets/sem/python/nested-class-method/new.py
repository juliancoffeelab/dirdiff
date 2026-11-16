class Options:
    """Store configuration parsing behavior for this example."""

    def parse(self, text):
        """Return configuration text with surrounding whitespace removed."""
        def normalize(value):
            """Strip the supplied configuration value."""
            return value.strip()

        return normalize(text)
