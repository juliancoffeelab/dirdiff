from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

__all__ = ["NoMonkeypatchPlugin"]


class NoMonkeypatchPlugin:
    name = "dirdiff-no-monkeypatch"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        self.tree = tree
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[NoMonkeypatchPlugin]]]:
        if "tests" not in Path(self.filename).parts:
            return

        patch_aliases = {
            alias.asname or alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "unittest.mock"
            for alias in node.names
            if alias.name == "patch"
        }
        monkeypatch_class_aliases = {
            alias.asname or alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom) and node.module == "pytest"
            for alias in node.names
            if alias.name == "MonkeyPatch"
        }
        pytest_aliases = {
            alias.asname or alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "pytest"
        }
        mock_module_aliases = {
            alias.asname or "unittest"
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "unittest.mock"
        } | {
            alias.asname or alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom) and node.module == "unittest"
            for alias in node.names
            if alias.name == "mock"
        }

        for node in ast.walk(self.tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and any(
                argument.arg == "monkeypatch"
                for argument in (
                    *node.args.posonlyargs,
                    *node.args.args,
                    *node.args.kwonlyargs,
                )
            ):
                yield (
                    node.lineno,
                    node.col_offset,
                    "NMP001 monkeypatching is forbidden",
                    type(self),
                )
                continue
            if not isinstance(node, ast.Call):
                continue
            if (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in patch_aliases
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in patch_aliases
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "patch"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in mock_module_aliases
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "patch"
                    and isinstance(node.func.value, ast.Attribute)
                    and node.func.value.attr == "mock"
                    and isinstance(node.func.value.value, ast.Name)
                    and node.func.value.value.id in mock_module_aliases
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id in monkeypatch_class_aliases
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "MonkeyPatch"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in pytest_aliases
                )
            ):
                yield (
                    node.lineno,
                    node.col_offset,
                    "NMP001 monkeypatching is forbidden",
                    type(self),
                )
