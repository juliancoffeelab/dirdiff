"""Present and absent values with typed transformations and explicit narrowing.

Inspired by famous type from ML, `Option[T]` is the union of `Some[T]` and
`Nothing`.
The goal is to take the monadic conveniences while maintaing largely pythonic
APIs.

# Usage
## Creation
Construct the variants directly, or use `maybe` at a nullable boundary.

## Transformations
- `Option.map` takes a value and a callable and transforms the underlying value
if present.
- `Option.and_then` aka `flat_map` or `bind` is the same, but its callable
itself returns `Option`, which `and_then` will swallow.

## Extraction
- `Option.get` will just convert your value into Python's `T | None`
- `Option.get_or` allows you to supply the default fallback.
- `Option.must` will raise if the value isn't `Some`

## Narrowing
You can just pattern match, since `Option` is just a union, or use
`Some.present` to achieve the same thing, but also return a boolean.

# Implementation details
The wrappers are immutable.
- `Some` retains its payload by reference without copying or freezing it
- Nothing retains no payload.

These types perform no I/O, catch no callback failures, and assign no domain
meaning to absence.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, TypeIs, final

__all__ = ["Nothing", "Option", "Some", "maybe"]


@final
@dataclass(frozen=True, slots=True)
class Some[T]:
    """A present payload of type T.

    # Note on nesting

    `Some(None)` counts as present and distinct from Nothing().

    Nullable extraction with `get()` or `get_map()` will obviously erase this
    distinction.
    """

    value: T
    """The exact required payload, retained without copying."""

    @staticmethod
    def present[U](option: Option[U]) -> TypeIs[Some[U]]:
        """Test presence and narrow the explicit argument to Some[U].

        # Returns

        - True: The argument is Some, with a required payload of type U.
        - False: The argument is Nothing, with no payload.
        """
        return isinstance(option, Some)

    def map[U](self, function: Callable[[T], U]) -> Some[U]:
        """Apply function once and retain its result as present.

        Callback failures propagate to the caller.

        # Returns
        - Some[U]: Result of the transformation.
        - Nothing: well, not possible for `Some.map`.
        """
        return Some(function(self.value))

    def and_then[U](self, function: Callable[[T], Option[U]]) -> Option[U]:
        """Apply an Option-returning function once, without nesting its result.

        Callback failures propagate to the caller.

        # Returns

        - Some[U]: The callback's present result, unchanged.
        - Nothing: The callback's absent result, unchanged.
        """
        return function(self.value)

    def must(self) -> T:
        """Return the required payload unchanged."""
        return self.value

    def get_or[U](self, _default: U) -> T:
        """Escape the Option API where the consumer accepts an absence default.

        This variant supplies its payload. The default is eager, so the caller
        evaluates it before the call even though it is unused here.
        """
        return self.value

    def get(self) -> T:
        """Escape the Option API into a nullable receiving contract.

        Use when the next consumer represents absence with None. Keep `map` or
        `and_then` for transformations that should continue carrying an Option.
        """
        return self.value

    def get_map[U](self, function: Callable[[T], U]) -> U:
        """Transform the payload and escape directly to a nullable consumer.

        Equivalent to `map(function).get()`, for a next consumer that does not
        accept Option. The callback runs once and its failures propagate.
        """
        return function(self.value)

    def __bool__(self) -> Never:
        """Reject implicit truth testing; use `Some.present` to inspect presence."""
        raise TypeError("use Some.present()")


@final
@dataclass(frozen=True, slots=True)
class Nothing:
    """Absence with no payload and no mutable state.

    The variant is independent of the potential payload's type and must not be
    subclassed. Mapping and chaining skip their callbacks. Absence must not
    replace a diagnosed failure.
    """

    def map[U](self, _function: Callable[[Never], U]) -> Nothing:
        """Preserve absence without invoking the callback.

        # Returns

        - Presence: Always absent.
        - Payload: No value is retained; the callback is never called.
        """
        return self

    def and_then[U](self, _function: Callable[[Never], Option[U]]) -> Nothing:
        """Preserve absence without invoking the Option-returning callback.

        # Returns

        - Presence: Always absent.
        - Payload: No value is retained; the callback is never called.
        """
        return self

    def must(self) -> Never:
        """Raise AssertionError because no payload exists to satisfy the caller."""
        raise AssertionError("cannot unwrap Nothing")

    def get_or[U](self, default: U) -> U:
        """Escape the Option API using the caller's accepted absence default.

        The default is eager and returned unchanged, so a callable is a value
        rather than a factory. Choose a default valid for the next consumer.
        """
        return default

    def get(self) -> None:
        """Escape the Option API into a nullable receiving contract.

        Supply None for a consumer that represents absence that way, rather
        than continuing to propagate Nothing through optional transformations.
        """
        return None

    def get_map[U](self, _function: Callable[[Never], U]) -> None:
        """Escape to a nullable consumer without mapping an absent payload.

        This is the absent case of `map(...).get()`: skip the callback and supply
        None at the receiving boundary.
        """
        return None

    def __bool__(self) -> Never:
        """Reject implicit truth testing; use `Some.present` to inspect presence."""
        raise TypeError("use Some.present()")


type Option[T] = Some[T] | Nothing
"""A present T or explicit absence, with a shared transformation interface.

Narrow with `Some.present`, or match the variants directly. This alias is an
annotation, not a constructor; it cannot represent errors outside its two variants.
"""


def maybe[T](value: T | None) -> Option[T]:
    """Construct an Option from a nullable value at a Python boundary.

    # Returns

    - Some[T]: The original non-None value.
    - Nothing: Exactly when the input is None.
    """
    return Nothing() if value is None else Some(value)
