"""Domain-independent value contracts for JSON and optional computations.

Import `JsonValue`, `Option`, `Some`, `Nothing`, and `maybe` from this
package. JSON describes uninterpreted serialized values; Option carries
explicit presence through Python operations.
These contracts perform no I/O, retain no application state, and handle no errors.
The package facade contains only re-exports of these contracts.
"""

from .json import JsonValue as JsonValue
from .option import Nothing as Nothing
from .option import Option as Option
from .option import Some as Some
from .option import maybe as maybe

__all__ = ["JsonValue", "Nothing", "Option", "Some", "maybe"]
