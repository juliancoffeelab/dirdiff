"""Run dmypy-backed strict boolean checks through Flake8.

## Public interface

Flake8 loads `StrictBoolPlugin`. SBT001 rejects implicit truthiness, SBT002
rejects value selection through `or`, and SBT003 rejects `object` annotations
outside the narrowed parameter of a `TypeIs` predicate.

## Purpose and boundaries

The plugin uses Python syntax for exact spans and dmypy for semantic types. One
process-wide inspection serves Flake8's per-file plugin instances. It keeps no
second type cache when no SBT code is selected. Once daemon readiness succeeds,
cleanup stops the one-shot daemon before diagnostics return. A readiness-wait
exception currently occurs before that cleanup scope.
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
"""Diagnostic for a non-boolean expression used as a condition leaf.

Comparisons and expressions resolved to boolean types satisfy this rule.
"""
OR_FALLBACK_CODE = "SBT002"
"""Diagnostic for value selection through `or` outside boolean contexts.

Boolean operations remain allowed when every inspected leaf is boolean.
"""
OBJECT_ANNOTATION_CODE = "SBT003"
"""Diagnostic for `builtins.object` outside a `TypeIs` narrowed parameter.

Both direct syntax and Mypy's resolved annotation text participate in the rule.
"""
BOOL_INSTANCE = "builtins.bool"
"""Exact verbosity-two Mypy spelling accepted as a concrete boolean instance.

`_is_bool_type_text` compares union members against this qualified name.
"""
OBJECT_INSTANCE = "builtins.object"
"""Qualified Mypy spelling searched inside resolved annotation types.

This catches aliases that do not expose `object` directly in their AST syntax.
"""
SBT_CACHE_DIR = Path(".sbt") / "mypy_cache"
"""Fine-grained Mypy cache shared by the build and one-shot dmypy daemon.

