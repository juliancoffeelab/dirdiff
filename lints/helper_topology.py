"""Report module helpers whose references fit a narrower lexical scope.

## Public interface

Flake8 loads `HelperTopologyPlugin`. It emits HLP001 for one-use private
helpers, HLP002 when all references sit below one outer function, and HLP003
when a local `TypeIs` predicate is separated from its narrowed type.

## Purpose and boundaries

The plugin combines the AST with Python's symbol table so local shadowing does
not count as a module reference. Literal `__all__` exports, tests, `main`,
decorated functions, `TypeIs` predicates, and functions longer than ten lines
stay outside the helper-placement rule. The plugin does not edit source or
decide whether a separately documented exception is worthwhile.
"""

from __future__ import annotations

import ast
import symtable
from collections.abc import Iterator
from pathlib import Path
from typing import override

INLINE_CODE = "HLP001"
"""Diagnostic for an eligible module helper with one external reference.

Recursive self-references do not count. The message asks for inlining or local
nesting beside the sole caller.
"""
NEST_CODE = "HLP002"
"""Diagnostic for a helper whose external references share one outer function.

The common lexical container can retain the helper without exposing a module
binding.
"""
COLOCATE_CODE = "HLP003"
"""Diagnostic for a local `TypeIs` predicate separated from its narrowed type.

Standalone string documentation immediately after the type remains part of the
allowed adjacency.
"""

__all__ = ["HelperTopologyPlugin"]


