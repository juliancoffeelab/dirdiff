"""Check explicit third-party JSON cache usage in engine tests.

Flake8 loads `ToolCachePlugin`. It checks literal key spelling, conflicting
literal ToolText inputs, direct subprocess adapters outside the shared cache,
and large handwritten Difftastic facts without a synthetic-input explanation.
It reads test source without importing it or running engines. Cross-file facts
are collected once per Flake8 process; dynamic inputs remain runtime checks.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

__all__ = ["ToolCachePlugin"]

type LiteralPair = tuple[tuple[str, str], tuple[str, str]]
"""Exact left/right inline text and filename pairs established from literals."""

type CacheCall = tuple[str, str, LiteralPair, str, int, int]
"""Engine, key, literal inputs, filename, line and column of one static call."""


class ToolCachePlugin:
    """Report cache contract violations that source syntax proves.

    Each instance retains Flake8's AST and filename. The process-wide inventory
    contains only literal calls from test source, never tool results. Calls
    through dynamic wrappers or variable inputs may escape conflict detection;
    their input hashes must still agree at runtime.
    """

    name = "dirdiff-tool-cache"
    version = "0.1.0"
    _inventory: ClassVar[list[CacheCall] | None] = None
    """Literal source facts read once per worker, independent of visit order."""

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Retain the parsed module and its source path for one Flake8 pass.

        # Parameters

        - `tree`: Flake8's parsed Python module, never executed.
        - `filename`: Source path used for diagnostics and synthetic comments.
        """
        self.tree = tree
        self.filename = filename

    def run(self) -> Iterator[tuple[int, int, str, type[ToolCachePlugin]]]:
        """Yield TJC diagnostics at statically invalid calls in this module.

        TJC001 rejects key spelling, TJC002 conflicting literal inputs,
        TJC003 uncached JSON execution, and TJC004 large synthetic JSON without
        a nearby `# tool-json: synthetic <reason>` explanation. No code runs.

        # Returns

        - `Location`: Source line and zero-based column of each invalid call.
        - `Diagnostic`: TJC message and plugin type consumed by Flake8.
        """
        if "tests" not in Path(self.filename).parts:
            return

        def calls(tree: ast.AST, filename: str) -> list[CacheCall]:
            """Collect direct imported tool_json calls with fully literal inputs.

            Unknown expressions contribute no fact. In particular, differing
            Path spellings do not prove differing input contents.

            # Parameters

            - `tree`: Parsed test source from which literal calls are collected.
            - `filename`: Diagnostic path attached to each established fact.
            """
            aliases = {
                alias.asname or alias.name: alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module == "helpers"
                for alias in node.names
            }
            found: list[CacheCall] = []
            for node in ast.walk(tree):
                if (
                    not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Name)
                    or aliases.get(node.func.id) != "tool_json"
                ):
                    continue
                options = {kw.arg: kw.value for kw in node.keywords}
                engine = (
                    node.args[0]
                    if len(node.args) > 0
                    else options.get("engine")
                )
                key = (
                    node.args[1]
                    if len(node.args) > 1
                    else options.get("cache_key")
                )
                if (
                    not isinstance(engine, ast.Constant)
                    or not isinstance(engine.value, str)
                    or not isinstance(key, ast.Constant)
                    or not isinstance(key.value, str)
                ):
                    continue
                sides: list[tuple[str, str]] = []
                for side in (options.get("left"), options.get("right")):
                    if (
                        not isinstance(side, ast.Call)
                        or not isinstance(side.func, ast.Name)
                        or aliases.get(side.func.id) != "ToolText"
                    ):
                        break
                    fields = {kw.arg: kw.value for kw in side.keywords}
                    text = (
                        side.args[0]
                        if len(side.args) > 0
                        else fields.get("text")
                    )
                    name = (
                        side.args[1]
                        if len(side.args) > 1
                        else fields.get("name")
                    )
                    if (
                        not isinstance(text, ast.Constant)
                        or not isinstance(text.value, str)
                        or not isinstance(name, ast.Constant)
                        or not isinstance(name.value, str)
                    ):
                        break
                    sides.append((text.value, name.value))
                if len(sides) == 2:
                    found.append(
                        (
                            engine.value,
                            key.value,
                            (sides[0], sides[1]),
                            filename,
                            node.lineno,
                            node.col_offset,
                        )
                    )
            return found

        if ToolCachePlugin._inventory is None:
            # Flake8 may use multiple workers. Each worker reads one inventory;
            # conflict detection does not depend on file visitation order.
            inventory: list[CacheCall] = []
            root = Path(__file__).resolve().parents[1] / "tests"
            for directory in root.iterdir():
                if directory.is_dir() and directory.name not in {
                    "presets",
                    "golden",
                    "e2e_temp",
                    "__pycache__",
                }:
                    for path in directory.rglob("test_*.py"):
                        inventory.extend(
                            calls(
                                ast.parse(path.read_text()), str(path.resolve())
                            )
                        )
            ToolCachePlugin._inventory = inventory
        local = calls(self.tree, str(Path(self.filename).resolve()))
        inventory = ToolCachePlugin._inventory + local
        facts = {(call[4], call[5]): call for call in local}
        lines = Path(self.filename).read_text().splitlines()
        aliases = {
            alias.asname or alias.name: alias.name
            for node in ast.walk(self.tree)
            if isinstance(node, ast.ImportFrom) and node.module == "helpers"
            for alias in node.names
        }
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            name = aliases.get(name, name)
            options = {kw.arg: kw.value for kw in node.keywords}
            key = options.get("cache_key")
            if name == "tool_json" and len(node.args) > 1:
                key = node.args[1]
            if (node.lineno, node.col_offset) in facts:
                _, known_key, pair, _, _, _ = facts[
                    node.lineno, node.col_offset
                ]
                if (
                    re.fullmatch(
                        r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*",
                        known_key,
                    )
                    is None
                ):
                    yield (
                        node.lineno,
                        node.col_offset,
                        "TJC001 invalid tool cache key",
                        type(self),
                    )
                allowed = options.get("allow_mismatches")
                if not (
                    isinstance(allowed, ast.Constant) and allowed.value is True
                ):
                    for (
                        other_engine,
                        other_key,
                        other_pair,
                        other_file,
                        other_line,
                        _,
                    ) in inventory:
                        if (other_engine, other_key) == facts[
                            node.lineno, node.col_offset
                        ][:2] and other_pair != pair:
                            yield (
                                node.lineno,
                                node.col_offset,
                                f"TJC002 tool cache inputs conflict with {other_file}:{other_line}",
                                type(self),
                            )
                            break
            elif isinstance(key, ast.Constant) and (
                not isinstance(key.value, str)
                or re.fullmatch(
                    r"[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*",
                    key.value,
                )
                is None
            ):
                yield (
                    node.lineno,
                    node.col_offset,
                    "TJC001 invalid tool cache key",
                    type(self),
                )
            if (
                name
                in {
                    "_run_diffsitter_json",
                    "_run_difftastic_json",
                    "_run_gumtree_json",
                    "run_diffsitter_json",
                    "run_difftastic_json",
                    "run_gumtree_json",
                }
                and Path(self.filename).name != "helpers.py"
            ):
                yield (
                    node.lineno,
                    node.col_offset,
                    "TJC003 executable JSON must go through tool_json",
                    type(self),
                )
            if (
                name == "difftastic_rows_from_json"
                and node.args
                and isinstance(node.args[0], ast.Dict)
            ):
                payload = node.args[0]
                assert payload.end_lineno is not None
                if payload.end_lineno - payload.lineno + 1 >= 10 and not any(
                    re.search(r"# tool-json: synthetic\s+\S", line)
                    for line in lines[max(0, node.lineno - 3) : payload.lineno]
                ):
                    yield (
                        node.lineno,
                        node.col_offset,
                        "TJC004 use real cached JSON or explain deliberately synthetic input",
                        type(self),
                    )
