"""Flake8 plugin for type-aware boolean lint checks."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

import mypy.build
import mypy.find_sources
import mypy.nodes
import mypy.options
import mypy.types
import mypy.version
import tomllib

TRUTHY_CODE = "SBT001"
OR_FALLBACK_CODE = "SBT002"
CACHE_DIR = Path(".sbt_cache")
CACHE_FILE = CACHE_DIR / "diagnostics.json"
CACHE_VERSION = "1"
SYNTAX_CHILD_ATTRS = (
    "actual",
    "args",
    "base",
    "body",
    "callee",
    "condition",
    "defs",
    "else_body",
    "expr",
    "exprs",
    "index",
    "indices",
    "initializer",
    "items",
    "key",
    "keys",
    "left",
    "lvalues",
    "msg",
    "ret_type",
    "right",
    "rvalue",
    "statements",
    "target",
    "targets",
    "type",
    "value",
    "values",
)


@dataclass(frozen=True)
class Diagnostic:
    path: str
    line: int
    column: int
    code: str
    message: str

    def flake8_error(self) -> tuple[int, int, str, type[StrictBoolPlugin]]:
        return (
            self.line,
            self.column,
            f"{self.code} {self.message}",
            StrictBoolPlugin,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
        }

    @classmethod
    def from_json(cls, value: object) -> Diagnostic | None:
        if not isinstance(value, dict):
            return None

        path = value.get("path")
        line = value.get("line")
        column = value.get("column")
        code = value.get("code")
        message = value.get("message")
        if not isinstance(path, str):
            return None
        if not isinstance(line, int):
            return None
        if not isinstance(column, int):
            return None
        if not isinstance(code, str):
            return None
        if not isinstance(message, str):
            return None

        return cls(
            path=path,
            line=line,
            column=column,
            code=code,
            message=message,
        )


class StrictBoolPlugin:
    """Run strict boolean checks from flake8 by invoking mypy."""

    name = "dirdiff-strict-bool"
    version = "0.1.0"
    _enabled_codes = frozenset({TRUTHY_CODE, OR_FALLBACK_CODE})
    _flake8_paths: Sequence[str] = ()
    _clean_cache = False
    _mypy_config = Path("pyproject.toml")
    _diagnostics_by_path: dict[str, list[Diagnostic]] = {}
    _has_checked = False

    def __init__(self, tree: Any, filename: str) -> None:
        self.filename = filename

    @classmethod
    def add_options(cls, parser: Any) -> None:
        parser.add_option(
            "--sbt-mypy-config",
            default="pyproject.toml",
            parse_from_config=True,
            help="Path to pyproject.toml containing [tool.mypy].",
        )
        parser.add_option(
            "--sbt-clean",
            action="store_true",
            default=False,
            parse_from_config=True,
            help="Hash every project Python file for SBT cache invalidation.",
        )

    @classmethod
    def parse_options(cls, options: Any) -> None:
        filenames = getattr(options, "filenames", ())
        if isinstance(filenames, list | tuple):
            cls._flake8_paths = tuple(str(filename) for filename in filenames)
        else:
            cls._flake8_paths = ()
        cls._clean_cache = bool(options.sbt_clean)
        cls._enabled_codes = _enabled_codes_from_options(options=options)
        cls._mypy_config = Path(str(options.sbt_mypy_config))

    def run(self) -> Iterator[tuple[int, int, str, type[StrictBoolPlugin]]]:
        plugin_type = type(self)
        path = str(Path(self.filename).resolve())
        if not plugin_type._has_checked:
            paths = self._paths_for_mypy(fallback_path=path)
            diagnostics = _collect_diagnostics(
                paths=paths,
                config_path=plugin_type._mypy_config,
                clean_cache=plugin_type._clean_cache,
                enabled_codes=plugin_type._enabled_codes,
            )
            plugin_type._diagnostics_by_path = _group_diagnostics(diagnostics)
            plugin_type._has_checked = True

        diagnostics = plugin_type._diagnostics_by_path.get(path)
        if diagnostics is None:
            return

        for diagnostic in diagnostics:
            yield diagnostic.flake8_error()

    @classmethod
    def _paths_for_mypy(cls, fallback_path: str) -> Sequence[str]:
        if len(cls._flake8_paths) > 0:
            return cls._flake8_paths
        return [fallback_path]


class StrictBoolVisitor:
    def __init__(
        self,
        path: str,
        type_map: dict[mypy.nodes.Expression, mypy.types.Type],
        enabled_codes: frozenset[str],
    ) -> None:
        self.path = path
        self.type_map = type_map
        self.enabled_codes = enabled_codes
        self.diagnostics: list[Diagnostic] = []
        self._boolean_context = 0
        self._or_parent = 0
        self._condition_expr_ids: set[int] = set()
        self._visited_node_ids: set[int] = set()

    def visit(self, node: mypy.nodes.Node) -> None:
        node_id = id(node)
        if node_id in self._visited_node_ids:
            return
        self._visited_node_ids.add(node_id)

        if isinstance(node, mypy.nodes.IfStmt):
            self._visit_if_stmt(node)
        elif isinstance(node, mypy.nodes.WhileStmt):
            self._visit_while_stmt(node)
        elif isinstance(node, mypy.nodes.AssertStmt):
            self._visit_assert_stmt(node)
        elif isinstance(node, mypy.nodes.OpExpr):
            self._visit_op_expr(node)
        else:
            self._visit_children(node)

    def _visit_if_stmt(self, stmt: mypy.nodes.IfStmt) -> None:
        for expr in stmt.expr:
            self._check_boolean_condition(expr)
        self._visit_children(stmt)

    def _visit_while_stmt(self, stmt: mypy.nodes.WhileStmt) -> None:
        self._check_boolean_condition(stmt.expr)
        self._visit_children(stmt)

    def _visit_assert_stmt(self, stmt: mypy.nodes.AssertStmt) -> None:
        self._check_boolean_condition(stmt.expr)
        self._visit_children(stmt)

    def _visit_op_expr(self, expr: mypy.nodes.OpExpr) -> None:
        is_or_operator = expr.op == "or"
        if (
            is_or_operator
            and OR_FALLBACK_CODE in self.enabled_codes
            and self._boolean_context == 0
            and self._or_parent == 0
            and id(expr) not in self._condition_expr_ids
            and not self._is_boolean_operation(expr)
        ):
            self._add(
                expr,
                OR_FALLBACK_CODE,
                "avoid value fallback with 'or'; make the fallback explicit",
            )

        if is_or_operator:
            self._or_parent += 1
        self._visit_children(expr)
        if is_or_operator:
            self._or_parent -= 1

    def _visit_children(self, node: mypy.nodes.Node) -> None:
        for attr in SYNTAX_CHILD_ATTRS:
            if not hasattr(node, attr):
                continue
            value = getattr(node, attr)
            self._visit_child_value(value)

    def _visit_child_value(self, value: object) -> None:
        if _is_syntax_node(value):
            self.visit(value)
            return

        if isinstance(value, list | tuple):
            for item in value:
                self._visit_child_value(item)

    def _check_boolean_condition(self, expr: mypy.nodes.Expression) -> None:
        self._boolean_context += 1
        self._check_bool_expr(expr)
        self._boolean_context -= 1

    def _check_bool_expr(self, expr: mypy.nodes.Expression) -> None:
        self._condition_expr_ids.add(id(expr))

        if isinstance(expr, mypy.nodes.OpExpr):
            if expr.op == "and" or expr.op == "or":
                self._check_bool_expr(expr.left)
                self._check_bool_expr(expr.right)
                return

        if isinstance(expr, mypy.nodes.UnaryExpr) and expr.op == "not":
            self._check_bool_expr(expr.expr)
            return

        if self._is_bool_type(self.type_map.get(expr)):
            return

        if TRUTHY_CODE not in self.enabled_codes:
            return

        self._add(
            expr,
            TRUTHY_CODE,
            "implicit truthiness is forbidden; use an explicit bool expression",
        )

    def _is_boolean_operation(self, expr: mypy.nodes.Expression) -> bool:
        if isinstance(expr, mypy.nodes.OpExpr):
            if expr.op == "and" or expr.op == "or":
                left_is_boolean = self._is_boolean_operation(expr.left)
                right_is_boolean = self._is_boolean_operation(expr.right)
                return left_is_boolean and right_is_boolean

        if isinstance(expr, mypy.nodes.UnaryExpr) and expr.op == "not":
            return self._is_boolean_operation(expr.expr)

        return self._is_bool_type(self.type_map.get(expr))

    def _is_bool_type(self, typ: mypy.types.Type | None) -> bool:
        if typ is None:
            return False

        proper = mypy.types.get_proper_type(typ)
        if isinstance(proper, mypy.types.TypeAliasType):
            proper = mypy.types.get_proper_type(proper.alias.target)

        return _is_bool_proper_type(proper)

    def _add(
        self,
        expr: mypy.nodes.Expression,
        code: str,
        message: str,
    ) -> None:
        self.diagnostics.append(
            Diagnostic(
                path=self.path,
                line=expr.line,
                column=expr.column,
                code=code,
                message=message,
            )
        )


def _collect_diagnostics(
    paths: Sequence[str],
    config_path: Path,
    clean_cache: bool,
    enabled_codes: frozenset[str],
) -> list[Diagnostic]:
    start_time = time.monotonic()
    print(
        f"SBT debug: start paths={list(paths)!r} config={config_path}",
        file=sys.stderr,
    )
    options = _read_pyproject_options(config_path=config_path)
    options.export_types = True
    options.incremental = False
    options.preserve_asts = True
    options.show_traceback = True

    expanded_paths = _expand_python_paths(paths=paths, options=options)
    print(
        "SBT debug: expanded "
        f"{len(paths)} paths to {len(expanded_paths)} python files "
        f"in {time.monotonic() - start_time:.3f}s",
        file=sys.stderr,
    )
    if len(expanded_paths) == 0:
        return []

    context_hash = _context_hash(
        expanded_paths=expanded_paths,
        config_path=config_path,
        clean_cache=clean_cache,
        enabled_codes=enabled_codes,
    )
    cached_diagnostics = _read_cache(context_hash=context_hash)
    if cached_diagnostics is not None:
        print(
            "SBT debug: cache hit "
            f"{len(cached_diagnostics)} diagnostics "
            f"in {time.monotonic() - start_time:.3f}s",
            file=sys.stderr,
        )
        return cached_diagnostics
    print(
        f"SBT debug: cache miss context={context_hash[:12]}",
        file=sys.stderr,
    )

    try:
        source_start_time = time.monotonic()
        sources = mypy.find_sources.create_source_list(
            paths=expanded_paths,
            options=options,
            allow_empty_dir=False,
        )
    except mypy.find_sources.InvalidSourceList:
        return []
    print(
        "SBT debug: created "
        f"{len(sources)} mypy sources in "
        f"{time.monotonic() - source_start_time:.3f}s",
        file=sys.stderr,
    )

    source_paths = {
        str(Path(source.path).resolve())
        for source in sources
        if source.path is not None
    }

    try:
        build_start_time = time.monotonic()
        result = mypy.build.build(
            sources=sources,
            options=options,
            stdout=StringIO(),
            stderr=StringIO(),
        )
    except mypy.build.CompileError:
        return []
    print(
        "SBT debug: mypy build finished "
        f"in {time.monotonic() - build_start_time:.3f}s",
        file=sys.stderr,
    )

    diagnostics: list[Diagnostic] = []
    walk_start_time = time.monotonic()
    visited_modules = 0
    for state in result.graph.values():
        tree = state.tree
        if not isinstance(tree, mypy.nodes.MypyFile):
            continue

        path = tree.path
        if path is None:
            path = state.xpath
        resolved_path = str(Path(path).resolve())
        if resolved_path not in source_paths:
            continue

        visited_modules += 1
        visitor = StrictBoolVisitor(
            path=resolved_path,
            type_map=result.types,
            enabled_codes=enabled_codes,
        )
        visitor.visit(tree)
        diagnostics.extend(visitor.diagnostics)
    print(
        "SBT debug: walked "
        f"{visited_modules} modules in {time.monotonic() - walk_start_time:.3f}s",
        file=sys.stderr,
    )

    diagnostics.sort(
        key=lambda item: (item.path, item.line, item.column, item.code)
    )
    print(
        "SBT debug: finished "
        f"{len(diagnostics)} diagnostics in {time.monotonic() - start_time:.3f}s",
        file=sys.stderr,
    )
    _write_cache(
        context_hash=context_hash,
        diagnostics=diagnostics,
    )
    return diagnostics


def _group_diagnostics(
    diagnostics: Sequence[Diagnostic],
) -> dict[str, list[Diagnostic]]:
    grouped: dict[str, list[Diagnostic]] = {}
    for diagnostic in diagnostics:
        existing = grouped.get(diagnostic.path)
        if existing is None:
            grouped[diagnostic.path] = [diagnostic]
        else:
            existing.append(diagnostic)
    return grouped


def _enabled_codes_from_options(options: Any) -> frozenset[str]:
    selected = _option_code_prefixes(
        options=options,
        names=("select", "extend_select"),
    )
    ignored = _option_code_prefixes(
        options=options,
        names=("ignore", "extend_ignore"),
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


def _option_code_prefixes(
    options: Any,
    names: Sequence[str],
) -> set[str]:
    prefixes: set[str] = set()
    for name in names:
        raw_value = getattr(options, name, ())
        if isinstance(raw_value, str):
            prefixes.add(raw_value)
        elif isinstance(raw_value, list | tuple | set | frozenset):
            for item in raw_value:
                if isinstance(item, str):
                    prefixes.add(item)
    return prefixes


def _expand_python_paths(
    paths: Sequence[str],
    options: mypy.options.Options,
) -> list[str]:
    expanded: list[str] = []
    exclude_patterns = [re.compile(pattern) for pattern in options.exclude]
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            for child in path.rglob("*.py"):
                child_text = str(child)
                if _is_excluded(child_text, exclude_patterns):
                    continue
                expanded.append(child_text)
        elif path.suffix == ".py":
            path_text = str(path)
            if _is_excluded(path_text, exclude_patterns):
                continue
            expanded.append(path_text)
    return expanded


def _is_excluded(path: str, patterns: Sequence[re.Pattern[str]]) -> bool:
    return any(pattern.search(path) is not None for pattern in patterns)


def _context_hash(
    expanded_paths: Sequence[str],
    config_path: Path,
    clean_cache: bool,
    enabled_codes: frozenset[str],
) -> str:
    digest = hashlib.sha256()
    digest.update(f"cache-version:{CACHE_VERSION}\n".encode())
    digest.update(f"python:{platform.python_version()}\n".encode())
    digest.update(f"mypy:{mypy.version.__version__}\n".encode())
    digest.update(f"enabled-codes:{','.join(sorted(enabled_codes))}\n".encode())

    plugin_path = Path(__file__)
    _hash_file(
        digest=digest,
        label="plugin",
        path=plugin_path,
    )
    _hash_file(
        digest=digest,
        label="config",
        path=config_path,
    )

    if clean_cache:
        hash_paths = _project_python_paths()
    else:
        hash_paths = list(expanded_paths)

    digest.update(f"clean-cache:{clean_cache}\n".encode())
    for raw_path in sorted(hash_paths):
        path = Path(raw_path).resolve()
        _hash_file(
            digest=digest,
            label="source",
            path=path,
        )

    return digest.hexdigest()


def _project_python_paths() -> list[str]:
    return [
        str(path)
        for root in (Path("src"), Path("lints"), Path("tests"))
        if root.exists()
        for path in root.rglob("*.py")
    ]


def _hash_file(
    digest: Any,
    label: str,
    path: Path,
) -> None:
    resolved_path = path.resolve()
    digest.update(f"{label}:{resolved_path}\n".encode())
    if not resolved_path.exists():
        digest.update(b"missing\n")
        return

    digest.update(resolved_path.read_bytes())
    digest.update(b"\n")


def _read_cache(context_hash: str) -> list[Diagnostic] | None:
    try:
        raw_cache = json.loads(CACHE_FILE.read_text())
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None

    if not isinstance(raw_cache, dict):
        return None
    if raw_cache.get("context_hash") != context_hash:
        return None

    raw_diagnostics = raw_cache.get("diagnostics")
    if not isinstance(raw_diagnostics, list):
        return None

    diagnostics: list[Diagnostic] = []
    for raw_diagnostic in raw_diagnostics:
        diagnostic = Diagnostic.from_json(raw_diagnostic)
        if diagnostic is None:
            return None
        diagnostics.append(diagnostic)
    return diagnostics


def _write_cache(
    context_hash: str,
    diagnostics: Sequence[Diagnostic],
) -> None:
    CACHE_DIR.mkdir(exist_ok=True)
    gitignore_path = CACHE_DIR / ".gitignore"
    if not gitignore_path.exists():
        gitignore_path.write_text("*\n")

    payload = {
        "context_hash": context_hash,
        "diagnostics": [diagnostic.to_json() for diagnostic in diagnostics],
    }
    CACHE_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _read_pyproject_options(config_path: Path) -> mypy.options.Options:
    options = mypy.options.Options()
    if not config_path.exists():
        return options

    config = tomllib.loads(config_path.read_text())
    tool_config = config.get("tool")
    if not isinstance(tool_config, dict):
        return options

    mypy_config = tool_config.get("mypy")
    if not isinstance(mypy_config, dict):
        return options

    python_version = mypy_config.get("python_version")
    if isinstance(python_version, str):
        major, minor = python_version.split(".", maxsplit=1)
        options.python_version = (int(major), int(minor))

    exclude = mypy_config.get("exclude")
    if isinstance(exclude, list):
        options.exclude = [item for item in exclude if isinstance(item, str)]

    check_untyped_defs = mypy_config.get("check_untyped_defs")
    if isinstance(check_untyped_defs, bool):
        options.check_untyped_defs = check_untyped_defs

    warn_unused_configs = mypy_config.get("warn_unused_configs")
    if isinstance(warn_unused_configs, bool):
        options.warn_unused_configs = warn_unused_configs

    strict = mypy_config.get("strict")
    if strict is True:
        _set_strict_options(options)

    return options


def _set_strict_options(options: mypy.options.Options) -> None:
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


def _is_syntax_node(value: object) -> bool:
    return isinstance(
        value,
        mypy.nodes.MypyFile
        | mypy.nodes.Block
        | mypy.nodes.Statement
        | mypy.nodes.Expression,
    )


def _is_bool_proper_type(typ: mypy.types.ProperType) -> bool:
    if isinstance(typ, mypy.types.Instance):
        return typ.type.fullname == "builtins.bool"

    if isinstance(typ, mypy.types.LiteralType):
        return isinstance(typ.value, bool)

    if isinstance(typ, mypy.types.UnionType):
        return all(
            _is_bool_proper_type(mypy.types.get_proper_type(item))
            for item in typ.items
        )

    if isinstance(typ, mypy.types.TypeType):
        return False

    if isinstance(typ, mypy.types.AnyType):
        return False

    return False
