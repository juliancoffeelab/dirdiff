"""Collect class-local route declarations for later FastAPI registration.

`ClassRoutes` provides the HTTP decorators used by the server and binds their
original functions to one server instance during application construction. Its
declaration records contain route metadata only.

The collector retains no application, server, database, or other runtime
interface. FastAPI and Starlette handle every HTTP entity after registration.

---
Hi, human here.
The goal of this module is to keep the hot-reloading entrypoint of uvicorn
without clamping it all into one closure.
So this class here is doing all the dark magic stuff with hot patching
FastAPI app to invert dependency and structure our code the way we want.
"""

from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType, UnionType
from typing import Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from dirdiff.server.base import Responses

__all__ = [
    "ClassRoutes",
]


type _ResponseModel = type[BaseModel] | UnionType
"""One response-model form used by this server's route declarations.

Routes use Pydantic model classes or one union of model classes. The collector
accepts no other explicit model syntax.
"""


@dataclass(frozen=True)
class _HttpRouteDeclaration:
    """Retain one HTTP route declaration until application construction.

    Each instance contains only the FastAPI options used by this module. The
    endpoint remains the original class-body function until `ClassRoutes`
    validates and binds it to one `_Server`.
    """

    method: Literal["GET", "POST", "PATCH", "DELETE"]
    """HTTP method passed unchanged to FastAPI registration."""

    path: str
    """Absolute application path declared beside the endpoint method."""

    endpoint: FunctionType
    """Original function returned unchanged by the route decorator."""

    response_model: _ResponseModel | None
    """Explicit response model, or `None` to let FastAPI infer it."""

    status_code: int | None
    """Declared success status, or `None` for FastAPI's ordinary default."""

    responses: Responses | None
    """Additional OpenAPI response metadata supplied by the declaration."""

    summary: str | None
    """Optional OpenAPI summary declared beside the endpoint."""

    response_class: type[Response]
    """Concrete response class registered for the route."""


@dataclass(frozen=True)
class _ExceptionHandlerDeclaration:
    """Retain one exception-handler declaration until app construction.

    The exception class and original `_Server` function are the complete
    declaration. FastAPI receives the bound method only after validation.
    """

    exception_class: type[Exception]
    """Exception type whose failures FastAPI sends to this handler."""

    endpoint: FunctionType
    """Original class-body function returned unchanged by the decorator."""


type _ClassRouteDeclaration = (
    _HttpRouteDeclaration | _ExceptionHandlerDeclaration
)
"""One source-ordered declaration retained by `ClassRoutes`."""


