from __future__ import annotations

import ast
import symtable
from collections.abc import Iterator
from pathlib import Path
from typing import override

INLINE_CODE = "HLP001"
NEST_CODE = "HLP002"
COLOCATE_CODE = "HLP003"

__all__ = ["HelperTopologyPlugin"]


class HelperTopologyPlugin:
    name = "dirdiff-helper-topology"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        assert isinstance(tree, ast.Module)
        self.tree = tree
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[HelperTopologyPlugin]]]:
        source_path = Path(self.filename).resolve()
        source = source_path.read_text()
        module_table = symtable.symtable(source, self.filename, "exec")
        visitor = _HelperTopologyVisitor(
            module=self.tree,
            module_table=module_table,
        )
        visitor.visit(self.tree)
        for node, code, message in visitor.diagnostics:
            yield node.lineno, node.col_offset, f"{code} {message}", type(self)


class _HelperTopologyVisitor(ast.NodeVisitor):
    def __init__(
        self,
        *,
        module: ast.Module,
        module_table: symtable.SymbolTable,
    ) -> None:
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
        self._visit_named_function(node)

    @override
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_named_function(node)

    def _visit_named_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
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
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_inlined_comprehension(node, values=(node.elt,))

    @override
    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_inlined_comprehension(node, values=(node.key, node.value))

    def _visit_inlined_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.DictComp,
        *,
        values: tuple[ast.expr, ...],
    ) -> None:
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
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Constant)
        and isinstance(statement.value.value, str)
    )