class HelperTopologyPlugin:
    """Expose helper-topology diagnostics through Flake8.

    Flake8 supplies a parsed module and the path to the identical source. The
    plugin retains those inputs until `run()` reads the source for lexical
    symbol resolution, then yields HLP001, HLP002, and HLP003 diagnostics without
    changing the source or retaining state between runs.

    # Usage

    Register this class as a Flake8 AST plugin. The file must remain readable and
    unchanged between construction and `run`.
    """

    name = "dirdiff-helper-topology"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Bind a parsed module to its readable, unchanged source path.

        # Parameters

        - `tree`: Parsed module supplied by Flake8.
        - `filename`: Path whose source must still correspond to that AST.

        `run` performs symbol-table resolution later and asserts this pairing.
        """
        assert isinstance(tree, ast.Module)
        self.tree = tree
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[HelperTopologyPlugin]]]:
        """Yield source-ordered Flake8 tuples for resolved private helpers.

        # Usage

        Flake8 iterates the returned tuples after constructing the plugin from
        one parsed source file.

        # Returns

        - `First`: Source line of the private-helper diagnostic.
        - `Second`: Zero-based source column.
        - `Third`: Rule-prefixed diagnostic message.
        - `Fourth`: This plugin class, as required by Flake8.
        - `Order`: Diagnostics follow AST traversal order; an empty iterator
          means no private helper violated the topology rules.

        # Failures

        ``filename`` must still contain the source represented by ``tree``.
        Invalid AST/symbol-table correspondence fails rather than producing
        diagnostics for a different lexical program.
        """
        source = Path(self.filename).read_text()
        module_table = symtable.symtable(source, self.filename, "exec")
        visitor = _HelperTopologyVisitor(
            module=self.tree,
            module_table=module_table,
        )
        visitor.visit(self.tree)
        for node, code, message in visitor.diagnostics:
            yield node.lineno, node.col_offset, f"{code} {message}", type(self)


class _HelperTopologyVisitor(ast.NodeVisitor):
    """Collect helper topology from one AST and its matching symbol table.

    The visitor owns the candidate index, scope stacks, resolved references,
    and resulting diagnostics for one traversal. It recognizes only lexical
    module bindings and must not infer imports, runtime aliases, or references
    in other modules.
    """

    def __init__(
        self,
        *,
        module: ast.Module,
        module_table: symtable.SymbolTable,
    ) -> None:
        """Index eligible declarations and initialize one traversal's state.

        ``module_table`` must describe the exact source parsed into ``module``;
        traversal asserts when their lexical scopes cannot be paired.

        # Parameters

        - `module`: Parsed module whose declarations and references are visited.
        - `module_table`: Compiler symbol table built from the same source text.
        """
        public_names: set[str] = set()
        type_is_names: set[str] = set()
        typing_modules: set[str] = set()
        for statement in module.body:
            if isinstance(statement, ast.ImportFrom) and statement.module in {
                "typing",
                "typing_extensions",
            }:
                type_is_names.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name == "TypeIs"
                )
            elif isinstance(statement, ast.Import):
                typing_modules.update(
                    alias.asname or alias.name
                    for alias in statement.names
                    if alias.name in {"typing", "typing_extensions"}
                )
            value: ast.expr | None = None
            if (
                isinstance(statement, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in statement.targets
                )
            ) or (
                isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and statement.target.id == "__all__"
            ):
                value = statement.value

            if isinstance(value, ast.List | ast.Tuple):
                elements = value.elts
                if all(
                    isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                    for element in elements
                ):
                    public_names.update(
                        element.value
                        for element in elements
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)
                    )

        candidates: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        duplicate_names: set[str] = set()
        for statement in module.body:
            if not isinstance(
                statement, ast.FunctionDef | ast.AsyncFunctionDef
            ):
                continue
            assert statement.end_lineno is not None
            return_annotation = statement.returns
            if (
                statement.name.startswith("test_")
                or statement.name == "main"
                or len(statement.decorator_list) > 0
                or statement.end_lineno - statement.lineno + 1 > 10
                or _is_type_is_annotation(
                    return_annotation,
                    type_is_names=type_is_names,
                    typing_modules=typing_modules,
                )
            ):
                continue
            if statement.name in candidates:
                duplicate_names.add(statement.name)
                continue
            if statement.name not in public_names:
                candidates[statement.name] = statement
        for name in duplicate_names:
            candidates.pop(name)

        local_types: dict[str, int] = {}
        for index, statement in enumerate(module.body):
            if isinstance(statement, ast.ClassDef):
                local_types[statement.name] = index
            elif isinstance(statement, ast.TypeAlias) and isinstance(
                statement.name, ast.Name
            ):
                local_types[statement.name.id] = index

        colocation_diagnostics: list[
            tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str]
        ] = []
        for index, statement in enumerate(module.body):
            if (
                not isinstance(
                    statement, ast.FunctionDef | ast.AsyncFunctionDef
                )
                or statement.name in public_names
            ):
                continue
            target_name = _type_is_target_name(
                statement.returns,
                type_is_names=type_is_names,
                typing_modules=typing_modules,
            )
            if target_name is None or target_name not in local_types:
                continue
            preceding_index = index - 1
            while preceding_index >= 0 and _is_string_expression(
                module.body[preceding_index]
            ):
                preceding_index -= 1
            if preceding_index != local_types[target_name]:
                colocation_diagnostics.append(
                    (
                        statement,
                        COLOCATE_CODE,
                        f"TypeIs function {statement.name!r} must sit directly "
                        f"after local type {target_name!r}",
                    )
                )

        self._candidates = candidates
        self._references: dict[
            str,
            list[
                tuple[
                    ast.Name,
                    ast.FunctionDef | ast.AsyncFunctionDef | None,
                ]
            ],
        ] = {name: [] for name in candidates}
        self._table_stack = [module_table]
        self._used_tables: set[symtable.SymbolTable] = set()
        self._named_function_stack: list[
            ast.FunctionDef | ast.AsyncFunctionDef
        ] = []
        self._inlined_comprehension_bindings: list[set[str]] = []
        self.diagnostics: list[
            tuple[ast.FunctionDef | ast.AsyncFunctionDef, str, str]
        ] = colocation_diagnostics

    @override
    def visit_Module(self, node: ast.Module) -> None:
        """Traverse the module, then classify each indexed helper's references.

        One external reference produces HLP001. Multiple references under one
        outer function produce HLP002. Results, including HLP003, are sorted by
        declaration line before publication.
        """
        for statement in node.body:
            self.visit(statement)

        for name, function in self._candidates.items():
            references = self._references[name]
            if len(references) == 1:
                self.diagnostics.append(
                    (
                        function,
                        INLINE_CODE,
                        f"module-local function {name!r} has one external "
                        "reference; inline it or nest it beside that use",
                    )
                )
                continue
            if len(references) < 2:
                continue

            container = references[0][1]
            if container is not None and all(
                reference_container is container
                for _, reference_container in references
            ):
                self.diagnostics.append(
                    (
                        function,
                        NEST_CODE,
                        f"module-local function {name!r} is referenced only "
                        f"beneath {container.name!r}; nest it there",
                    )
                )

        self.diagnostics.sort(key=lambda diagnostic: diagnostic[0].lineno)

    @override
    def visit_Name(self, node: ast.Name) -> None:
        """Count a load only when lexical resolution reaches an indexed helper.

        Local shadowing, inlined comprehension targets, and recursive references
        do not count. Each accepted reference retains its outermost function.
        """
        if (
            not isinstance(node.ctx, ast.Load)
            or node.id not in self._candidates
        ):
            return
        if any(
            node.id in bindings
            for bindings in self._inlined_comprehension_bindings
        ):
            return

        table = self._table_stack[-1]
        symbol = table.lookup(node.id)
        if (
            table.get_type() != symtable.SymbolTableType.MODULE
            and not symbol.is_global()
        ):
            return

        function = self._candidates[node.id]
        if function in self._named_function_stack:
            return
        container = (
            self._named_function_stack[0]
            if len(self._named_function_stack) > 0
            else None
        )
        self._references[node.id].append((node, container))

    @override
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Traverse a synchronous declaration with compiler-accurate scoping.

        `_visit_named_function` handles signature expressions in the parent and
        statements in the function's claimed symbol table.
        """
        self._visit_named_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Traverse an async declaration under ordinary named-function scoping.

        Async execution semantics do not change how helper names resolve, so the
        shared named-function traversal applies.
        """
        self._visit_named_function(node)

    def _visit_named_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """Traverse signature syntax outside and body syntax inside one function.

        The matching compiler table and function are pushed only for the body.
        Both stacks must return to their prior state before this call completes.
        """
        for decorator in node.decorator_list:
            self.visit(decorator)
        for default in node.args.defaults:
            self.visit(default)
        for keyword_default in node.args.kw_defaults:
            if keyword_default is not None:
                self.visit(keyword_default)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            if argument.annotation is not None:
                self.visit(argument.annotation)
        if (
            node.args.vararg is not None
            and node.args.vararg.annotation is not None
        ):
            self.visit(node.args.vararg.annotation)
        if (
            node.args.kwarg is not None
            and node.args.kwarg.annotation is not None
        ):
            self.visit(node.args.kwarg.annotation)
        if node.returns is not None:
            self.visit(node.returns)
        for type_parameter in node.type_params:
            self.visit(type_parameter)

        table = self._take_child_table(
            name=node.name,
            line=node.lineno,
            table_type=symtable.SymbolTableType.FUNCTION,
        )
        self._table_stack.append(table)
        self._named_function_stack.append(node)
        for statement in node.body:
            self.visit(statement)
        popped_function = self._named_function_stack.pop()
        popped_table = self._table_stack.pop()
        assert popped_function is node
        assert popped_table is table

    @override
    def visit_Lambda(self, node: ast.Lambda) -> None:
        """Traverse lambda defaults in the parent and its body in lambda scope.

        The claimed compiler table covers only the body expression and is removed
        immediately afterward.
        """
        for default in node.args.defaults:
            self.visit(default)
        for keyword_default in node.args.kw_defaults:
            if keyword_default is not None:
                self.visit(keyword_default)

        table = self._take_child_table(
            name="lambda",
            line=node.lineno,
            table_type=symtable.SymbolTableType.FUNCTION,
        )
        self._table_stack.append(table)
        self.visit(node.body)
        popped_table = self._table_stack.pop()
        assert popped_table is table

    @override
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Traverse class inputs in the parent and declarations in class scope.

        Decorators, bases, keywords, and type parameters resolve before the class
        table is pushed. The table remains active for every body statement.
        """
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)
        for type_parameter in node.type_params:
            self.visit(type_parameter)

        table = self._take_child_table(
            name=node.name,
            line=node.lineno,
            table_type=symtable.SymbolTableType.CLASS,
        )
        self._table_stack.append(table)
        for statement in node.body:
            self.visit(statement)
        popped_table = self._table_stack.pop()
        assert popped_table is table

    @override
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        """Traverse the first iterable outside and the rest in generator scope.

        Later iterables, targets, filters, and the result expression all use the
        claimed `genexpr` compiler table.
        """
        first_generator = node.generators[0]
        self.visit(first_generator.iter)
        table = self._take_child_table(
            name="genexpr",
            line=node.lineno,
            table_type=symtable.SymbolTableType.FUNCTION,
        )
        self._table_stack.append(table)
        for index, generator in enumerate(node.generators):
            if index > 0:
                self.visit(generator.iter)
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.elt)
        popped_table = self._table_stack.pop()
        assert popped_table is table

    @override
    def visit_ListComp(self, node: ast.ListComp) -> None:
        """Traverse an inlined list comprehension without counting target names.

        Its result expression is visited after generator bindings are indexed.
        """
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Traverse an inlined set comprehension without counting target names.

        Its result expression is visited after generator bindings are indexed.
        """
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Traverse both dict result expressions under isolated target bindings.

        The key and value are visited after every generator and filter.
        """
        self._visit_inlined_comprehension(node, values=(node.key, node.value))

    def _visit_inlined_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
        *,
        values: tuple[ast.expr, ...],
    ) -> None:
        """Traverse Python 3.14's inlined comprehension binding region.

        # Parameters

        - `node`: List, set, or dict comprehension whose targets remain local.
        - `values`: Result expressions visited after generator bindings exist.

        The first iterable runs outside the isolated target-name region, matching
        compiler scoping. The binding stack is removed before this call returns.
        """
        first_generator = node.generators[0]
        self.visit(first_generator.iter)
        bindings: set[str] = set()
        self._inlined_comprehension_bindings.append(bindings)
        for index, generator in enumerate(node.generators):
            if index > 0:
                self.visit(generator.iter)
            bindings.update(
                child.id
                for child in ast.walk(generator.target)
                if isinstance(child, ast.Name)
                and isinstance(child.ctx, ast.Store)
            )
            self.visit(generator.target)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        popped_bindings = self._inlined_comprehension_bindings.pop()
        assert popped_bindings is bindings

    def _take_child_table(
        self,
        *,
        name: str,
        line: int,
        table_type: symtable.SymbolTableType,
    ) -> symtable.SymbolTable:
        """Claim the compiler table matching one AST lexical-scope node.

        # Parameters

        - `name`: Compiler scope name expected for the AST node.
        - `line`: Declaration line used to distinguish same-named scopes.
        - `table_type`: Function, class, or comprehension scope kind required.

        A table can be claimed once. Missing or ambiguous matches fail the
        AST/source correspondence assertion.
        """
        parent = self._table_stack[-1]
        candidates = [
            child
            for child in parent.get_children()
            if child.get_name() == name
            and child.get_lineno() == line
            and child.get_type() == table_type
            and child not in self._used_tables
        ]
        if len(candidates) == 0:
            type_parameter_tables = [
                child
                for child in parent.get_children()
                if child.get_name() == name
                and child.get_lineno() == line
                and child.get_type() == symtable.SymbolTableType.TYPE_PARAMETERS
            ]
            candidates = [
                child
                for parameter_table in type_parameter_tables
                for child in parameter_table.get_children()
                if child.get_name() == name
                and child.get_lineno() == line
                and child.get_type() == table_type
                and child not in self._used_tables
            ]

        assert len(candidates) == 1, (
            f"expected one {table_type.value} symbol table for {name!r} "
            f"at line {line}, found {len(candidates)}"
        )
        table = candidates[0]
        self._used_tables.add(table)
        return table


