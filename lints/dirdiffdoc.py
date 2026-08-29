"""Report structural violations of dirdiff's Python documentation policy.

## Public interface

Flake8 loads `DirdiffDocPlugin`. It checks module and package docs, declared
callables and types, class-field docs, module runtime values, exact callable
parameter bullets, return contracts, callable section order, and one banned
vague compound.

## Purpose and boundaries

DDD catches omissions that syntax can prove. It does not judge whether prose is
true or useful, infer fields assigned inside methods, or enforce field placement
outside class bodies. Human review still decides whether documentation explains
the real caller contract and belongs at the right level.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from itertools import pairwise
from pathlib import Path
from typing import override

MISSING_TYPE_CODE = "DDD001"
"""Diagnostic emitted when a declared class or type alias lacks documentation.

The visitor reports it at the declaration so the missing contract has an exact
source location.
"""
MISSING_FIELD_CODE = "DDD002"
"""Diagnostic for a directly annotated class field without adjacent docs.

Fields created only inside methods are outside this syntax-level rule.
"""
TYPE_FIELD_BULLETS_CODE = "DDD003"
"""Diagnostic for field bullet lists misplaced in a type-level docstring.

The rule directs individual field contracts back beside their declarations.
"""
VAGUE_COMPOUND_CODE = "DDD004"
"""Diagnostic for unquoted uses of the guide's forbidden "bearing" wording.

Only this lint's own documentation may quote the exact term it rejects.
"""
MISSING_MODULE_CODE = "DDD005"
"""Diagnostic emitted when a module or package has no real docstring.

The plugin chooses the package or module guide from the source filename.
"""
MISSING_CALLABLE_CODE = "DDD006"
"""Diagnostic emitted for a declared function or method without documentation.

Synchronous and asynchronous declarations follow the same rule.
"""
TYPE_FIELD_BLOB_CODE = "DDD007"
"""Diagnostic for type prose that describes three or more declared fields.

Backticked field-name references form the syntax-level approximation.
"""
CALLABLE_PARAMETER_CODE = "DDD008"
"""Diagnostic for incomplete or inaccurate multi-parameter documentation.

It reports missing, unknown, and duplicate bullets against the declaration.
"""
MISSING_GLOBAL_CODE = "DDD009"
"""Diagnostic for a module runtime assignment without an adjacent contract.

Export metadata in `__all__` is the sole assignment exempted by name.
"""
OPTIONAL_RETURN_CODE = "DDD010"
"""Diagnostic for an optional return without distinct present and absent docs.

The rule is separate from general structured returns because absence needs a
caller-facing meaning as well as a description of the present value.
"""
STRUCTURED_RETURN_CODE = "DDD011"
"""Diagnostic for a structured return without a divided shape explanation.

Atomic values and lists of atomic values are self-describing at the syntax
level. Tuples, mappings, unions, and other parameterized results need at least
two bullets so each caller-visible part is documented separately.
"""
CALLABLE_SECTION_ORDER_CODE = "DDD012"
"""Diagnostic for callable contract sections in a misleading order.

Parameters lead all sections. Usage precedes both returns and failures, and
returns precede failures, so callers encounter obligations before results and
failure modes.
"""
TYPE_DOC_GUIDE = "docs/how_to_docs.md#type-docstrings"
"""Guide target appended to diagnostics about types and their fields.

Keeping the fragment here gives every type rule one authoritative destination.
"""
LANGUAGE_DOC_GUIDE = "docs/how_to_docs.md#language"
"""Guide target appended to documentation-language diagnostics.

It currently explains the concrete project vocabulary expected by DDD004.
"""
PACKAGE_DOC_GUIDE = "docs/how_to_docs.md#package-docstrings"
"""Guide target used when an `__init__.py` facade lacks documentation.

Package diagnostics use it instead of the ordinary module section.
"""
MODULE_DOC_GUIDE = "docs/how_to_docs.md#module-docstrings"
"""Guide target used when an ordinary Python module lacks documentation.

The source filename decides between this value and `PACKAGE_DOC_GUIDE`.
"""
CALLABLE_DOC_GUIDE = "docs/how_to_docs.md#function-and-method-docstrings"
"""Guide target appended to callable and parameter diagnostics.

Both missing docstrings and malformed parameter sections point here.
"""
GLOBAL_DOC_GUIDE = "docs/how_to_docs.md#global-value-documentation"
"""Guide target appended when a shared module value lacks its contract.

