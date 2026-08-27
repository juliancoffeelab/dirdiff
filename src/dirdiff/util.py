"""Domain-independent foundational types shared across dirdiff.

This module is the Python prelude for types that describe general programming
values rather than dirdiff entities. It owns no application data or resources,
performs no I/O, and must not collect feature helpers merely because several
callers could share them.
"""

from __future__ import annotations

__all__ = ["JsonValue"]

type JsonValue = (
    str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]
)
"""A value representable by the standard JSON data model.

Object keys are strings, arrays and objects recursively contain JSON values,
and no arbitrary Python instance belongs to this type.
"""
