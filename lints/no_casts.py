"""Reject imported typing cast calls during Flake8 runs.

## Public interface

Flake8 loads `NoCastsPlugin`. Each instance receives one parsed module and
yields CST001 for calls recognized from `typing.cast` or
`typing_extensions.cast` imports.

## Purpose and boundaries

The rule keeps type narrowing visible as validation or ordinary Python control
flow. It is syntactic: it does not import source, resolve re-exports, track
shadowing after an import, or perform type analysis.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from typing import override

CAST_CODE = "CST001"
"""Stable Flake8 code for every forbidden cast call this plugin recognizes.

Direct and module-qualified imports share the same rule identity.
"""
CAST_MESSAGE = f"{CAST_CODE} typing.cast is forbidden; validate or narrow the value instead"
"""Complete diagnostic text shared by direct and module-qualified cast calls.

The plugin appends no alias spelling because all recognized forms violate the
same project contract.
"""

__all__ = ["NoCastsPlugin"]


class NoCastsPlugin:
    """Report imported typing cast calls through Flake8.

    Each instance retains one parsed module. `run` creates a fresh syntactic
    visitor, so alias recognition never imports code, resolves re-exports, or
    leaks between files.

    # Usage

    Register the class as a Flake8 AST plugin. Flake8 constructs it with the
    parsed tree and filename, then iterates `run` once for that file.
    """

    name = "dirdiff-no-casts"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Retain one parsed module for Flake8's later plugin iteration.

        # Parameters

        - `tree`: Parsed source module to inspect syntactically.
        - `filename`: Source path supplied by Flake8 for plugin compatibility.

        This lint does not reopen the file, so `filename` does not affect the
        diagnostics.
        """
        self.tree = tree
        self.filename = filename

    def run(self) -> Iterator[tuple[int, int, str, type[NoCastsPlugin]]]:
        """Yield one Flake8 diagnostic for each recognized cast call.

        Results follow AST traversal order. Running the same plugin instance
        creates a fresh visitor and retains no aliases or calls between runs.

        # Usage

        Flake8 consumes each tuple as line, zero-based column, complete message,
        and plugin class.

        # Returns

        - `First`: Source line of the cast call.
        - `Second`: Zero-based source column.
        - `Third`: The NC001 diagnostic message.
        - `Fourth`: This plugin class, as required by Flake8.
        - `Order`: Diagnostics follow AST traversal order; an empty iterator
          means no recognized call used a typing cast.

        # Failures

        This AST-only pass has no expected domain failure. An invalid object in
        place of the parsed tree fails through the standard AST visitor.
        """
        visitor = _CastVisitor()
        visitor.visit(self.tree)
        for node in visitor.cast_calls:
            yield node.lineno, node.col_offset, CAST_MESSAGE, type(self)


class _CastVisitor(ast.NodeVisitor):
    """Collect cast call sites recognized from typing imports and aliases.

    `NoCastsPlugin.run` creates one visitor per module, visits the supplied AST,
    and reports the collected calls. Imported function and module aliases are
    tracked separately, so a name spelled `cast` is ignored unless a supported
    typing import made that spelling eligible.

    This visitor performs syntactic recognition only; it does not import code,
    resolve re-exports, track later shadowing, or perform type analysis.
    """

    def __init__(self) -> None:
        """Create empty alias indexes and a source-ordered call collection.

        One visitor handles one module traversal. Callers must create another
        visitor rather than carrying import bindings into a different module.
        """
        self.cast_names: set[str] = set()
        self.cast_modules: set[str] = set()
        self.cast_calls: list[ast.Call] = []

    @override
    def visit_Import(self, node: ast.Import) -> None:
        """Record aliases for imported typing modules before visiting children.

        Only `typing` and `typing_extensions` qualify. A module alias becomes a
        valid qualifier for later `.cast(...)` calls in this traversal.
        """
        for alias in node.names:
            if alias.name in {"typing", "typing_extensions"}:
                self.cast_modules.add(alias.asname or alias.name)
        self.generic_visit(node)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record direct cast aliases imported from either typing module.

        Other imported members and relative or unrelated modules do not add a
        forbidden callee name. Child syntax is still traversed.
        """
        if node.module not in {"typing", "typing_extensions"}:
            self.generic_visit(node)
            return

        for alias in node.names:
            if alias.name == "cast":
                self.cast_names.add(alias.asname or alias.name)
        self.generic_visit(node)

    @override
    def visit_Call(self, node: ast.Call) -> None:
        """Collect a call when its callee matches an indexed cast import.

        The call's start coordinate becomes the diagnostic location. Nested
        calls are visited afterward so every recognized cast is reported.
        """
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
    """Recognize a cast call through direct or module import aliases.

    # Parameters

    - `func`: Callee expression from one AST call.
    - `cast_names`: Names imported directly from supported typing modules.
    - `cast_modules`: Aliases bound to supported typing modules.

    Attribute chains do not match. The caller supplies names learned from
    imports; this predicate does not decide whether a later binding shadowed
    one of them.
    """
    if isinstance(func, ast.Name):
        return func.id in cast_names
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "cast"
        and isinstance(func.value, ast.Name)
        and func.value.id in cast_modules
    )