class ClassRoutes:
    """Collect and bind the small FastAPI decorator set used by `_Server`.

    Decorators record declarations and return their exact input functions.
    `register` first validates the complete declaration set, then binds each
    function to one concrete `_Server` and gives it to FastAPI in source order.

    The collector stores no application, server, database, or other runtime
    interface. It does not dispatch HTTP entities after construction.
    """

    def __init__(self) -> None:
        """Create an empty import-time declaration collector."""
        self._declarations: list[_ClassRouteDeclaration] = []

    def get[Endpoint](
        self,
        path: str,
        *,
        response_model: _ResponseModel | None = None,
        status_code: int | None = None,
        responses: Responses | None = None,
        summary: str | None = None,
        response_class: type[Response] = JSONResponse,
    ) -> Callable[[Endpoint], Endpoint]:
        """Record one GET declaration and preserve its endpoint function.

        # Parameters

        - `path`: Absolute FastAPI route path.
        - `response_model`: Explicit model, or `None` for FastAPI inference.
        - `status_code`: Explicit success status, or the ordinary default.
        - `responses`: Additional response models for generated API metadata.
        - `summary`: Optional summary for generated API metadata.
        - `response_class`: Response class FastAPI uses for this route.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """
        return self._http_route(
            "GET",
            path,
            response_model=response_model,
            status_code=status_code,
            responses=responses,
            summary=summary,
            response_class=response_class,
        )

    def post[Endpoint](
        self,
        path: str,
        *,
        response_model: _ResponseModel | None = None,
        status_code: int | None = None,
        responses: Responses | None = None,
        summary: str | None = None,
        response_class: type[Response] = JSONResponse,
    ) -> Callable[[Endpoint], Endpoint]:
        """Record one POST declaration and preserve its endpoint function.

        # Parameters

        - `path`: Absolute FastAPI route path.
        - `response_model`: Explicit model, or `None` for FastAPI inference.
        - `status_code`: Explicit success status, or the ordinary default.
        - `responses`: Additional response models for generated API metadata.
        - `summary`: Optional summary for generated API metadata.
        - `response_class`: Response class FastAPI uses for this route.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """
        return self._http_route(
            "POST",
            path,
            response_model=response_model,
            status_code=status_code,
            responses=responses,
            summary=summary,
            response_class=response_class,
        )

    def patch[Endpoint](
        self,
        path: str,
        *,
        response_model: _ResponseModel | None = None,
        status_code: int | None = None,
        responses: Responses | None = None,
        summary: str | None = None,
        response_class: type[Response] = JSONResponse,
    ) -> Callable[[Endpoint], Endpoint]:
        """Record one PATCH declaration and preserve its endpoint function.

        # Parameters

        - `path`: Absolute FastAPI route path.
        - `response_model`: Explicit model, or `None` for FastAPI inference.
        - `status_code`: Explicit success status, or the ordinary default.
        - `responses`: Additional response models for generated API metadata.
        - `summary`: Optional summary for generated API metadata.
        - `response_class`: Response class FastAPI uses for this route.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """
        return self._http_route(
            "PATCH",
            path,
            response_model=response_model,
            status_code=status_code,
            responses=responses,
            summary=summary,
            response_class=response_class,
        )

    def delete[Endpoint](
        self,
        path: str,
        *,
        response_model: _ResponseModel | None = None,
        status_code: int | None = None,
        responses: Responses | None = None,
        summary: str | None = None,
        response_class: type[Response] = JSONResponse,
    ) -> Callable[[Endpoint], Endpoint]:
        """Record one DELETE declaration and preserve its endpoint function.

        # Parameters

        - `path`: Absolute FastAPI route path.
        - `response_model`: Explicit model, or `None` for FastAPI inference.
        - `status_code`: Explicit success status, or the ordinary default.
        - `responses`: Additional response models for generated API metadata.
        - `summary`: Optional summary for generated API metadata.
        - `response_class`: Response class FastAPI uses for this route.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """
        return self._http_route(
            "DELETE",
            path,
            response_model=response_model,
            status_code=status_code,
            responses=responses,
            summary=summary,
            response_class=response_class,
        )

    def _http_route[Endpoint](
        self,
        method: Literal["GET", "POST", "PATCH", "DELETE"],
        path: str,
        *,
        response_model: _ResponseModel | None,
        status_code: int | None,
        responses: Responses | None,
        summary: str | None,
        response_class: type[Response],
    ) -> Callable[[Endpoint], Endpoint]:
        """Build a decorator that records one typed HTTP declaration.

        # Parameters

        - `method`: HTTP method FastAPI registers for the endpoint.
        - `path`: Absolute FastAPI route path.
        - `response_model`: Explicit model, or `None` for FastAPI inference.
        - `status_code`: Explicit success status, or the ordinary default.
        - `responses`: Additional response models for generated API metadata.
        - `summary`: Optional summary for generated API metadata.
        - `response_class`: Response class FastAPI uses for this route.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """

        def record(endpoint: Endpoint) -> Endpoint:
            """Append the declaration and return the original function."""
            assert isinstance(endpoint, FunctionType)
            self._declarations.append(
                _HttpRouteDeclaration(
                    method=method,
                    path=path,
                    endpoint=endpoint,
                    response_model=response_model,
                    status_code=status_code,
                    responses=responses,
                    summary=summary,
                    response_class=response_class,
                )
            )
            return endpoint

        return record

    def exception_handler[Endpoint](
        self,
        exception_class: type[Exception],
    ) -> Callable[[Endpoint], Endpoint]:
        """Record one exception handler and preserve its endpoint function.

        # Parameters

        - `exception_class`: Failure type FastAPI sends to the bound handler.

        # Returns

        - A decorator accepting one undecorated `_Server` function.
        - Applying it records the declaration and returns that exact function.
        """

        def record(endpoint: Endpoint) -> Endpoint:
            """Append the declaration and return the original function."""
            assert isinstance(endpoint, FunctionType)
            self._declarations.append(
                _ExceptionHandlerDeclaration(exception_class, endpoint)
            )
            return endpoint

        return record

    def register[Server](self, app: FastAPI, server: Server) -> None:
        """Validate and bind every declaration onto one fresh application.

        The concrete class must still expose every original function under its
        declared name, and no function may have more than one declaration. All
        validation precedes registration so invalid input cannot leave a
        partially configured application.

        # Parameters

        - `app`: Fresh FastAPI application receiving the declarations.
        - `server`: Concrete instance whose original methods are bound.
        """
        server_class = type(server)
        declared_endpoints: set[FunctionType] = set()
        for declaration in self._declarations:
            endpoint = declaration.endpoint
            assert endpoint not in declared_endpoints, (
                f"duplicate route declaration for {endpoint.__name__}"
            )
            assert server_class.__dict__.get(endpoint.__name__) is endpoint, (
                f"declared endpoint {endpoint.__name__} was replaced"
            )
            declared_endpoints.add(endpoint)

        for declaration in self._declarations:
            original = declaration.endpoint
            bound_endpoint = original.__get__(server, server_class)
            if isinstance(declaration, _ExceptionHandlerDeclaration):
                app.add_exception_handler(
                    declaration.exception_class,
                    bound_endpoint,
                )
                continue
            # FastAPI rejects valid Mapping response metadata in its annotation:
            # https://github.com/fastapi/fastapi/discussions/16259
            #
            # And if we type-ignoring it anyway, we also made it accept
            # HTTPStatus instead of "str | int", to bend the covariance in the
            # mapping key:
            # https://github.com/fastapi/fastapi/discussions/16259
            if declaration.response_model is None:
                app.add_api_route(
                    declaration.path,
                    bound_endpoint,
                    methods=[declaration.method],
                    status_code=declaration.status_code,
                    responses=declaration.responses,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
                    summary=declaration.summary,
                    response_class=declaration.response_class,
                )
            else:
                app.add_api_route(
                    declaration.path,
                    bound_endpoint,
                    methods=[declaration.method],
                    response_model=declaration.response_model,
                    status_code=declaration.status_code,
                    responses=declaration.responses,  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
                    summary=declaration.summary,
                    response_class=declaration.response_class,
                )
