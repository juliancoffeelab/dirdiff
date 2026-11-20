"""Domain-independent JSON, optional values, and computation outcomes.

Import `JsonValue`, `Option`, `Some`, `Nothing`, `maybe`, `Result`, `Ok`,
`Err`, and `catch` from this package. JSON describes uninterpreted serialized
values; Option carries explicit presence through Python operations.
`Result` carries explicit success or failure, and `catch` adapts synchronous
exception-raising callables. These contracts retain no application state and
perform no I/O themselves.
The package facade contains only re-exports of these contracts.
"""

from .json import JsonValue as JsonValue
from .option import Nothing as Nothing
from .option import Option as Option
from .option import Some as Some
from .option import maybe as maybe
from .result import Err as Err
from .result import Ok as Ok
from .result import Result as Result
from .result import catch as catch

__all__ = [
    "Err",
    "JsonValue",
    "Nothing",
    "Ok",
    "Option",
    "Result",
    "Some",
    "catch",
    "maybe",
]