def _type_is_target_name(
    annotation: ast.expr | None,
    *,
    type_is_names: set[str],
    typing_modules: set[str],
) -> str | None:
    """Return the direct name in a resolved `TypeIs[T]` annotation.

    # Parameters

    - `annotation`: Return annotation to inspect, if the function has one.
    - `type_is_names`: Directly imported names known to mean `TypeIs`.
    - `typing_modules`: Imported module aliases that may qualify `TypeIs`.

    Subscript arguments more complex than a direct local name return `None`.

    # Returns

    - `str`: The direct local target name inside a resolved `TypeIs[T]`.
    - `None`: The annotation is not a resolved `TypeIs`, or its target is not a
      direct name. The caller must not treat it as a narrowing helper target.
    """
    if not _is_type_is_annotation(
        annotation,
        type_is_names=type_is_names,
        typing_modules=typing_modules,
    ):
        return None
    assert isinstance(annotation, ast.Subscript)
    return (
        annotation.slice.id if isinstance(annotation.slice, ast.Name) else None
    )


def _is_type_is_annotation(
    annotation: ast.expr | None,
    *,
    type_is_names: set[str],
    typing_modules: set[str],
) -> bool:
    """Recognize `TypeIs` only when its constructor import resolves.

    # Parameters

    - `annotation`: Return annotation syntax to inspect.
    - `type_is_names`: Direct import names bound to `TypeIs`.
    - `typing_modules`: Module aliases bound to `typing` variants.

    An unrelated local named `TypeIs` does not satisfy this syntactic import
    boundary.
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


def _is_string_expression(statement: ast.stmt) -> bool:
    """Recognize a standalone string that may document a preceding type alias.

    HLP003 skips any consecutive matches while finding the declaration directly
    before a local `TypeIs` predicate.
    """
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )
