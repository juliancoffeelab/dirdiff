"""Flake8 plugin for dmypy-backed boolean-expression checks.

Flake8 supplies the invocation boundary while Python's AST supplies exact
boolean-expression spans. A one-shot dmypy daemon consumes a library-produced
fine-grained cache, exports expression types, and answers span inspections.
The daemon is always stopped before diagnostics return; this module must not
leave background processes, maintain a separate SBT cache, or inspect types
when no SBT code is selected.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import time
import tomllib
from argparse import Namespace
from collections.abc import Iterator, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import ClassVar, Protocol, override

import mypy.build
import mypy.dmypy.client
import mypy.dmypy_server
import mypy.find_sources
import mypy.options
from mypy.errors import CompileError

TRUTHY_CODE = "SBT001"
OR_FALLBACK_CODE = "SBT002"
BOOL_INSTANCE = "builtins.bool"
SBT_CACHE_DIR = Path(".sbt") / "mypy_cache"

__all__ = ["StrictBoolPlugin"]


class _OptionRegistrar(Protocol):
    """Accept the one Flake8 option-registration call this plugin makes."""

    def add_option(
        self,
        name: str,
        *,
        default: str,
        parse_from_config: bool,
        help: str,
    ) -> None:
        """Register one configuration-backed string option with Flake8."""


@dataclass(frozen=True)
class Diagnostic:
    """Represent one SBT violation in Flake8's coordinate system."""

    path: str
    line: int
    column: int
    code: str
    message: str

    def flake8_error(self) -> tuple[int, int, str, type[StrictBoolPlugin]]:
        """Return the tuple consumed by Flake8's plugin iterator."""
        return (
            self.line,
            self.column,
            f"{self.code} {self.message}",
            StrictBoolPlugin,
        )


class StrictBoolPlugin:
    """Run project-scoped strict boolean checks once per Flake8 process."""

    name = "dirdiff-strict-bool"
    version = "0.2.0"
    _enabled_codes = frozenset({TRUTHY_CODE, OR_FALLBACK_CODE})
    _flake8_paths: Sequence[str] = ()
    _mypy_config = Path("pyproject.toml")
    _diagnostics_by_path: ClassVar[dict[str, list[Diagnostic]]] = {}
    _has_checked = False

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Retain the filename whose project-scoped diagnostics Flake8 requests."""
        self.filename = filename

    @classmethod
    def add_options(cls, parser: _OptionRegistrar) -> None:
        """Register the Mypy configuration path used for the one-shot build."""
        parser.add_option(
            "--sbt-mypy-config",
            default="pyproject.toml",
            parse_from_config=True,
            help="Path to pyproject.toml containing [tool.mypy].",
        )

    @classmethod
    def parse_options(cls, options: Namespace) -> None:
        """Capture Flake8 paths, selected SBT codes, and Mypy configuration."""
        filenames = getattr(options, "filenames", ())
        if isinstance(filenames, list | tuple):
            cls._flake8_paths = tuple(str(filename) for filename in filenames)
        else:
            cls._flake8_paths = ()
        cls._enabled_codes = _enabled_codes_from_options(options=options)
        cls._mypy_config = Path(str(options.sbt_mypy_config))

    def run(self) -> Iterator[tuple[int, int, str, type[StrictBoolPlugin]]]:
        """Yield this file's diagnostics after one shared dmypy inspection run."""
        plugin_type = type(self)
        if len(plugin_type._enabled_codes) == 0:
            return

        path = str(Path(self.filename).resolve())
        if not plugin_type._has_checked:

            def _group_diagnostics(
                diagnostics: Sequence[Diagnostic],
            ) -> dict[str, list[Diagnostic]]:
                """Index project diagnostics by path for Flake8 instances."""
                grouped: dict[str, list[Diagnostic]] = {}
                for diagnostic in diagnostics:
                    grouped.setdefault(diagnostic.path, []).append(diagnostic)
                return grouped

            paths = plugin_type._flake8_paths or (path,)
            diagnostics = _collect_diagnostics(
                paths=paths,
                config_path=plugin_type._mypy_config,
                enabled_codes=plugin_type._enabled_codes,
            )
            plugin_type._diagnostics_by_path = _group_diagnostics(diagnostics)
            plugin_type._has_checked = True

        for diagnostic in plugin_type._diagnostics_by_path.get(path, []):
            yield diagnostic.flake8_error()


