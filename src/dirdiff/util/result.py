"""Successful and failed values with typed transformations and narrowing.

Inspired by the type from ML, `Result[T, E]` is the union of `Ok[T]` and
`Err[E]`.
The goal is to take the monadic conveniences while maintaining largely Pythonic
APIs.

# Usage
## Creation
You can construct the variants directly.

Or, what you'll probably do more is using a `@catch` decorator.
It accepts one through ten exception classes, like in this example.

>>> @catch(ValueError, OverflowError)
... def number(text: str) -> int:
...     'Parse an integer or return its conversion error.'
...     return int(text)
>>> number("12")
Ok(value=12)
>>> result = number("bad")
>>> Ok.good(result)
False
>>> isinstance(result.error, ValueError)
True

## Transformations
- `Result.map` takes a value and a callable and transforms the underlying value
if successful.
- `Result.map_bind` additionally supplies an explicit tuple of callback
arguments after the payload, instead of capturing those values in a closure.
- `Result.map_err` is the same as `map`, but transforms the error instead.
- `Result.and_then` aka `flat_map` or monadic `bind` is the same, but its
callable itself returns `Result`, which `and_then` will swallow. The resulting
error type includes both the original and callback error types.

## Extraction
- `Result.or_raise` returns the successful value or raises the error.
- To use `or_raise`, the error must be an exception. Use `map_err` to convert
other error values into exceptions first.

>>> Err("unreadable").map_err(ValueError).or_raise()
Traceback (most recent call last):
    ...
ValueError: unreadable

## Narrowing
You can just pattern match, since `Result` is just a union, or use
`Ok.good` to achieve the same thing, but also return a boolean.

# Implementation details
Both variants retain their payloads by reference without copying or freezing
them.
- `Ok` is a frozen dataclass.
- `Err.error` is Final, so type checkers reject reassignment. Deletion is
rejected at runtime.
- Caught exceptions retain their traceback, including references to local
  values.

These types perform no I/O and assign no domain meaning to errors.
Transformations catch no callback failures; only `catch` converts the explicitly
selected exceptions into `Err`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from inspect import isasyncgenfunction, iscoroutinefunction, isgeneratorfunction
from typing import Final, Never, TypeIs, final, overload, override

__all__ = ["Err", "Ok", "Result", "catch"]


@final
@dataclass(frozen=True, slots=True)
class Ok[T]:
    """A successful payload of type T.

    # Note on nesting

    `Ok(ValueError)` counts as successful, as does `Ok(Err("error"))`.

    Even `Ok.or_raise` on it would return an error as is, without throwing an
    exception.
    """

    value: T
    """The exact required payload, retained without copying."""

    @staticmethod
    def good[U, E](result: Result[U, E]) -> TypeIs[Ok[U]]:
        """Test success and narrow the explicit argument to Ok[U].

        # Returns

        - True: The argument is Ok, with a required payload of type U.
        - False: The argument is Err, with a required error of type E.
        """
        return isinstance(result, Ok)

    def map[U](self, function: Callable[[T], U]) -> Ok[U]:
        """Apply function once and retain its result as successful.

        Callback failures propagate to the caller.

        # Usage

        >>> Ok("notes").map(str.upper)
        Ok(value='NOTES')
        >>> Err("unreadable").map(str.upper)
        Err(error='unreadable')

        # Returns

        - `Ok[U]`: Result of the transformation.
        - `Err`: Not possible for `Ok.map`.
        """
        return Ok(function(self.value))

    def map_bind[U, *Args](
        self, arguments: tuple[*Args], function: Callable[[T, *Args], U]
    ) -> Ok[U]:
        """Transform the payload with explicitly supplied additional inputs.

        See `dirdiff.util.Some.then_bind` for explanation; usually you don't
        need this method and should prefer `map`.

        # Parameters

        - `arguments`: Positional callback inputs following the payload.
        - `function`: Transforms the payload together with the supplied inputs.

        # Usage

        Pass the changing suffix explicitly, so the callback does not capture a
        loop variable. Both `map` and `map_bind` call immediately; but this form
        is more lint-friendly.

        >>> paths = []
        >>> for suffix in (".md", ".txt"):
        ...     paths.append(Ok("notes").map_bind(
        ...         (suffix,), lambda name, extension: name + extension
        ...     ))
        >>> paths
        [Ok(value='notes.md'), Ok(value='notes.txt')]

        # Returns

        - `Ok[U]`: Result of the transformation.
        - `Err`: Not possible for `Ok.map_bind`.
        """
        return Ok(function(self.value, *arguments))

    def map_err[F](self, _function: Callable[[Never], F]) -> Ok[T]:
        """Preserve success without invoking the error callback.

        # Returns

        - `Ok[T]`: This successful value, unchanged.
        - `Err[F]`: Not possible for `Ok.map_err`.
        """
        return self

    def and_then[U, F](
        self, function: Callable[[T], Result[U, F]]
    ) -> Result[U, F]:
        """Apply a Result-returning function once, without nesting its result.

        Callback failures propagate to the caller.

        # Usage

        >>> def first(items: list[str]) -> Result[str, str]:
        ...     'Return the first item, or an error for an empty list.'
        ...     return Ok(items[0]) if len(items) > 0 else Err("empty list")
        >>> Ok(["notes", "todo"]).and_then(first)
        Ok(value='notes')
        >>> Ok([]).and_then(first)
        Err(error='empty list')
        >>> Err("unreadable").and_then(first)
        Err(error='unreadable')

        # Returns

        - `Ok[U]`: The callback's successful result, unchanged.
        - `Err[F]`: The callback's failed result, unchanged.
        """
        return function(self.value)

    def or_raise(self) -> T:
        """Return the required payload unchanged."""
        return self.value

    def __bool__(self) -> Never:
        """Reject implicit truth testing; use `Ok.good` to inspect success."""
        raise TypeError("use Ok.good()")


@final
class Err[E]:
    """An object representing a failed operation with type E.

    The error can be any value, it doesn't have to be an exception.

    You can use various monadic operations, similar to `dirdiff.util.Option`,
    or if you want to interact with Python's exceptions, use `@catch` to
    make a function capture an exception, or use `Err.or_raise` to raise
    an exception.
    In that case, you do need an error to subclass an Exception.
    """

    __slots__ = ("error",)
    __match_args__ = ("error",)

    def __init__(self, error: E) -> None:
        """Retain the required error unchanged."""
        self.error: Final[E] = error

    @override
    def __delattr__(self, _name: str) -> Never:
        """Reject deletion because the error payload is required."""
        raise AttributeError("Err is immutable")

    @override
    def __repr__(self) -> str:
        """Represent this Err with its error's repr."""
        return f"Err(error={self.error!r})"

    def map[U](self, _function: Callable[[Never], U]) -> Err[E]:
        """Preserve failure without invoking the callback.

        # Returns

        - `Err[E]`: This failed value, unchanged.
        - `Ok[U]`: Not possible for `Err.map`.
        """
        return self

    def map_bind[U, *Args](
        self, _arguments: tuple[*Args], _function: Callable[[Never, *Args], U]
    ) -> Err[E]:
        """Preserve failure without invoking a callback with additional inputs.

        This is the failed case of `map_bind`. Supplied arguments have already
        been evaluated by the caller; this variant retains none of them.

        # Returns

        - `Err[E]`: This failed value, unchanged.
        - `Ok[U]`: Not possible for `Err.map_bind`.
        """
        return self

    def map_err[F](self, function: Callable[[E], F]) -> Err[F]:
        """Apply function once and retain its result as an error.

        Callback failures propagate to the caller.

        # Usage

        >>> Err("unreadable").map_err(str.upper)
        Err(error='UNREADABLE')
        >>> Ok("notes").map_err(str.upper)
        Ok(value='notes')

        # Returns

        - `Err[F]`: Result of the transformation.
        - `Ok`: Not possible for `Err.map_err`.
        """
        return Err(function(self.error))

    def and_then[U, F](
        self, _function: Callable[[Never], Result[U, F]]
    ) -> Err[E]:
        """Preserve failure without invoking the Result-returning callback.

        # Returns

        - `Err[E]`: This failed value, unchanged.
        - `Ok[U]`: Not possible for `Err.and_then`.
        """
        return self

    def or_raise(self: Err[Exception]) -> Never:
        """Raise the required error because no successful payload exists.

        The error must be an Exception. The type checker rejects this method for
        other error types; use `map_err` to convert them into exceptions.
        Caught exceptions keep their original traceback, and raising adds
        this call.

        # Usage

        >>> Err(ValueError("unreadable")).or_raise()
        Traceback (most recent call last):
            ...
        ValueError: unreadable

        >>> Err("unreadable").map_err(ValueError).or_raise()
        Traceback (most recent call last):
            ...
        ValueError: unreadable
        """
        raise self.error

    def __bool__(self) -> Never:
        """Reject implicit truth testing; use `Ok.good` to inspect success."""
        raise TypeError("use Ok.good()")


