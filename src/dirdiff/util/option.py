"""Present and absent values with typed transformations and explicit narrowing.

Inspired by famous type from ML, `Option[T]` is the union of `Some[T]` and
`Nothing`.
The goal is to take the monadic conveniences while maintaing largely pythonic
APIs.

# Usage
## Creation
- Construct the variants directly
- Use `maybe` at a nullable boundary.
- `Some.when` accepts a condition and an eager value
- `Some.then` calls a factory only when its condition is true.
- `Some.then_bind` supplies an explicit tuple of arguments to that conditional
callback.

## Transformations
- `Option.map` takes a value and a callable and transforms the underlying value
if present.
- `Option.map_bind` additionally supplies an explicit tuple of callback
arguments after the payload, without capturing those values in a closure.
- `Option.and_then` aka `flat_map` or monadic `bind` is the same, but its
callable itself returns `Option`, which `and_then` will swallow.

## Extraction
- `Option.get` will just convert your value into Python's `T | None`
- `Option.get_or` allows you to supply the default fallback.
- `Option.map_or` transforms a present payload and extracts it, or returns an
eager default when absent.
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
    def when[U](condition: bool, value: U) -> Option[U]:
        """Retain an eager value when the condition holds.

        The caller evaluates `value` before this call, even when the condition
        is false. Use `then` when producing the value must be conditional.

        # Parameters

        - `condition`: Whether to retain the supplied value.
        - `value`: Already evaluated payload; callables are retained as values.

        # Returns

        - `Some[U]`: The supplied value when the condition is true.
        - `Nothing`: Absence when the condition is false.
        """
        return Some(value) if condition else Nothing()

    @staticmethod
    def then[U](condition: bool, factory: Callable[[], U]) -> Option[U]:
        """Produce a value only when the condition holds.

        Invoke `factory` once when true and skip it when false. Factory failures
        propagate. Use `when` for a value already produced by the caller.

        # Parameters

        - `condition`: Whether to invoke the factory and retain its result.
        - `factory`: Produces the payload without arguments when invoked.

        # Usage

        ```python
        >>> names = ["README.md"]
        >>> Some.then(len(names) > 0, lambda: names[0])
        Some(value='README.md')
        >>> Some.then(False, lambda: names[1])
        Nothing()

        ```

        # Returns

        - `Some[U]`: The factory's result when the condition is true.
        - `Nothing`: Absence when the condition is false.
        """
        return Some(factory()) if condition else Nothing()

    @staticmethod
    def then_bind[U, *Args](
        condition: bool,
        arguments: tuple[*Args],
        function: Callable[[*Args], U],
    ) -> Option[U]:
        """Produce a value conditionally using explicitly supplied arguments.

        Strictly speaking, it's largely the same as the usual `bind`, but as
        lambdas, in theory, can capture their arguments in a weird way with
        loops, that helps to make the code more explicit and linters like it.

        When condition is true, calls `function(*arguments`), otherwise
        skips it.

        # Parameters

        - `condition`: Whether to invoke the callback and retain its result.
        - `arguments`: Positional callback inputs evaluated by the caller.
        - `function`: Produces the payload from the supplied inputs.

        # Usage

        Deferred lambdas capture the loop variable, so they all see its last
        value:

        ```python
        >>> callbacks = []
        >>> for name in ("notes", "todo"):
        ...     callbacks.append(lambda: name + ".md")
        >>> [callback() for callback in callbacks]
        ['todo.md', 'todo.md']

        ```

        Supply each iteration's value explicitly instead of capturing the loop
        variable. Both `then` and `then_bind` call immediately, so a capture would
        also be safe here; the tuple removes what loop-variable lints warn about.

        ```python
        >>> paths = []
        >>> for name in ("notes", "todo"):
        ...     paths.append(Some[str].then_bind(
        ...         True, (name,), lambda current: current + ".md"
        ...     ))
        >>> paths
        [Some(value='notes.md'), Some(value='todo.md')]

        ```

        # Returns

        - `Some[U]`: The callback's result when the condition is true.
        - `Nothing`: Absence when the condition is false.
        """
        return Some(function(*arguments)) if condition else Nothing()

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

        # Usage

        ```python
        >>> Some("notes").map(str.upper)
        Some(value='NOTES')
        >>> Nothing().map(str.upper)
        Nothing()

        ```

        # Returns
        - Some[U]: Result of the transformation.
        - Nothing: well, not possible for `Some.map`.
        """
        return Some(function(self.value))

    def map_bind[U, *Args](
        self,
        arguments: tuple[*Args],
        function: Callable[[T, *Args], U],
    ) -> Some[U]:
        """Transform the payload with explicitly supplied additional inputs.

        See `Some.then_bind` for explanation, usually you don't need that
        method and should prefer `map`.

        # Parameters

        - `arguments`: Positional callback inputs following the payload.
        - `function`: Transforms the payload together with the supplied inputs.

        # Usage

        Pass the changing suffix explicitly, so the callback does not capture
        a loop variable. See `Some.then_bind` for the deferred-callback pitfall.

        ```python
        >>> paths = []
        >>> for suffix in (".md", ".txt"):
        ...     paths.append(Some("notes").map_bind(
        ...         (suffix,), lambda name, extension: name + extension
        ...     ))
        >>> paths
        [Some(value='notes.md'), Some(value='notes.txt')]

        ```

        # Returns

        - `Some[U]`: Result of the transformation.
        - `Nothing`: Not possible for `Some.map_bind`.
        """
        return Some(function(self.value, *arguments))

    def and_then[U](self, function: Callable[[T], Option[U]]) -> Option[U]:
        """Apply an Option-returning function once, without nesting its result.

        Callback failures propagate to the caller.

        # Usage

        ```python
        >>> def first(items: list[str]) -> Option[str]:
        ...     'Return the first item, or Nothing for an empty list.'
        ...     return Some(items[0]) if len(items) > 0 else Nothing()
        >>> Some(["notes", "todo"]).and_then(first)
        Some(value='notes')
        >>> Some([]).and_then(first)
        Nothing()
        >>> Nothing().and_then(first)
        Nothing()

        ```

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

    def map_or[U, V](self, _default: V, function: Callable[[T], U]) -> U:
        """Transform the payload and escape to a default-accepting consumer.

        Shorthand to `map(function).get_or(default)` without a temporary
        wrapper.
        Invoke the function once and propagate its failures. The caller
        evaluates the eager default even though this variant does not use it.

        # Usage

        ```python
        >>> Some("notes").map_or("untitled", str.upper)
        'NOTES'
        >>> Nothing().map_or("untitled", str.upper)
        'untitled'

        ```
        """
        return function(self.value)

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

        # Usage

        ```python
        >>> Some("notes").get_map(str.upper)
        'NOTES'
        >>> Nothing().get_map(str.upper) is None
        True

        ```
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

        - `Nothing`: This absent value, unchanged.
        - `Some[U]`: Not possible for `Nothing.map`.
        """
        return self

    def map_bind[U, *Args](
        self,
        _arguments: tuple[*Args],
        _function: Callable[[Never, *Args], U],
    ) -> Nothing:
        """Preserve absence without invoking a callback with additional inputs.

        This is the absent case of `map_bind`. Supplied arguments have already
        been evaluated by the caller; this variant retains none of them.

        # Returns

        - `Nothing`: This absent value, unchanged.
        - `Some[U]`: Not possible for `Nothing.map_bind`.
        """
        return self

    def and_then[U](self, _function: Callable[[Never], Option[U]]) -> Nothing:
        """Preserve absence without invoking the Option-returning callback.

        # Returns

        - `Nothing`: This absent value, unchanged.
        - `Some[U]`: Not possible for `Nothing.and_then`.
        """
        return self

    def must(self) -> Never:
        """Raise AssertionError because no payload exists to satisfy the caller.

        # Usage

        ```python
        >>> Nothing().must()
        Traceback (most recent call last):
            ...
        AssertionError: cannot unwrap Nothing

        ```
        """
        raise AssertionError("cannot unwrap Nothing")

    def get_or[U](self, default: U) -> U:
        """Escape the Option API using the caller's accepted absence default.

        The default is eager and returned unchanged, so a callable is a value
        rather than a factory. Choose a default valid for the next consumer.
        """
        return default

    def map_or[U, V](self, default: V, _function: Callable[[Never], U]) -> V:
        """Escape to a default-accepting consumer without mapping absence.

        Return the eager default unchanged and skip the function. This is the
        absent case of `map(function).get_or(default)`.
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

    # Usage

    ```python
    >>> maybe("notes")
    Some(value='notes')
    >>> maybe(None)
    Nothing()

    ```

    # Returns

    - Some[T]: The original non-None value.
    - Nothing: Exactly when the input is None.
    """
    return Nothing() if value is None else Some(value)