DDD009 messages use it for annotated and unannotated assignments alike.
"""
_MARKDOWN_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
"""Recognize Markdown list items in type prose, including numbered variants.

Only line starts matter because the rule asks whether a type doc contains field
documentation shaped as a list. Any list in a class with direct fields matches
this syntax-level approximation.
"""
_VAGUE_COMPOUND = re.compile(r"\bbearings?\b", re.IGNORECASE)
"""Find singular or plural "bearing" as a complete, case-insensitive word.

Word boundaries avoid rejecting longer unrelated identifiers.
"""
_QUOTED_BEARING = re.compile(r"(['\"])bearing\1")
"""Find the exact quoted lint term allowed only in this module's own docs.

The same quote character must surround the word before it is removed from this
module's language check.
"""
_BACKTICKED_IDENTIFIER = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*)`")
"""Extract Python-shaped names that type prose presents as identifiers.

The resulting names are intersected with direct annotated fields to detect a
field inventory in type-level prose.
"""
_PARAMETER_BULLET = re.compile(
    r"^\s*-\s+`(?P<stars>\*{0,2})(?P<name>[A-Za-z_][A-Za-z0-9_]*)`\s*:"
)
"""Parse exact parameter names from bullets under a `# Parameters` section.

Optional stars accept documentation that spells variadic parameters as `*args`
or `**kwargs`; comparison uses the declaration's name without those markers.
"""
_RETURN_BULLET = re.compile(r"^\s*-\s+(?P<text>\S.*)$")
"""Parse one required Markdown bullet inside a `# Returns` section.

Return contracts use the same visible structure in Python and TypeScript. A
bare paragraph and a one-item list do not satisfy the policy even when they name
the return type.
"""

__all__ = ["DirdiffDocPlugin"]


class DirdiffDocPlugin:
    """Expose dirdiff documentation diagnostics through Flake8.

    One instance retains one parsed module and source filename. `run` validates
    the module contract and delegates declaration checks to a fresh visitor, so
    instances share no diagnostics or mutable state.

    # Usage

    Register the class as a Flake8 AST plugin. Flake8 constructs one instance per
    source module and consumes the tuples yielded by `run`.
    """

    name = "dirdiffdoc"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Retain the parsed module and its source path supplied by Flake8.

        # Parameters

        - `tree`: Parsed Python module supplied by Flake8.
        - `filename`: Source path used to distinguish packages and this lint.
        """
        assert isinstance(tree, ast.Module)
        self.tree = tree
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[DirdiffDocPlugin]]]:
        """Yield source-ordered DDD diagnostics for one parsed module.

        The module or package contract is checked first. Declaration diagnostics
        are then collected, sorted by source coordinate and code, and linked to
        the guide section for that rule.

        # Usage

        Flake8 consumes each tuple as line, zero-based column, complete message,
        and plugin class.

        # Returns

        - Diagnostics are yielded in source order after the module diagnostic.
        - Each tuple contains the source line, zero-based column, complete
          message, and this plugin class.

        # Failures

        This syntax-only pass has no expected domain failure. The constructor
        asserts that Flake8 supplied an `ast.Module`.
        """
        module_docstring = ast.get_docstring(self.tree, clean=False)
        if module_docstring is None or module_docstring.strip() == "":
            is_package = Path(self.filename).name == "__init__.py"
            guide = PACKAGE_DOC_GUIDE if is_package else MODULE_DOC_GUIDE
            subject = "package" if is_package else "module"
            yield (
                1,
                0,
                f"{MISSING_MODULE_CODE} {subject} needs a docstring; see {guide}",
                type(self),
            )

        visitor = _DirdiffDocVisitor(
            self.tree,
            allow_quoted_compound=(
                Path(self.filename).resolve() == Path(__file__).resolve()
            ),
        )
        visitor.visit(self.tree)
        for node, code, message in sorted(
            visitor.diagnostics,
            key=lambda diagnostic: (
                diagnostic[0].lineno,
                diagnostic[0].col_offset,
                diagnostic[1],
            ),
        ):
            if code == VAGUE_COMPOUND_CODE:
                guide = LANGUAGE_DOC_GUIDE
            elif code in {
                MISSING_CALLABLE_CODE,
                CALLABLE_PARAMETER_CODE,
                OPTIONAL_RETURN_CODE,
                STRUCTURED_RETURN_CODE,
                CALLABLE_SECTION_ORDER_CODE,
            }:
                guide = CALLABLE_DOC_GUIDE
            elif code == MISSING_GLOBAL_CODE:
                guide = GLOBAL_DOC_GUIDE
            else:
                guide = TYPE_DOC_GUIDE
            yield (
                node.lineno,
                node.col_offset,
                f"{code} {message}; see {guide}",
                type(self),
            )