class StrictBoolVisitor(ast.NodeVisitor):
    """Apply SBT rules to one Python AST using dmypy for expression types."""

    def __init__(
        self,
        path: str,
        status_file: str,
        enabled_codes: frozenset[str],
    ) -> None:
        """Bind diagnostics to one file and one checked daemon state."""
        self.path = path
        self.status_file = status_file
        self.enabled_codes = enabled_codes
        self.diagnostics: list[Diagnostic] = []
        self._boolean_context = 0
        self._or_parent = 0
        self._condition_expr_ids: set[int] = set()
        self._type_by_span: dict[str, str | None] = {}

    @override
    def visit_If(self, node: ast.If) -> None:
        """Check an `if` condition before walking its nested syntax."""
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_While(self, node: ast.While) -> None:
        """Check a `while` condition before walking its nested syntax."""
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_Assert(self, node: ast.Assert) -> None:
        """Check an assertion condition before walking its nested syntax."""
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Reject outer value-fallback `or` expressions outside conditions."""
        is_or = isinstance(node.op, ast.Or)
        if (
            is_or
            and OR_FALLBACK_CODE in self.enabled_codes
            and self._boolean_context == 0
            and self._or_parent == 0
            and id(node) not in self._condition_expr_ids
            and not self._is_boolean_operation(node)
        ):
            self._add(
                node,
                OR_FALLBACK_CODE,
                "avoid value fallback with 'or'; make the fallback explicit",
            )

        if is_or:
            self._or_parent += 1
        self.generic_visit(node)
        if is_or:
            self._or_parent -= 1

    def _check_boolean_condition(self, expression: ast.expr) -> None:
        """Check one expression tree while suppressing value-fallback rules."""
        self._boolean_context += 1
        self._check_bool_expr(expression)
        self._boolean_context -= 1

    def _check_bool_expr(self, expression: ast.expr) -> None:
        """Require each non-operator condition leaf to have a boolean type."""
        self._condition_expr_ids.add(id(expression))
        if isinstance(expression, ast.BoolOp):
            for value in expression.values:
                self._check_bool_expr(value)
            return
        if isinstance(expression, ast.UnaryOp) and isinstance(
            expression.op, ast.Not
        ):
            self._check_bool_expr(expression.operand)
            return
        if isinstance(expression, ast.Compare):
            return
        if (
            self._is_bool_type(expression)
            or TRUTHY_CODE not in self.enabled_codes
        ):
            return
        self._add(
            expression,
            TRUTHY_CODE,
            "implicit truthiness is forbidden; use an explicit bool expression",
        )

    def _is_boolean_operation(self, expression: ast.expr) -> bool:
        """Report whether syntax and inspected leaves form a boolean operation."""
        if isinstance(expression, ast.BoolOp):
            return all(
                self._is_boolean_operation(value) for value in expression.values
            )
        if isinstance(expression, ast.UnaryOp) and isinstance(
            expression.op, ast.Not
        ):
            return self._is_boolean_operation(expression.operand)
        if isinstance(expression, ast.Compare):
            return True
        return self._is_bool_type(expression)

    def _is_bool_type(self, expression: ast.expr) -> bool:
        """Classify one exact AST span from dmypy's formatted type result."""
        # dmypy expects the AST's exact byte span with a one-based start column.
        assert expression.end_lineno is not None
        assert expression.end_col_offset is not None
        location = (
            f"{self.path}:{expression.lineno}:{expression.col_offset + 1}:"
            f"{expression.end_lineno}:{expression.end_col_offset}"
        )
        if location not in self._type_by_span:
            self._type_by_span[location] = _inspect_type(
                status_file=self.status_file,
                location=location,
            )
        type_text = self._type_by_span[location]
        return type_text is not None and _is_bool_type_text(type_text)

    def _add(self, expression: ast.expr, code: str, message: str) -> None:
        """Record one AST expression using Flake8's zero-based column."""
        self.diagnostics.append(
            Diagnostic(
                path=self.path,
                line=expression.lineno,
                column=expression.col_offset,
                code=code,
                message=message,
            )
        )


