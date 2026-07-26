"""Flake8 checks for the lexical placement of module-local functions.

HLP is a syntax-and-symbol-table lint with two diagnostics. HLP001 reports a
module-local function with one non-recursive reference, which can be inlined at
that use. HLP002 reports a module-local function with several references that
all occur beneath one outermost named function, which can contain the helper
lexically.

The plugin treats functions named by a literal module ``__all__`` as public and
therefore outside its interface. It also excludes ``test_*``, ``main``,
decorated functions, ``TypeIs[...]`` contracts, and functions whose inclusive
source span exceeds ten lines. Python's symbol table determines whether each
name load actually reaches the module binding, so shadowed local names are not
counted. The plugin owns no persistent state, changes no source, and does not
judge whether an exceptional separate contract is beneficial.
"""

from __future__ import annotations

import ast
import symtable
from collections.abc import Iterator
from pathlib import Path
from typing import override

INLINE_CODE = "HLP001"
NEST_CODE = "HLP002"

__all__ = ["HelperTopologyPlugin"]


class HelperTopologyPlugin:
    """Expose helper-topology diagnostics through Flake8.

    Flake8 supplies a parsed module and the path to the identical source. The
    plugin retains those inputs until ``run()`` reads the source for lexical
    symbol resolution, then yields HLP001 and HLP002 diagnostics without
    changing the source or retaining state between runs.
    """

    name = "dirdiff-helper-topology"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        """Bind a parsed module to its readable, unchanged source path."""
        assert isinstance(tree, ast.Module)
        self.tree = tree
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[HelperTopologyPlugin]]]:
        """Yield source-ordered Flake8 tuples for resolved private helpers.

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
        """
        public_names: set[str] = set()
        for statement in module.body:
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
                or (
                    isinstance(return_annotation, ast.Subscript)
                    and (
                        (
                            isinstance(return_annotation.value, ast.Name)
                            and return_annotation.value.id == "TypeIs"
                        )
                        or (
                            isinstance(return_annotation.value, ast.Attribute)
                            and return_annotation.value.attr == "TypeIs"
                        )
                    )
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
        ] = []

    @override
    def visit_Module(self, node: ast.Module) -> None:
        """Traverse the module and publish source-ordered diagnostics."""
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
                        "reference; inline it at that use",
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
        """Count loads that resolve to an indexed module function binding."""
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
        """Visit signature syntax in its parent scope and its body in its scope."""
        self._visit_named_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Visit an async declaration with ordinary named-function scoping."""
        self._visit_named_function(node)

    def _visit_named_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        """Traverse a named function using its compiler symbol table."""
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
        """Traverse lambda defaults outside and its expression inside its scope."""
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
        """Traverse class inputs outside and its statements inside class scope."""
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
        """Traverse a generator expression according to its compiler scope."""
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
        """Traverse an inlined list comprehension with isolated target names."""
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        """Traverse an inlined set comprehension with isolated target names."""
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        """Traverse an inlined dict comprehension with isolated target names."""
        self._visit_inlined_comprehension(node, values=(node.key, node.value))

    def _visit_inlined_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
        *,
        values: tuple[ast.expr, ...],
    ) -> None:
        """Traverse Python 3.14's inlined comprehension binding region."""
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
        """Claim the compiler table matching one AST lexical-scope node."""
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