Both phases must use this exact directory so the daemon can inspect expression
types from the library build. The daemon status file lives in a temporary
directory and is removed separately.
"""

__all__ = ["StrictBoolPlugin"]


class _OptionRegistrar(Protocol):
    """Provide the Flake8 option registration operation used by this plugin.

    `StrictBoolPlugin.add_options` calls the implementation during process setup,
    before per-file instances exist. The protocol describes only the required
    registration call and holds no parsed option state.
    """

    def add_option(
        self,
        name: str,
        *,
        default: str,
        parse_from_config: bool,
        help: str,
    ) -> None:
        """Register one configuration-backed string option with Flake8.

        `StrictBoolPlugin.add_options` invokes this during Flake8 setup, before
        plugin instances exist. The implementation may retain the option for
        later parsing and returns no value.

        # Parameters

        - `name`: Exact command-line option spelling, including leading dashes.
        - `default`: Value used when neither CLI nor configuration supplies one.
        - `parse_from_config`: Whether Flake8 configuration may set the option.
        - `help`: User-facing explanation shown in Flake8 option help.
        """


@dataclass(frozen=True)
class Diagnostic:
    """Carry one semantic boolean violation to a per-file Flake8 instance.

    `StrictBoolVisitor` constructs records from exact AST expressions.
    `StrictBoolPlugin` partitions them by resolved source path, and
    `flake8_error` adds the rule code to the final plugin tuple.
    """

    path: str
    """Resolved source path used to route one shared result to a plugin instance.

    It must use the same normalization as `StrictBoolPlugin.run` or the
    diagnostic will not reach its file.
    """

    line: int
    """One-based start line of the violating AST expression.

    `flake8_error` forwards it unchanged to Flake8.
    """

    column: int
    """Zero-based AST byte offset of the violating expression's first token.

    This differs from the one-based column spelling sent to `dmypy inspect`.
    """

    code: str
    """Enabled SBT rule whose condition the expression violated.

    It is prepended to `message` only when Flake8 output is constructed.
    """

    message: str
    """Concrete correction guidance stored without a repeated rule code.

    `flake8_error` joins it to `code` with one space.
    """

    def flake8_error(self) -> tuple[int, int, str, type[StrictBoolPlugin]]:
        """Encode this record in Flake8's plugin iterator contract.

        The rule code is prefixed exactly once. Source coordinates pass through
        unchanged, and the plugin class occupies the final tuple field.

        # Usage

        `StrictBoolPlugin.run` calls this for each diagnostic assigned to its
        source file and yields the result directly to Flake8.

        # Returns

        - `First`: Source line of the violating expression.
        - `Second`: Zero-based source column.
        - `Third`: Rule-prefixed diagnostic message.
        - `Fourth`: The strict-bool plugin class required by Flake8.

        # Failures

        This conversion performs no I/O and has no expected failure.
        """
        return (
            self.line,
            self.column,
            f"{self.code} {self.message}",
            StrictBoolPlugin,
        )


class StrictBoolPlugin:
    """Coordinate one semantic boolean inspection across Flake8 file instances.

    Flake8 creates an instance per source file, but the first enabled instance
    builds the Mypy cache, starts dmypy, and partitions all results. Later
    instances only yield their path's retained diagnostics.

    # Usage

    Register the class as a Flake8 AST plugin and option provider. Flake8 calls
    `add_options` and `parse_options` once, then constructs an instance for each
    source file and iterates `run`.
    """

    name = "dirdiff-strict-bool"
    version = "0.2.0"
    _enabled_codes = frozenset(
        {TRUTHY_CODE, OR_FALLBACK_CODE, OBJECT_ANNOTATION_CODE}
    )
    _flake8_paths: Sequence[str] = ()
    """Complete invocation paths captured before per-file plugin instances run.

    The first instance passes the collection to the one shared Mypy inspection.
    An empty value makes that instance inspect its own file only.
    """

    _mypy_config = Path("pyproject.toml")
    _diagnostics_by_path: ClassVar[dict[str, list[Diagnostic]]] = {}
    """One process-wide dmypy result partitioned for Flake8's file instances.

    The first `run` replaces the complete mapping before `_has_checked` becomes
    true. Later instances read their path's list and never mutate its members.
    """
    _has_checked = False

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Retain the file whose project-scoped diagnostics Flake8 requests.

        # Parameters

        - `tree`: Parsed per-file AST, unused because the shared run reparses all paths.
        - `filename`: Source path used to select this instance's shared diagnostics.

        Constructing an instance does not start Mypy or inspect source.
        """
        self.filename = filename

    @classmethod
    def add_options(cls, parser: _OptionRegistrar) -> None:
        """Register the Mypy configuration path used for the one-shot build.

        Flake8 calls this once during option setup. Configuration files may set
        the same string option because `parse_from_config` is enabled.

        # Usage

        Flake8 supplies its option manager; application code does not call this
        hook directly.

        # Failures

        Registration errors from the supplied parser propagate.
        """
        parser.add_option(
            "--sbt-mypy-config",
            default="pyproject.toml",
            parse_from_config=True,
            help="Path to pyproject.toml containing [tool.mypy].",
        )

    @classmethod
    def parse_options(cls, options: Namespace) -> None:
        """Capture invocation-wide inputs before per-file plugin instances run.

        Non-sequence filename values become an empty path set. Rule selection is
        reduced to enabled SBT codes, and the configured Mypy path is retained.

        # Usage

        Flake8 calls this after option parsing and before constructing per-file
        plugin instances.

        # Failures

        `sbt_mypy_config` must be present on the namespace; a missing attribute
        raises `AttributeError` instead of inventing a configuration path.
        """
        filenames = getattr(options, "filenames", ())
        if isinstance(filenames, list | tuple):
            cls._flake8_paths = tuple(str(filename) for filename in filenames)
        else:
            cls._flake8_paths = ()
        cls._enabled_codes = _enabled_codes_from_options(options=options)
        cls._mypy_config = Path(str(options.sbt_mypy_config))

    def run(self) -> Iterator[tuple[int, int, str, type[StrictBoolPlugin]]]:
        """Yield this file's share of one process-wide semantic inspection.

        The first instance collects and partitions all diagnostics. Later
        instances read that stable mapping; selecting no SBT codes performs no
        Mypy or daemon work.

        # Usage

        Flake8 consumes only the diagnostics whose resolved path matches this
        instance's filename.

        # Returns

        - `First`: Source line of the violating expression.
        - `Second`: Zero-based source column.
        - `Third`: Rule-prefixed diagnostic message.
        - `Fourth`: The strict-bool plugin class required by Flake8.
        - `Order and absence`: Tuples follow expression order. An empty iterator
          means no SBT code was enabled or inspection found no violation.

        # Failures

        Mypy build and daemon check failures produce no diagnostics. Unexpected
        filesystem, configuration, daemon-readiness, parsing, or inspection
        failures propagate; readiness failure can leave the daemon running.
        """
        plugin_type = type(self)
        if len(plugin_type._enabled_codes) == 0:
            return

        path = str(Path(self.filename).resolve())
        if not plugin_type._has_checked:

            def _group_diagnostics(
                diagnostics: Sequence[Diagnostic],
            ) -> dict[str, list[Diagnostic]]:
                """Partition source-ordered diagnostics by their resolved path.

                Per-path order remains unchanged. Missing paths are added when
                their first diagnostic appears.

                # Returns

                - `Keys`: Resolved source paths in first-appearance order.
                - `Values`: Each path's diagnostics in their input order; paths
                  without a diagnostic are absent.
                """
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
    """Apply selected semantic boolean rules to one parsed Python file.

    The visitor combines AST context with exact-span types from a live dmypy
    daemon. It retains per-span type results and source-ordered diagnostics for
    one traversal only; daemon startup and disposal belong to the caller.
    """

    def __init__(
        self,
        tree: ast.Module,
        path: str,
        status_file: str,
        enabled_codes: frozenset[str],
    ) -> None:
        """Bind one file traversal to a checked daemon and selected rules.

        # Parameters

        - `tree`: Parsed source module also scanned for relevant import aliases.
        - `path`: Absolute source path attached to every emitted diagnostic.
        - `status_file`: Live dmypy status file used for expression inspection.
        - `enabled_codes`: SBT rules this traversal is allowed to emit.

        The visitor caches inspected types for its lifetime only. The caller
        must keep the daemon running until traversal finishes.
        """
        self.path = path
        self.status_file = status_file
        self.enabled_codes = enabled_codes
        self.diagnostics: list[Diagnostic] = []
        self._boolean_context = 0
        self._or_parent = 0
        self._condition_expr_ids: set[int] = set()
        self._type_by_span: dict[str, str | None] = {}
        self._type_is_names: set[str] = set()
        self._typing_modules: set[str] = set()
        self._object_names = {"object"}
        self._in_direct_class_body = 0
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                if statement.module in {"typing", "typing_extensions"}:
                    self._type_is_names.update(
                        alias.asname or alias.name
                        for alias in statement.names
                        if alias.name == "TypeIs"
                    )
                elif statement.module == "builtins":
                    self._object_names.update(
                        alias.asname or alias.name
                        for alias in statement.names
                        if alias.name == "object"
                    )
            elif isinstance(statement, ast.Import):
                self._typing_modules.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name in {"typing", "typing_extensions"}
                )

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check a synchronous signature, then visit its ordinary body scope.

        The direct-class marker is cleared while body statements run so nested
        functions do not inherit method-parameter treatment.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for every synchronous function.
        This callback checks the signature and type parameters, then explicitly
        dispatches each body statement under ordinary function scope.

        # Failures

        A dmypy client exception during annotation inspection propagates and
        aborts the traversal; an unsuccessful inspection response merely makes
        semantic type information unavailable.
        """
        self._check_function_annotations(node)
        self._check_type_parameters(node.type_params)
        direct_class_body = self._in_direct_class_body
        self._in_direct_class_body = 0
        for statement in node.body:
            self.visit(statement)
        self._in_direct_class_body = direct_class_body

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check an async signature under the synchronous annotation contract.

        Its body likewise runs without inheriting a surrounding direct-class
        marker.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for every async function. The
        callback applies the synchronous signature contract, then explicitly
        visits body statements with the direct-class marker cleared.

        # Failures

        A dmypy client exception during annotation inspection propagates and
        aborts the traversal; an unsuccessful inspection response yields no
        semantic type for that annotation.
        """
        self._check_function_annotations(node)
        self._check_type_parameters(node.type_params)
        direct_class_body = self._in_direct_class_body
        self._in_direct_class_body = 0
        for statement in node.body:
            self.visit(statement)
        self._in_direct_class_body = direct_class_body

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check class type parameters and mark only its direct body declarations.

        Decorators, bases, and keyword expressions are visited first. The marker
        is active only while walking this class body's statements.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for each class declaration. This
        callback checks class type parameters, visits class-construction inputs,
        then marks the direct body so method `self` and `cls` are treated only
        at that level.

        # Failures

        Exceptions from nested callback dispatch or dmypy inspection propagate
        and abort this traversal.
        """
        self._check_type_parameters(node.type_params)
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        self._in_direct_class_body += 1
        for statement in node.body:
            self.visit(statement)
        self._in_direct_class_body -= 1

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check an annotated assignment without revisiting its annotation syntax.

        The value expression is still traversed when present so nested boolean
        expressions receive their own rules.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for annotated assignments in any
        visited scope. The callback checks the annotation once and dispatches
        only the optional value expression afterward.

        # Failures

        A dmypy client exception while resolving the annotation propagates. An
        unsuccessful inspection response does not by itself emit SBT003.
        """
        self._check_object_annotation(node.annotation)
        if node.value is not None:
            self.visit(node.value)

    @override
    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        """Check a PEP 695 alias body and every declared type parameter.

        Bounds and defaults are checked separately before the aliased annotation
        itself.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for PEP 695 aliases. The callback
        checks each type-parameter bound and default before the alias value.

        # Failures

        A dmypy client exception during semantic annotation inspection
        propagates and aborts traversal of the alias.
        """
        self._check_type_parameters(node.type_params)
        self._check_object_annotation(node.value)

    def _check_function_annotations(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> None:
        """Check one signature while exempting only a `TypeIs` narrowed input.

        For methods, the first positional slot after `self` or `cls` is the
        candidate. Defaults and decorators are visited after all annotations.
        """
        positional = (*node.args.posonlyargs, *node.args.args)
        narrowed_index = 0
        if (
            self._in_direct_class_body > 0
            and len(positional) > 0
            and positional[0].arg in {"self", "cls"}
        ):
            narrowed_index = 1
        narrowed_argument = (
            positional[narrowed_index]
            if len(positional) > narrowed_index
            else None
        )
        is_type_is = _is_type_is_annotation(
            node.returns,
            type_is_names=self._type_is_names,
            typing_modules=self._typing_modules,
        )
        for argument in (
            *positional,
            *node.args.kwonlyargs,
            node.args.vararg,
            node.args.kwarg,
        ):
            if argument is None or argument.annotation is None:
                continue
            if (
                is_type_is
                and argument is narrowed_argument
                and (
                    _is_object_name(
                        argument.annotation, object_names=self._object_names
                    )
                    or self._annotation_type(argument.annotation)
                    == OBJECT_INSTANCE
                )
            ):
                continue
            self._check_object_annotation(argument.annotation)
        if node.returns is not None:
            self._check_object_annotation(node.returns)
        for default in (*node.args.defaults, *node.args.kw_defaults):
            if default is not None:
                self.visit(default)
        for decorator in node.decorator_list:
            self.visit(decorator)

    def _check_type_parameters(self, parameters: list[ast.type_param]) -> None:
        """Check every PEP 695 bound and default annotation for `object`.

        Unbounded parameters and parameters without defaults contribute no
        annotation syntax to this rule.
        """
        for parameter in parameters:
            if (
                isinstance(parameter, ast.TypeVar)
                and parameter.bound is not None
            ):
                self._check_object_annotation(parameter.bound)
            if (
                isinstance(
                    parameter, ast.TypeVar | ast.ParamSpec | ast.TypeVarTuple
                )
                and parameter.default_value is not None
            ):
                self._check_object_annotation(parameter.default_value)

    def _check_object_annotation(self, annotation: ast.expr) -> None:
        """Report `object` found directly or through semantic type resolution.

        Disabled SBT003 avoids dmypy inspection entirely. A matching annotation
        produces one diagnostic at the complete annotation expression.
        """
        if OBJECT_ANNOTATION_CODE not in self.enabled_codes:
            return
        type_text = self._annotation_type(annotation)
        has_object_name = any(
            _is_object_name(child, object_names=self._object_names)
            for child in ast.walk(annotation)
        )
        if has_object_name or (
            type_text is not None and OBJECT_INSTANCE in type_text
        ):
            self._add(
                annotation,
                OBJECT_ANNOTATION_CODE,
                "builtins.object is forbidden outside a TypeIs parameter",
            )

    def _annotation_type(self, annotation: ast.expr) -> str | None:
        """Inspect an annotation through the shared exact-span type cache.

        This wrapper keeps annotation call sites distinct from ordinary boolean
        expression checks while using the same dmypy operation.

        # Returns

        - `str`: Mypy's fully qualified type text for the exact annotation span.
        - `None`: dmypy inspection failed or returned non-text output. The
          caller must rely only on its separate syntactic checks.
        """
        return self._expression_type(annotation)

    @override
    def visit_If(self, node: ast.If) -> None:
        """Check an `if` test as boolean context before ordinary AST traversal.

        Marking the test first prevents its `or` nodes from being reported again
        as value selection during `generic_visit`.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for each `if`. The callback checks
        the test under boolean context, then uses `generic_visit` for the test,
        body, and `else` branch.

        # Failures

        A dmypy client exception while inspecting a condition leaf propagates;
        an unsuccessful type response makes that leaf non-boolean.
        """
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_While(self, node: ast.While) -> None:
        """Check a `while` test as boolean context before ordinary traversal.

        Nested expressions are marked so later visitor dispatch does not treat
        condition-level `or` as value selection.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for each `while`. The callback
        checks the test under boolean context, then uses `generic_visit` for the
        condition, loop body, and `else` branch.

        # Failures

        A dmypy client exception while inspecting a condition leaf propagates;
        an unsuccessful type response makes that leaf non-boolean.
        """
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_Assert(self, node: ast.Assert) -> None:
        """Check an assertion test as boolean context before ordinary traversal.

        The optional assertion message remains normal expression syntax and is
        not part of the condition contract.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for each assertion. The callback
        checks only `test` as boolean context, then uses `generic_visit` so the
        optional message still receives ordinary nested-expression checks.

        # Failures

        A dmypy client exception while inspecting the test propagates; an
        unsuccessful type response makes a non-syntactic condition leaf fail
        the boolean rule.
        """
        self._check_boolean_condition(node.test)
        self.generic_visit(node)

    @override
    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        """Reject an outer value-selecting `or` outside known boolean context.

        Nested `or` nodes share the outer diagnostic. Operations whose inspected
        leaves are all boolean remain valid boolean expressions.

        # Usage

        `ast.NodeVisitor.visit` dispatches here for `and` and `or` expressions.
        Outside a condition, the callback classifies only the outermost `or`,
        then uses `generic_visit` so nested expressions receive their own rules.

        # Failures

        A dmypy client exception while classifying an operand propagates. An
        unsuccessful type response prevents that operand from qualifying as a
        boolean-only operation.
        """
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
        """Check one condition tree while marking its temporary boolean context.

        The depth counter is restored before returning, so unrelated expressions
        do not inherit the condition exemption.
        """
        self._boolean_context += 1
        self._check_bool_expr(expression)
        self._boolean_context -= 1

    def _check_bool_expr(self, expression: ast.expr) -> None:
        """Require every non-operator condition leaf to resolve as boolean.

        Boolean operators recurse into their values, `not` recurses into its
        operand, and comparisons satisfy the condition contract syntactically.
        """
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
        """Return whether an expression combines only boolean-producing syntax.

        Comparisons are boolean without daemon inspection. Other leaves must
        resolve to accepted boolean type text.
        """
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
        """Classify one exact AST span from dmypy's formatted type result.

        Missing inspection output is not boolean. Successful output must satisfy
        `_is_bool_type_text` in full.
        """
        type_text = self._expression_type(expression)
        return type_text is not None and _is_bool_type_text(type_text)

    def _expression_type(self, expression: ast.expr) -> str | None:
        """Return and cache dmypy's type for one exact AST expression span.

        The location uses one-based start columns and AST end coordinates. Each
        unique span triggers at most one daemon inspection per visitor.

        # Returns

        - `str`: Mypy's fully qualified type text for the exact expression span,
          read from or added to this visitor's cache.
        - `None`: dmypy inspection failed or returned non-text output. Boolean
          classification must treat the expression as not proven boolean.
        """
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
        return self._type_by_span[location]

    def _add(self, expression: ast.expr, code: str, message: str) -> None:
        """Record one AST expression using Flake8's zero-based column.

        # Parameters

        - `expression`: Exact syntax node whose start coordinate is reported.
        - `code`: Enabled SBT rule that the expression violates.
        - `message`: Explanation without the code prefix added at output time.
        """
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
    """Build a fine cache, inspect the project, and stop the one-shot daemon.

    # Parameters

    - `paths`: Flake8 inputs expanded to Python sources under Mypy exclusions.
    - `config_path`: Project file supplying supported Mypy options.
    - `enabled_codes`: SBT subset visitors may report.

    Empty path sets, Mypy compile failures, daemon startup failures, and failed
    daemon checks produce no diagnostics. After the server wait succeeds, a
    `finally` block stops the daemon before this function returns or propagates
    an unexpected inspection exception. A readiness-wait exception occurs
    before that cleanup scope and therefore propagates without a stop attempt.
    """
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
                    tree=tree,
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
    """Return the fully qualified formatted type for one exact expression.

    # Parameters

    - `status_file`: Live dmypy instance that has exported source expression types.
    - `location`: Exact file and byte-span spelling accepted by `dmypy inspect`.

    Failed inspection and non-text output return `None`. Surrounding quotes and
    one trailing newline are removed from successful output.

    # Returns

    - `str`: Mypy's formatted type with one trailing newline and surrounding
      output quotes removed when present.
    - `None`: dmypy reported failure or supplied no string output. The caller
      must treat this span as having no semantic type result.
    """
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
    """Match Mypy's verbosity-two rendering of accepted boolean unions.

    Every union member must be a concrete bool or a literal containing only
    boolean values. One non-boolean member rejects the complete type.
    """

    def is_bool_atom(item: str) -> bool:
        """Recognize one concrete bool or bool-only literal union member.

        Empty literal payloads and literals containing any other value are not
        boolean atoms.
        """
        if item == BOOL_INSTANCE:
            return True
        if not item.startswith("Literal[") or not item.endswith("]"):
            return False
        values = item.removeprefix("Literal[").removesuffix("]").split(", ")
        return len(values) > 0 and all(
            value in {"True", "False"} for value in values
        )

    return all(is_bool_atom(item) for item in type_text.split(" | "))


def _is_type_is_annotation(
    annotation: ast.expr | None,
    *,
    type_is_names: set[str],
    typing_modules: set[str],
) -> bool:
    """Recognize `TypeIs` only when its constructor import resolves.

    # Parameters

    - `annotation`: Return annotation syntax to inspect, if present.
    - `type_is_names`: Direct import aliases known to bind `TypeIs`.
    - `typing_modules`: Imported module aliases that may qualify `TypeIs`.

    An unrelated local constructor with the same spelling does not match.
    """
    if not isinstance(annotation, ast.Subscript):
        return False
    constructor = annotation.value
    return (
        isinstance(constructor, ast.Name) and constructor.id in type_is_names
    ) or (
        isinstance(constructor, ast.Attribute)
        and constructor.attr == "TypeIs"
        and isinstance(constructor.value, ast.Name)
        and constructor.value.id in typing_modules
    )


def _is_object_name(annotation: ast.AST, *, object_names: set[str]) -> bool:
    """Recognize a direct spelling of the built-in object annotation.

    # Parameters

    - `annotation`: Annotation node or child being checked syntactically.
    - `object_names`: Direct names accepted as built-in `object` spellings.

    The visitor supplies the built-in name and aliases imported from `builtins`.
    Explicit `builtins.object` also matches without an import alias.
    """
    return (
        isinstance(annotation, ast.Name) and annotation.id in object_names
    ) or (
        isinstance(annotation, ast.Attribute)
        and annotation.attr == "object"
        and isinstance(annotation.value, ast.Name)
        and annotation.value.id == "builtins"
    )


def _enabled_codes_from_options(options: Namespace) -> frozenset[str]:
    """Resolve the SBT subset selected by Flake8's prefix configuration.

    Selection prefixes narrow the complete rule set when present. Ignore
    prefixes then remove matches from that selected set.

    # Returns

    - `Members`: Enabled codes from the closed SBT rule set after selection
      prefixes narrow it and ignore prefixes remove matches.
    - `Empty set`: No SBT rule is enabled, so plugin instances must perform no
      Mypy build or daemon inspection.
    """

    def _option_code_prefixes(
        options: Namespace, names: Sequence[str]
    ) -> set[str]:
        """Collect string code prefixes from sequence or scalar options.

        # Parameters

        - `options`: Parsed Flake8 namespace containing selection settings.
        - `names`: Attribute names read in order from that namespace.

        Missing attributes and non-string members contribute nothing. The set
        removes duplicates before rule selection.

        # Returns

        - `Members`: Distinct string prefixes from scalar or sequence values of
          the named options.
        - `Empty set`: Every named option was missing or contributed no string
          member, so the caller applies no selection or ignore prefix.
        """
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
    codes = {TRUTHY_CODE, OR_FALLBACK_CODE, OBJECT_ANNOTATION_CODE}
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
    """Expand Flake8 paths while respecting Mypy's configured exclusions.

    # Parameters

    - `paths`: Files or directories supplied at the Flake8 invocation boundary.
    - `options`: Mypy options whose exclusion regexes filter candidates.

    Directories are traversed recursively for `.py` files. Returned spellings
    preserve input traversal order and are not deduplicated.
    """
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
    """Translate the supported project configuration into Mypy `Options`.

    Missing or structurally invalid tables leave defaults in place. This adapter
    reads Python version, exclusions, and the strict flag only.
    """
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
    """Apply the strict Mypy flags this semantic lint relies on.

    The caller supplies a fresh options object. This function mutates it in place
    and does not parse or preserve additional configuration.
    """
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