def _collect_diagnostics(
    paths: Sequence[str],
    config_path: Path,
    enabled_codes: frozenset[str],
) -> list[Diagnostic]:
    """Build a fine cache, inspect the project, and stop the one-shot daemon."""
    started = time.monotonic()
    options = _read_pyproject_options(config_path=config_path)
    expanded_paths = _expand_python_paths(paths=paths, options=options)
    if len(expanded_paths) == 0:
        return []

    options.incremental = True
    options.cache_fine_grained = True
    options.cache_dir = str(SBT_CACHE_DIR)
    sources = mypy.find_sources.create_source_list(
        paths=expanded_paths,
        options=options,
        allow_empty_dir=False,
    )
    try:
        mypy.build.build(
            sources=sources,
            options=options,
            stdout=StringIO(),
            stderr=StringIO(),
        )
    except CompileError:
        return []

    with tempfile.TemporaryDirectory(prefix="dirdiff-sbt-") as temporary_dir:
        status_file = str(Path(temporary_dir) / "dmypy.json")
        daemon_options = _read_pyproject_options(config_path=config_path)
        daemon_options.incremental = True
        daemon_options.local_partial_types = True
        daemon_options.cache_fine_grained = True
        daemon_options.use_fine_grained_cache = True
        daemon_options.cache_dir = str(SBT_CACHE_DIR)
        result = mypy.dmypy_server.daemonize(
            daemon_options,
            status_file,
            timeout=None,
            log_file=None,
        )
        if result != 0:
            return []
        with redirect_stdout(StringIO()):
            mypy.dmypy.client.wait_for_server(status_file)
        try:
            response = mypy.dmypy.client.request(
                status_file,
                "check",
                files=list(expanded_paths),
                export_types=True,
            )
            if response.get("status") != 0:
                return []

            diagnostics: list[Diagnostic] = []
            for raw_path in expanded_paths:
                path = Path(raw_path).resolve()
                tree = ast.parse(path.read_text(), filename=str(path))
                visitor = StrictBoolVisitor(
                    path=str(path),
                    status_file=status_file,
                    enabled_codes=enabled_codes,
                )
                visitor.visit(tree)
                diagnostics.extend(visitor.diagnostics)
        finally:
            mypy.dmypy.client.request(status_file, "stop", timeout=5)

    diagnostics.sort(
        key=lambda item: (item.path, item.line, item.column, item.code)
    )
    print(
        f"SBT debug: checked {len(expanded_paths)} files in "
        f"{time.monotonic() - started:.3f}s",
        file=sys.stderr,
    )
    return diagnostics


def _inspect_type(status_file: str, location: str) -> str | None:
    """Return the fully qualified formatted type for one exact expression."""
    response = mypy.dmypy.client.request(
        status_file,
        "inspect",
        show="type",
        location=location,
        verbosity=2,
        limit=0,
        include_span=False,
        include_kind=False,
        include_object_attrs=False,
        union_attrs=False,
        force_reload=False,
    )
    if response.get("status") != 0:
        return None
    output = response.get("out")
    if not isinstance(output, str):
        return None
    output = output.removesuffix("\n")
    if output.startswith('"') and output.endswith('"'):
        return output[1:-1]
    return output


