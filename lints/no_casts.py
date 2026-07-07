"""Flake8 plugin for banning `typing.cast` escape hatches.

CST is an AST-only lint for the narrow syntactic contract that dirdiff code
must not use `typing.cast` or `typing_extensions.cast`. Runtime and external
payload boundaries should validate or narrow values with ordinary Python
checks, then let the type checker see that contract. Semantic boolean checks
belong to SBT; this module only reports cast calls.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import override

CAST_CODE = "CST001"
CAST_MESSAGE = f"{CAST_CODE} typing.cast is forbidden; validate or narrow the value instead"

__all__ = ["NoCastsPlugin"]


class NoCastsPlugin:
    """Report `typing.cast` and `typing_extensions.cast` calls via flake8."""

    name = "dirdiff-no-casts"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        self.tree = tree
        self.filename = filename

    def run(self) -> Iterator[tuple[int, int, str, type[NoCastsPlugin]]]:
        visitor = _CastVisitor()
        visitor.visit(self.tree)
        for node in visitor.cast_calls:
            yield node.lineno, node.col_offset, CAST_MESSAGE, type(self)


class _CastVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.cast_names: set[str] = set()
        self.cast_modules: set[str] = set()
        self.cast_calls: list[ast.Call] = []

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in {"typing", "typing_extensions"}:
                self.cast_modules.add(alias.asname or alias.name)
        self.generic_visit(node)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module not in {"typing", "typing_extensions"}:
            self.generic_visit(node)
            return

        for alias in node.names:
            if alias.name == "cast":
                self.cast_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        if _is_cast_call(
            node.func,
            cast_names=self.cast_names,
            cast_modules=self.cast_modules,
        ):
            self.cast_calls.append(node)
        self.generic_visit(node)


def _is_cast_call(
    func: ast.expr,
    *,
    cast_names: set[str],
    cast_modules: set[str],
) -> bool:
    if isinstance(func, ast.Name):
        return func.id in cast_names
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "cast"
        and isinstance(func.value, ast.Name)
        and func.value.id in cast_modules
    )
