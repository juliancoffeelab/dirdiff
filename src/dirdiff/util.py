"""Domain-independent value types shared across dirdiff.

## Public interface

`JsonValue` describes parsed JSON whose feature-specific meaning is unknown or
intentionally left uninterpreted.

## Purpose and boundaries

This module gives unrelated packages one definition of JSON-compatible values.
It stores no application data, performs no I/O, and must not collect feature
helpers merely because several callers could share them.
"""

from __future__ import annotations

__all__ = ["JsonValue"]

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
"""A value representable by the standard JSON data model.

Use this alias where code retains arbitrary parsed JSON without interpreting a
feature-specific schema, such as rejected notebook content.

- Scalars are strings, numbers, booleans, and null.
- Arrays recursively contain `JsonValue` elements.
- Objects have string keys and recursively contain `JsonValue` values.

The type excludes arbitrary Python instances, bytes, datetimes, and
NaN-specific semantics.
"""
