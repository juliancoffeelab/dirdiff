"""Domain-independent JSON, optional values, computation outcomes, and media.

Import `JsonValue`, `Option`, `Some`, `Nothing`, `maybe`, `Result`, `Ok`,
`Err`, `catch`, and `image_media_type` from this package. JSON describes
uninterpreted serialized values; Option carries explicit presence through
Python operations. `Result` carries explicit success or failure, and `catch`
adapts synchronous exception-raising callables. `image_media_type` names the
image type that bytes hold, or None, and leaves which types are acceptable to
its caller. These contracts retain no application state and perform no I/O
themselves.
The package facade contains only re-exports of these contracts.
"""

from .json import JsonValue as JsonValue
from .media import image_media_type as image_media_type
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
    "image_media_type",
    "maybe",
]