def _is_bool_type_text(type_text: str) -> bool:
    """Match Mypy's verbosity-two rendering of current SBT boolean types."""

    def is_bool_atom(item: str) -> bool:
        """Recognize a concrete bool instance or bool-valued literal type."""
        if item == BOOL_INSTANCE:
            return True
        if not item.startswith("Literal[") or not item.endswith("]"):
            return False
        values = item.removeprefix("Literal[").removesuffix("]").split(", ")
        return len(values) > 0 and all(
            value in {"True", "False"} for value in values
        )

    return all(is_bool_atom(item) for item in type_text.split(" | "))


def _enabled_codes_from_options(options: Namespace) -> frozenset[str]:
    """Resolve the SBT subset selected by Flake8's prefix configuration."""

    def _option_code_prefixes(
        options: Namespace, names: Sequence[str]
    ) -> set[str]:
        """Collect string code prefixes from sequence or scalar options."""
        prefixes: set[str] = set()
        for name in names:
            raw_value = getattr(options, name, ())
            if isinstance(raw_value, str):
                prefixes.add(raw_value)
            elif isinstance(raw_value, list | tuple | set | frozenset):
                prefixes.update(
                    item for item in raw_value if isinstance(item, str)
                )
        return prefixes

    selected = _option_code_prefixes(
        options=options, names=("select", "extend_select")
    )
    ignored = _option_code_prefixes(
        options=options, names=("ignore", "extend_ignore")
    )
    codes = {TRUTHY_CODE, OR_FALLBACK_CODE}
    if len(selected) > 0:
        codes = {
            code
            for code in codes
            if any(code.startswith(prefix) for prefix in selected)
        }
    if len(ignored) > 0:
        codes = {
            code
            for code in codes
            if not any(code.startswith(prefix) for prefix in ignored)
        }
    return frozenset(codes)


def _expand_python_paths(
    paths: Sequence[str], options: mypy.options.Options
) -> list[str]:
    """Expand Flake8 paths while respecting Mypy's configured exclusions."""
    exclude_patterns = [re.compile(pattern) for pattern in options.exclude]
    expanded: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        candidates = path.rglob("*.py") if path.is_dir() else (path,)
        for candidate in candidates:
            candidate_text = str(candidate)
            if candidate.suffix == ".py" and not any(
                pattern.search(candidate_text) is not None
                for pattern in exclude_patterns
            ):
                expanded.append(candidate_text)
    return expanded


def _read_pyproject_options(config_path: Path) -> mypy.options.Options:
    """Translate the project's supported Mypy configuration into Options."""
    options = mypy.options.Options()
    if not config_path.exists():
        return options
    config = tomllib.loads(config_path.read_text())
    tool = config.get("tool")
    if not isinstance(tool, dict):
        return options
    mypy_config = tool.get("mypy")
    if not isinstance(mypy_config, dict):
        return options
    python_version = mypy_config.get("python_version")
    if isinstance(python_version, str):
        major, minor = python_version.split(".", maxsplit=1)
        options.python_version = (int(major), int(minor))
    exclude = mypy_config.get("exclude")
    if isinstance(exclude, list):
        options.exclude = [item for item in exclude if isinstance(item, str)]
    if mypy_config.get("strict") is True:
        _set_strict_options(options)
    return options


def _set_strict_options(options: mypy.options.Options) -> None:
    """Apply strict flags required by the project's semantic lint boundary."""
    options.check_untyped_defs = True
    options.disallow_any_generics = True
    options.disallow_incomplete_defs = True
    options.disallow_subclassing_any = True
    options.disallow_untyped_calls = True
    options.disallow_untyped_decorators = True
    options.disallow_untyped_defs = True
    options.implicit_optional = False
    options.strict_equality = True
    options.warn_redundant_casts = True
    options.warn_return_any = True
    options.warn_unused_ignores = True