class _DirdiffDocVisitor(ast.NodeVisitor):
    """Collect documentation violations for one parsed module traversal.

    Construction indexes adjacent standalone strings and direct module statement
    identity. The visitor appends source nodes with rule-specific messages; it
    does not format Flake8 tuples or read source files.
    """

    def __init__(
        self,
        tree: ast.Module,
        *,
        allow_quoted_compound: bool,
    ) -> None:
        """Index docs and retain whether this lint may name its banned term.

        # Parameters

        - `tree`: Parsed module whose adjacent docstrings are indexed.
        - `allow_quoted_compound`: Whether quoted lint terminology is allowed.
        """
        self._allow_quoted_compound = allow_quoted_compound
        self._module_statement_ids = {id(statement) for statement in tree.body}
        self._adjacent_docstrings: dict[int, str] = {}
        for node in ast.walk(tree):
            for _, value in ast.iter_fields(node):
                if not (
                    isinstance(value, list)
                    and all(isinstance(item, ast.stmt) for item in value)
                ):
                    continue
                for statement, following in pairwise(value):
                    if (
                        isinstance(statement, ast.stmt)
                        and isinstance(following, ast.Expr)
                        and isinstance(following.value, ast.Constant)
                        and isinstance(following.value.value, str)
                        and following.value.value.strip() != ""
                    ):
                        self._adjacent_docstrings[id(statement)] = (
                            following.value.value
                        )
        self.diagnostics: list[tuple[ast.stmt, str, str]] = []

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check a class contract and the placement of direct field docs.

        Missing type and field docs, type-level field lists, and field-name blobs
        produce separate diagnostics. Nested declarations are visited afterward.
        """
        docstring = ast.get_docstring(node, clean=False)
        if docstring is None or docstring.strip() == "":
            self.diagnostics.append(
                (
                    node,
                    MISSING_TYPE_CODE,
                    f"type {node.name!r} needs a docstring",
                )
            )

        fields = [
            statement
            for statement in node.body
            if isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
        ]
        for field in fields:
            if id(field) not in self._adjacent_docstrings:
                assert isinstance(field.target, ast.Name)
                self.diagnostics.append(
                    (
                        field,
                        MISSING_FIELD_CODE,
                        f"field {node.name}.{field.target.id} needs an adjacent "
                        "docstring",
                    )
                )

        if (
            len(fields) > 0
            and docstring is not None
            and any(
                _MARKDOWN_BULLET.match(line) is not None
                for line in docstring.splitlines()
            )
        ):
            self.diagnostics.append(
                (
                    node,
                    TYPE_FIELD_BULLETS_CODE,
                    f"type {node.name!r} puts field docs in bullet points; "
                    "move those docs beside the fields",
                )
            )

        if docstring is not None:
            documented_names = set(_BACKTICKED_IDENTIFIER.findall(docstring))
            field_names = {
                field.target.id
                for field in fields
                if isinstance(field.target, ast.Name)
            }
            described_fields = documented_names & field_names
            if len(described_fields) >= 3:
                self.diagnostics.append(
                    (
                        node,
                        TYPE_FIELD_BLOB_CODE,
                        f"type {node.name!r} describes {len(described_fields)} "
                        "fields in its type docstring; move individual field "
                        "descriptions beside their declarations",
                    )
                )
        self.generic_visit(node)

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check one synchronous callable before traversing its body.

        Existing docs also pass through exact parameter-bullet validation when
        the callable has at least two caller-supplied parameters.
        """
        self._check_callable(node)
        self.generic_visit(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check one asynchronous callable under the synchronous documentation rules.

        Its nested declarations and standalone strings are traversed after the
        callable contract is checked.
        """
        self._check_callable(node)
        self.generic_visit(node)

    @override
    def visit_Expr(self, node: ast.Expr) -> None:
        """Check standalone string documentation for forbidden vague wording.

        Non-string expressions pass through untouched. This lint's own source may
        quote its exact rejected term; no other module receives that exception.
        """
        if not (
            isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            self.generic_visit(node)
            return
        documentation = node.value.value
        if self._allow_quoted_compound:
            documentation = _QUOTED_BEARING.sub("", documentation)
        if _VAGUE_COMPOUND.search(documentation) is not None:
            self.diagnostics.append(
                (
                    node,
                    VAGUE_COMPOUND_CODE,
                    'documentation uses forbidden "bearing" wording; name '
                    "the concrete relationship instead",
                )
            )
        self.generic_visit(node)

    @override
    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        """Require an adjacent contract after one explicit PEP 695 type alias.

        The diagnostic points at the alias declaration. Nested syntax remains
        available to later visitor rules.
        """
        if id(node) not in self._adjacent_docstrings:
            assert isinstance(node.name, ast.Name)
            self.diagnostics.append(
                (
                    node,
                    MISSING_TYPE_CODE,
                    f"type alias {node.name.id!r} needs an adjacent docstring",
                )
            )
        self.generic_visit(node)

    @override
    def visit_Assign(self, node: ast.Assign) -> None:
        """Classify assignment targets as legacy aliases or runtime values.

        Nested unpacking targets are flattened in source order. Only direct
        module statements can produce global-value diagnostics.
        """
        names: list[str] = []
        # Assignment targets may nest global names inside unpacking patterns.
        targets = list(node.targets)
        while targets:
            target = targets.pop()
            if isinstance(target, ast.Name):
                names.append(target.id)
            elif isinstance(target, ast.Starred):
                targets.append(target.value)
            elif isinstance(target, (ast.List, ast.Tuple)):
                targets.extend(target.elts)
        names.reverse()
        if len(names) == 1:
            name = names[0]
            if not self._check_legacy_alias(node, name):
                self._check_global(node, name)
        else:
            for name in names:
                self._check_global(node, name)
        self.generic_visit(node)

    @override
    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check one directly named annotated module assignment.

        CamelCase names follow the legacy alias rule; other names follow the
        runtime-value rule. Class fields are handled by `visit_ClassDef`.
        """
        if isinstance(node.target, ast.Name):
            name = node.target.id
            if not self._check_legacy_alias(node, name):
                self._check_global(node, name)
        self.generic_visit(node)

    def _check_legacy_alias(
        self,
        node: ast.Assign | ast.AnnAssign,
        name: str,
    ) -> bool:
        """Check a CamelCase module assignment used as a type alias.

        # Parameters

        - `node`: Module assignment that may declare a legacy type alias.
        - `name`: Assigned identifier used to recognize the alias convention.

        Returns whether the assignment follows the legacy type-alias naming
        convention, including documented aliases that need no diagnostic.
        """
        core_name = name.removeprefix("_")
        if (
            id(node) not in self._module_statement_ids
            or core_name == ""
            or not core_name[0].isupper()
            or core_name.isupper()
        ):
            return False
        if id(node) not in self._adjacent_docstrings:
            self.diagnostics.append(
                (
                    node,
                    MISSING_TYPE_CODE,
                    f"type alias {name!r} needs an adjacent docstring",
                )
            )
        return True

    def _check_global(
        self,
        node: ast.Assign | ast.AnnAssign,
        name: str,
    ) -> None:
        """Require an adjacent docstring on one module-level runtime value.

        # Parameters

        - `node`: Assignment that may bind a module-level runtime value.
        - `name`: Direct name bound by the assignment.

        `__all__` is standardized export metadata rather than an application
        value. Imports and assignments nested beneath module statements remain
        outside this syntax check.
        """
        if (
            id(node) not in self._module_statement_ids
            or name == "__all__"
            or id(node) in self._adjacent_docstrings
        ):
            return
        self.diagnostics.append(
            (
                node,
                MISSING_GLOBAL_CODE,
                f"global value {name!r} needs an adjacent docstring",
            )
        )

    def _check_callable(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """Report a missing callable contract or validate its exact sections.

        Blank docstrings count as absent. A nonblank contract avoids DDD006 but
        may still produce parameter or return diagnostics for its signature.
        """
        docstring = ast.get_docstring(node, clean=False)
        if docstring is not None and docstring.strip() != "":
            headings: list[tuple[int, str]] = []
            fence_marker: str | None = None
            for line_number, line in enumerate(docstring.splitlines()):
                stripped = line.strip()
                fence_match = re.match(r"^(`{3,}|~{3,})", stripped)
                if fence_marker is None and fence_match is not None:
                    fence_marker = fence_match.group(1)
                    continue
                if fence_marker is not None:
                    if re.fullmatch(
                        rf"{re.escape(fence_marker[0])}"
                        rf"{{{len(fence_marker)},}}\s*",
                        stripped,
                    ):
                        fence_marker = None
                    continue
                if stripped.startswith("# "):
                    headings.append((line_number, stripped))

            parameter_positions = [
                line_number
                for line_number, heading in headings
                if heading == "# Parameters"
            ]
            usage_positions = [
                line_number
                for line_number, heading in headings
                if heading.startswith("# Usage")
            ]
            return_positions = [
                line_number
                for line_number, heading in headings
                if heading == "# Returns"
            ]
            failure_positions = [
                line_number
                for line_number, heading in headings
                if heading == "# Failures"
            ]
            non_parameter_positions = [
                line_number
                for line_number, heading in headings
                if heading != "# Parameters"
            ]
            parameters_are_first = (
                not parameter_positions
                or not non_parameter_positions
                or max(parameter_positions) < min(non_parameter_positions)
            )
            usage_precedes_returns = (
                not usage_positions
                or not return_positions
                or max(usage_positions) < min(return_positions)
            )
            usage_precedes_failures = (
                not usage_positions
                or not failure_positions
                or max(usage_positions) < min(failure_positions)
            )
            returns_precede_failures = (
                not return_positions
                or not failure_positions
                or max(return_positions) < min(failure_positions)
            )
            if not (
                parameters_are_first
                and usage_precedes_returns
                and usage_precedes_failures
                and returns_precede_failures
            ):
                self.diagnostics.append(
                    (
                        node,
                        CALLABLE_SECTION_ORDER_CODE,
                        f"callable {node.name!r} has sections out of order; "
                        "put `# Parameters` first, then `# Usage`, "
                        "`# Returns`, and `# Failures` when present",
                    )
                )
            self._check_callable_parameters(node, docstring)
            annotation = node.returns
            if annotation is None:
                return

            def qualified_name(expression: ast.expr) -> str | None:
                """Return one dotted annotation name, or no simple name.

                # Returns

                - A dotted identifier for a Name or Attribute expression.
                - `None`: The annotation is another syntax shape.
                """

                if isinstance(expression, ast.Name):
                    return expression.id
                if isinstance(expression, ast.Attribute):
                    parent = qualified_name(expression.value)
                    return (
                        expression.attr
                        if parent is None
                        else f"{parent}.{expression.attr}"
                    )
                return None

            def unwrapped(expression: ast.expr) -> ast.expr:
                """Remove transparent `Annotated` wrappers from a return type."""

                while isinstance(expression, ast.Subscript) and qualified_name(
                    expression.value
                ) in {"Annotated", "typing.Annotated"}:
                    annotation_parts = expression.slice
                    expression = (
                        annotation_parts.elts[0]
                        if isinstance(annotation_parts, ast.Tuple)
                        else annotation_parts
                    )
                return expression

            def is_none_type(expression: ast.expr) -> bool:
                """Return whether one annotation arm denotes `None`."""

                expression = unwrapped(expression)
                return (
                    isinstance(expression, ast.Constant)
                    and expression.value is None
                ) or qualified_name(expression) in {
                    "None",
                    "NoneType",
                    "types.NoneType",
                }

            def union_arms(expression: ast.expr) -> list[ast.expr]:
                """Flatten one union annotation while preserving its arm order."""

                expression = unwrapped(expression)
                if isinstance(expression, ast.BinOp) and isinstance(
                    expression.op, ast.BitOr
                ):
                    return [
                        *union_arms(expression.left),
                        *union_arms(expression.right),
                    ]
                if isinstance(expression, ast.Subscript) and qualified_name(
                    expression.value
                ) in {"Union", "typing.Union"}:
                    union_parts = expression.slice
                    return (
                        list(union_parts.elts)
                        if isinstance(union_parts, ast.Tuple)
                        else [union_parts]
                    )
                return [expression]

            def is_optional_return(expression: ast.expr) -> bool:
                """Return whether the whole callable result may be `None`."""

                expression = unwrapped(expression)
                if isinstance(expression, ast.Subscript) and qualified_name(
                    expression.value
                ) in {"Optional", "typing.Optional"}:
                    return True
                arms = union_arms(expression)
                return len(arms) > 1 and any(is_none_type(arm) for arm in arms)

            def is_atomic_return(expression: ast.expr) -> bool:
                """Return whether syntax makes the result shape self-evident."""

                expression = unwrapped(expression)
                if is_none_type(expression):
                    return True
                if isinstance(expression, (ast.Name, ast.Attribute)):
                    return qualified_name(expression) not in {
                        "dict",
                        "frozenset",
                        "list",
                        "set",
                        "tuple",
                    }
                if isinstance(expression, ast.Constant):
                    return True
                if not isinstance(expression, ast.Subscript):
                    return False
                type_name = qualified_name(expression.value)
                if type_name in {
                    "Literal",
                    "TypeGuard",
                    "TypeIs",
                    "typing.Literal",
                    "typing.TypeGuard",
                    "typing.TypeIs",
                }:
                    return True
                if type_name not in {"list", "typing.List"}:
                    return False
                item_type = expression.slice
                return (
                    len(union_arms(item_type)) == 1
                    and not is_optional_return(item_type)
                    and is_atomic_return(item_type)
                )

            return_bullets: list[str] = []
            in_returns = False
            for line in docstring.splitlines():
                if line.strip() == "# Returns":
                    in_returns = True
                    continue
                if in_returns and line.strip().startswith("# "):
                    break
                if not in_returns:
                    continue
                match = _RETURN_BULLET.match(line)
                if match is not None:
                    return_bullets.append(match.group("text"))

            if is_optional_return(annotation):
                none_explained = False
                present_explained = False
                for bullet in return_bullets:
                    normalized = bullet.replace("`", "").strip()
                    match = re.match(r"^None\b", normalized)
                    if match is None:
                        present_explained = True
                        continue
                    remainder = normalized[match.end() :].strip(" :.-")
                    if remainder != "":
                        none_explained = True
                if (
                    len(return_bullets) < 2
                    or not present_explained
                    or not none_explained
                ):
                    self.diagnostics.append(
                        (
                            node,
                            OPTIONAL_RETURN_CODE,
                            f"callable {node.name!r} returns an optional value; "
                            "add separate `# Returns` bullets for the present "
                            "value and exactly what `None` means",
                        )
                    )
                return
            if not is_atomic_return(annotation) and len(return_bullets) < 2:
                self.diagnostics.append(
                    (
                        node,
                        STRUCTURED_RETURN_CODE,
                        f"callable {node.name!r} returns structured type "
                        f"{ast.unparse(annotation)!r}; add at least two "
                        "`# Returns` bullets dividing that shape into its "
                        "caller-visible parts",
                    )
                )
            return
        self.diagnostics.append(
            (
                node,
                MISSING_CALLABLE_CODE,
                f"callable {node.name!r} needs a docstring",
            )
        )

    def _check_callable_parameters(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        docstring: str,
    ) -> None:
        """Require exact Markdown bullets for multi-parameter callables.

        # Parameters

        - `node`: Function declaration supplying the authoritative parameters.
        - `docstring`: Callable documentation searched for parameter bullets.
        """
        parameters = [
            argument.arg
            for argument in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            if argument.arg not in {"self", "cls"}
        ]
        if node.args.vararg is not None:
            parameters.append(node.args.vararg.arg)
        if node.args.kwarg is not None:
            parameters.append(node.args.kwarg.arg)
        if len(parameters) < 2:
            return

        documented: list[str] = []
        in_parameters = False
        for line in docstring.splitlines():
            if line.strip() == "# Parameters":
                in_parameters = True
                continue
            if in_parameters and line.strip().startswith("# "):
                break
            if not in_parameters:
                continue
            match = _PARAMETER_BULLET.match(line)
            if match is not None:
                documented.append(match.group("name"))

        expected = set(parameters)
        actual = set(documented)
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        duplicates = sorted(
            name for name in actual if documented.count(name) > 1
        )
        if not missing and not unknown and not duplicates:
            return

        problems: list[str] = []
        if missing:
            problems.append(f"missing {', '.join(missing)}")
        if unknown:
            problems.append(f"unknown {', '.join(unknown)}")
        if duplicates:
            problems.append(f"duplicate {', '.join(duplicates)}")
        self.diagnostics.append(
            (
                node,
                CALLABLE_PARAMETER_CODE,
                f"callable {node.name!r} needs one `# Parameters` bullet per "
                f"parameter ({'; '.join(problems)})",
            )
        )
