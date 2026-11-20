from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import FunctionType, UnionType
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

if TYPE_CHECKING:
    from dirdiff.server.base import Responses

__all__ = ["ClassRoutes"]


type _ResponseModel = type[BaseModel] | UnionType


@dataclass(frozen=True)
class _HttpRouteDeclaration:
    method: Literal["GET", "POST", "PATCH", "DELETE"]
    path: str
    endpoint: FunctionType
    response_model: _ResponseModel | None
    status_code: int | None
    responses: Responses | None
    summary: str | None
    response_class: type[Response]


@dataclass(frozen=True)
class _ExceptionHandlerDeclaration:
    exception_class: type[Exception]
    endpoint: FunctionType


type _ClassRouteDeclaration = (
    _HttpRouteDeclaration | _ExceptionHandlerDeclaration
)


class ClassRoutes:
    def __init__(self) -> None:
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
        def record(endpoint: Endpoint) -> Endpoint:
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
        def record(endpoint: Endpoint) -> Endpoint:
            assert isinstance(endpoint, FunctionType)
            self._declarations.append(
                _ExceptionHandlerDeclaration(exception_class, endpoint)
            )
            return endpoint

        return record

    def register[Server](self, app: FastAPI, server: Server) -> None:
        # Validate every declaration before registering any, so a bad one
        # cannot leave the app half configured.
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
