from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Never, TypeIs, final

__all__ = ["Nothing", "Option", "Some", "maybe"]


@final
@dataclass(frozen=True, slots=True)
class Some[T]:
    value: T

    @staticmethod
    def when[U](condition: bool, value: U) -> Option[U]:
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
        return self.value

    def get_or[U](self, _default: U) -> T:
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
        raise TypeError("use Some.present()")


@final
@dataclass(frozen=True, slots=True)
class Nothing:
    def map[U](self, _function: Callable[[Never], U]) -> Nothing:
        return self

    def map_bind[U, *Args](
        self,
        _arguments: tuple[*Args],
        _function: Callable[[Never, *Args], U],
    ) -> Nothing:
        return self

    def and_then[U](self, _function: Callable[[Never], Option[U]]) -> Nothing:
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
        return default

    def map_or[U, V](self, default: V, _function: Callable[[Never], U]) -> V:
        return default

    def get(self) -> None:
        return None

    def get_map[U](self, _function: Callable[[Never], U]) -> None:
        return None

    def __bool__(self) -> Never:
        raise TypeError("use Some.present()")


type Option[T] = Some[T] | Nothing


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