type Result[T, E] = Ok[T] | Err[E]
"""A successful T or explicit error E, with a shared transformation interface.

Narrow with `Ok.good`, or match the variants directly. This alias is an
annotation, not a constructor. Use Option for absence.
"""


@overload
def catch[E1: Exception, **P, T](
    error1: type[E1], /
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E1]]]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[E1: Exception, E2: Exception, **P, T](
    error1: type[E1], error2: type[E2], /
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E1 | E2]]]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[E1: Exception, E2: Exception, E3: Exception, **P, T](
    error1: type[E1], error2: type[E2], error3: type[E3], /
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E1 | E2 | E3]]]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[E1: Exception, E2: Exception, E3: Exception, E4: Exception, **P, T](
    error1: type[E1], error2: type[E2], error3: type[E3], error4: type[E4], /
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E1 | E2 | E3 | E4]]]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    /,
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E1 | E2 | E3 | E4 | E5]]]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    E6: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    error6: type[E6],
    /,
) -> Callable[
    [Callable[P, T]], Callable[P, Result[T, E1 | E2 | E3 | E4 | E5 | E6]]
]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.
    - `error6`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5 | E6]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    E6: Exception,
    E7: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    error6: type[E6],
    error7: type[E7],
    /,
) -> Callable[
    [Callable[P, T]], Callable[P, Result[T, E1 | E2 | E3 | E4 | E5 | E6 | E7]]
]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.
    - `error6`: Exception class to catch, including its subclasses.
    - `error7`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5 | E6 | E7]`: The caught exception, unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    E6: Exception,
    E7: Exception,
    E8: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    error6: type[E6],
    error7: type[E7],
    error8: type[E8],
    /,
) -> Callable[
    [Callable[P, T]],
    Callable[P, Result[T, E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8]],
]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.
    - `error6`: Exception class to catch, including its subclasses.
    - `error7`: Exception class to catch, including its subclasses.
    - `error8`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8]`: The caught exception,
      unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    E6: Exception,
    E7: Exception,
    E8: Exception,
    E9: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    error6: type[E6],
    error7: type[E7],
    error8: type[E8],
    error9: type[E9],
    /,
) -> Callable[
    [Callable[P, T]],
    Callable[P, Result[T, E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9]],
]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.
    - `error6`: Exception class to catch, including its subclasses.
    - `error7`: Exception class to catch, including its subclasses.
    - `error8`: Exception class to catch, including its subclasses.
    - `error9`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9]`: The caught exception,
      unchanged.
    """
    ...


@overload
def catch[
    E1: Exception,
    E2: Exception,
    E3: Exception,
    E4: Exception,
    E5: Exception,
    E6: Exception,
    E7: Exception,
    E8: Exception,
    E9: Exception,
    E10: Exception,
    **P,
    T,
](
    error1: type[E1],
    error2: type[E2],
    error3: type[E3],
    error4: type[E4],
    error5: type[E5],
    error6: type[E6],
    error7: type[E7],
    error8: type[E8],
    error9: type[E9],
    error10: type[E10],
    /,
) -> Callable[
    [Callable[P, T]],
    Callable[P, Result[T, E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9 | E10]],
]:
    """Wrap a function's result in Ok, or return a caught exception as Err.

    The wrapped function keeps its parameter types. Other exceptions propagate.

    # Parameters

    - `error1`: Exception class to catch, including its subclasses.
    - `error2`: Exception class to catch, including its subclasses.
    - `error3`: Exception class to catch, including its subclasses.
    - `error4`: Exception class to catch, including its subclasses.
    - `error5`: Exception class to catch, including its subclasses.
    - `error6`: Exception class to catch, including its subclasses.
    - `error7`: Exception class to catch, including its subclasses.
    - `error8`: Exception class to catch, including its subclasses.
    - `error9`: Exception class to catch, including its subclasses.
    - `error10`: Exception class to catch, including its subclasses.

    # Returns

    The decorator's wrapped function returns:

    - `Ok[T]`: The function's result.
    - `Err[E1 | E2 | E3 | E4 | E5 | E6 | E7 | E8 | E9 | E10]`: The caught
      exception, unchanged.
    """
    ...


def catch[E: Exception, **P, T](
    *errors: type[E],
) -> Callable[[Callable[P, T]], Callable[P, Result[T, E]]]:
    """Construct a Result from a synchronous function at a Python boundary.

    Use as a decorator or wrap an existing function with
    `catch(ValueError)(function)`.

    The wrapped function keeps its parameter types and metadata.

    # Parameters

    - `errors`: One through ten `Exception` classes (not instances).

    # Capture scope
    This decorator would only catch supplied exception classes. Everything
    else will escape and propagate naturally (i.e. raise).

    # Usage

    Provide a sequence of errors you want to catch, we don't support TypeForms
    (yet).

    >>> @catch(ValueError, OverflowError)
    ... def number(text: str) -> int:
    ...     'Parse an integer or return its conversion error.'
    ...     return int(text)
    >>> number("12")
    Ok(value=12)
    >>> number("12").or_raise()
    12
    >>> result = number("bad")
    >>> Ok.good(result)
    False
    >>> isinstance(result.error, ValueError)
    True

    # Returns

    A decorator whose wrapped function returns:

    - `Ok[T]`: The function's result if it returned normally.
    - `Err[E]`: The exact exception raised by the function, with its traceback.

    # Failures

    Raises AssertionError for invalid exception classes, an invalid number of
    classes, or a coroutine or generator function. Their bodies run after the
    call returns, outside the catch block. An ordinary function returning an
    iterator or awaitable wraps that value; it does not execute it.
    """
    assert 1 <= len(errors) <= 10, (
        "catch requires one through ten exception classes"
    )
    assert all(
        isinstance(error, type) and issubclass(error, Exception)
        for error in errors
    ), "catch requires Exception subclasses"

    def decorate(function: Callable[P, T]) -> Callable[P, Result[T, E]]:
        """Wrap a synchronous function without changing its parameters.

        # Returns

        - Normal return: The wrapped function supplies Ok with the returned
          value.
        - Selected exception: The wrapped function supplies Err with that
          exception.
        """
        assert not (
            iscoroutinefunction(function)
            or isgeneratorfunction(function)
            or isasyncgenfunction(function)
        ), "catch requires a synchronous non-generator function"

        @wraps(function)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> Result[T, E]:
            """Call function once and wrap its result or selected exception.

            # Parameters

            - `args`: Positional arguments passed to function unchanged.
            - `kwargs`: Keyword arguments passed to function unchanged.

            # Returns

            - `Ok[T]`: The function's result.
            - `Err[E]`: The caught exception, unchanged.
            """
            try:
                return Ok(function(*args, **kwargs))
            except errors as error:
                return Err(error)

        return wrapped

    return decorate
