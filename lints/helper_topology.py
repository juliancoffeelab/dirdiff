from __future__ import annotations

import ast
import re
import symtable
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import override

INLINE_CODE = "HLP001"
NEST_CODE = "HLP002"
COLOCATE_CODE = "HLP003"
MISSING_EXPORT_CODE = "HLP004"
UNUSED_EXPORT_CODE = "HLP005"
PRIVATE_EXPORT_CODE = "HLP006"
PACKAGE_IMPORT_CODE = "HLP007"

type _InterfaceDiagnostic = tuple[int, int, str]

__all__ = ["HelperTopologyPlugin"]


@lru_cache(maxsize=1)
def _project_interface_diagnostics(
    project_root: Path,
) -> dict[Path, tuple[_InterfaceDiagnostic, ...]]:
    def module_parts(path: Path) -> tuple[str, ...]:
        source_root = project_root / "src"
        tests_root = project_root / "tests"
        lints_root = project_root / "lints"
        alembic_root = project_root / "alembic"
        if path.is_relative_to(source_root):
            relative = path.relative_to(source_root)
            prefix: tuple[str, ...] = ()
        elif path.is_relative_to(tests_root):
            relative = path.relative_to(tests_root)
            prefix = ("tests",)
        elif path.is_relative_to(lints_root):
            relative = path.relative_to(lints_root)
            prefix = ()
        else:
            assert path.is_relative_to(alembic_root)
            relative = path.relative_to(alembic_root)
            prefix = ("alembic",)
        parts = (*prefix, *relative.with_suffix("").parts)
        return parts[:-1] if parts[-1] == "__init__" else parts

    indexed_roots = tuple(
        path
        for name in ("src", "tests", "lints", "alembic")
        if (path := project_root / name).is_dir()
    )
    assert len(indexed_roots) == 4, (
        "helper topology requires src, tests, lints, and alembic"
    )
    preset_root = project_root / "tests" / "presets"
    files = tuple(
        sorted(
            path.resolve()
            for root in indexed_roots
            for path in root.rglob("*.py")
            if preset_root.resolve() not in path.resolve().parents
        )
    )
    trees = {
        path: ast.parse(path.read_text(), filename=str(path)) for path in files
    }
    module_by_path: dict[Path, str] = {}
    path_by_module: dict[str, Path] = {}
    for path in files:
        parts = module_parts(path)
        module = ".".join(parts)
        assert module not in path_by_module, f"duplicate module path: {module}"
        module_by_path[path] = module
        path_by_module[module] = path
        # Pytest places the tests root on sys.path, so its shared top-level
        # helpers are imported as "helpers" rather than "tests.helpers".
        if path.parent == (project_root / "tests").resolve():
            short_name = path.stem
            assert short_name not in path_by_module
            path_by_module[short_name] = path

    exports: dict[Path, dict[str, ast.Constant]] = {}
    declares_all: set[Path] = set()
    bindings: dict[Path, dict[str, ast.AST]] = {}
    for path, tree in trees.items():
        exported: dict[str, ast.Constant] = {}
        bound: dict[str, ast.AST] = {}
        all_values: list[ast.expr | None] = []
        for statement in tree.body:
            if isinstance(
                statement,
                ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
            ):
                bound.setdefault(statement.name, statement)
            elif isinstance(statement, ast.TypeAlias) and isinstance(
                statement.name, ast.Name
            ):
                bound.setdefault(statement.name.id, statement)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    if isinstance(target, ast.Name):
                        bound.setdefault(target.id, statement)
                        if target.id == "__all__":
                            all_values.append(statement.value)
            elif isinstance(statement, ast.AnnAssign) and isinstance(
                statement.target, ast.Name
            ):
                bound.setdefault(statement.target.id, statement)
                if statement.target.id == "__all__":
                    all_values.append(statement.value)
            elif isinstance(statement, ast.ImportFrom | ast.Import):
                for alias in statement.names:
                    bound.setdefault(
                        alias.asname or alias.name.rsplit(".", 1)[-1],
                        alias,
                    )
        assert len(all_values) <= 1, f"multiple __all__ declarations: {path}"
        if len(all_values) == 1:
            declares_all.add(path)
            value = all_values[0]
            assert isinstance(value, ast.List | ast.Tuple), (
                f"__all__ must be a literal list or tuple: {path}"
            )
            for element in value.elts:
                assert isinstance(element, ast.Constant) and isinstance(
                    element.value, str
                ), f"__all__ entries must be literal strings: {path}"
                assert element.value not in exported, (
                    f"duplicate __all__ entry {element.value!r}: {path}"
                )
                exported[element.value] = element
        exports[path] = exported
        bindings[path] = bound

    uses: dict[Path, set[str]] = {path: set() for path in files}
    entrypoint_pattern = re.compile(
        r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)"
    )
    for importer_path, tree in trees.items():
        importer = module_by_path[importer_path]
        package = (
            importer
            if importer_path.name == "__init__.py"
            else importer.rpartition(".")[0]
        )
        module_aliases: dict[str, set[tuple[Path, tuple[str, ...]]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    package_parts = package.split(".") if package else []
                    assert node.level <= len(package_parts)
                    parent = package_parts[
                        : len(package_parts) - (node.level - 1)
                    ]
                    target_module = ".".join(
                        (
                            *parent,
                            *(
                                (node.module or "").split(".")
                                if node.module
                                else ()
                            ),
                        )
                    )
                else:
                    target_module = node.module or ""
                target_path = path_by_module.get(target_module)
                if target_path is None or target_path == importer_path:
                    continue
                for alias in node.names:
                    if alias.name == "*":
                        uses[target_path].update(exports[target_path])
                        continue
                    submodule_path = path_by_module.get(
                        f"{target_module}.{alias.name}"
                    )
                    if submodule_path is not None:
                        module_aliases.setdefault(
                            alias.asname or alias.name, set()
                        ).add((submodule_path, ()))
                    else:
                        uses[target_path].add(alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target_path = path_by_module.get(alias.name)
                    if target_path is not None and target_path != importer_path:
                        module_name_parts = alias.name.split(".")
                        module_aliases.setdefault(
                            alias.asname or module_name_parts[0],
                            set(),
                        ).add(
                            (
                                target_path,
                                ()
                                if alias.asname
                                else tuple(module_name_parts[1:]),
                            )
                        )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                match = entrypoint_pattern.fullmatch(node.value.strip())
                if match is not None:
                    target_path = path_by_module.get(match.group(1))
                    if target_path is not None and target_path != importer_path:
                        uses[target_path].add(match.group(2))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            attributes: list[str] = []
            attribute_value: ast.expr = node
            while isinstance(attribute_value, ast.Attribute):
                attributes.append(attribute_value.attr)
                attribute_value = attribute_value.value
            if not isinstance(attribute_value, ast.Name):
                continue
            attributes.reverse()
            for target_path, module_tail in module_aliases.get(
                attribute_value.id, ()
            ):
                if tuple(attributes[: len(module_tail)]) != module_tail:
                    continue
                if len(attributes) > len(module_tail):
                    uses[target_path].add(attributes[len(module_tail)])

    for config_name in (".flake8", "pyproject.toml"):
        config_path = project_root / config_name
        if not config_path.is_file():
            continue
        for match in entrypoint_pattern.finditer(config_path.read_text()):
            target_path = path_by_module.get(match.group(1))
            if target_path is not None:
                uses[target_path].add(match.group(2))

    diagnostics: dict[Path, list[_InterfaceDiagnostic]] = {
        path: [] for path in files
    }
    for path in files:
        exported = exports[path]
        externally_used = uses[path]
        module_parts_for_path = module_by_path[path].split(".")
        private_package_init = path.name == "__init__.py" and any(
            part.startswith("_") for part in module_parts_for_path[1:]
        )
        requires_declaration = path.is_relative_to(
            project_root / "src"
        ) or path.is_relative_to(project_root / "lints")
        # The source root alone omits __all__: pdoc uses its absence to discover
        # subpackages. This does not exempt root imports from interface checks.
        if (
            requires_declaration
            and not private_package_init
            and path not in declares_all
            and module_by_path[path] != "dirdiff"
        ):
            diagnostics[path].append(
                (
                    1,
                    0,
                    f"{MISSING_EXPORT_CODE} module has no literal __all__ "
                    "declaration",
                )
            )
        if private_package_init and trees[path].body:
            first_statement = trees[path].body[0]
            diagnostics[path].append(
                (
                    first_statement.lineno,
                    first_statement.col_offset,
                    f"{PACKAGE_IMPORT_CODE} private package __init__.py must "
                    "be empty",
                )
            )
        for name in sorted(externally_used - exported.keys()):
            node = bindings[path].get(name, trees[path])
            diagnostics[path].append(
                (
                    getattr(node, "lineno", 1),
                    getattr(node, "col_offset", 0),
                    f"{MISSING_EXPORT_CODE} repo-used name {name!r} is absent "
                    "from __all__",
                )
            )
        for name in sorted(exported.keys() - externally_used):
            node = exported[name]
            diagnostics[path].append(
                (
                    node.lineno,
                    node.col_offset,
                    f"{UNUSED_EXPORT_CODE} exported name {name!r} has no "
                    "repo-external use",
                )
            )
        for name in sorted(name for name in exported if name.startswith("_")):
            node = exported[name]
            diagnostics[path].append(
                (
                    node.lineno,
                    node.col_offset,
                    f"{PRIVATE_EXPORT_CODE} exported name {name!r} has a "
                    "private prefix",
                )
            )

    # Facades publish their implementations, and top-level modules are peer
    # packages whose module file is itself their interface. A module x.py may
    # reach directly into its private _x package. No other module may import
    # _x or anything beneath it, and the private package initializer is empty.
    for importer_path, tree in trees.items():
        importer_parts = module_by_path[importer_path].split(".")
        for node in ast.walk(tree):
            target_modules: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    package_parts = importer_parts[:-1]
                    parent = package_parts[
                        : len(package_parts) - (node.level - 1)
                    ]
                    target_module = ".".join(
                        (
                            *parent,
                            *(
                                (node.module or "").split(".")
                                if node.module
                                else ()
                            ),
                        )
                    )
                else:
                    target_module = node.module or ""
                target_modules.append(target_module)
                target_modules.extend(
                    f"{target_module}.{alias.name}"
                    for alias in node.names
                    if f"{target_module}.{alias.name}" in path_by_module
                )
            elif isinstance(node, ast.Import):
                target_modules.extend(alias.name for alias in node.names)
            else:
                continue

            import_violates_boundary = False
            for target_module in dict.fromkeys(target_modules):
                target_path = path_by_module.get(target_module)
                if target_path is None:
                    continue
                target_parts = target_module.split(".")
                private_indexes = [
                    index
                    for index, part in enumerate(target_parts)
                    if index >= 1 and part.startswith("_")
                ]
                if private_indexes:
                    private_index = private_indexes[0]
                    private_parts = tuple(target_parts[: private_index + 1])
                    target_is_private_facade = (
                        len(target_parts) == private_index + 1
                        and target_path.name == "__init__.py"
                    )
                    paired_module = (
                        *private_parts[:-1],
                        private_parts[-1][1:],
                    )
                    paired_implementation_import = (
                        tuple(importer_parts) == paired_module
                        and not target_is_private_facade
                    )
                    if paired_implementation_import:
                        continue
                    import_violates_boundary = True
                    break

                if importer_path.name == "__init__.py":
                    continue
                if importer_path.is_relative_to(project_root / "tests"):
                    continue
                importer_package = tuple(importer_parts[:2])
                target_package = tuple(target_parts[:2])
                shares_package = (
                    len(importer_package) == 2
                    and importer_package == target_package
                )
                allowed_internal_base = (
                    shares_package
                    and target_path.name == "base.py"
                    and len(target_parts) == 3
                )
                allowed_external_facade = (
                    not shares_package and target_path.name == "__init__.py"
                )
                allowed_top_level_module = len(target_parts) == 2
                if (
                    allowed_internal_base
                    or allowed_external_facade
                    or allowed_top_level_module
                ):
                    continue
                import_violates_boundary = True
                break
            if import_violates_boundary:
                diagnostics[importer_path].append(
                    (
                        node.lineno,
                        node.col_offset,
                        f"{PACKAGE_IMPORT_CODE} imports inside a package must "
                        "target its base.py or the paired module's private "
                        "submodules; imports from outside must target a package "
                        "facade or top-level module",
                    )
                )
    return {path: tuple(sorted(items)) for path, items in diagnostics.items()}


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
        diagnostics = [
            (node.lineno, node.col_offset, f"{code} {message}")
            for node, code, message in visitor.diagnostics
        ]
        project_root = next(
            parent
            for parent in source_path.parents
            if (parent / ".flake8").is_file()
        )
        diagnostics.extend(
            _project_interface_diagnostics(project_root).get(source_path, ())
        )
        for line, column, message in sorted(diagnostics):
            yield line, column, message, type(self)


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
