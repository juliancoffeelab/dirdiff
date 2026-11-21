from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

MISSING_EXPORT_CODE = "PKG001"
UNUSED_EXPORT_CODE = "PKG002"
PRIVATE_EXPORT_CODE = "PKG003"
PACKAGE_IMPORT_CODE = "PKG004"

type _Diagnostic = tuple[int, int, str]

__all__ = ["PackageInterfacePlugin"]


@lru_cache(maxsize=1)
def _project_diagnostics(
    project_root: Path,
) -> dict[Path, tuple[_Diagnostic, ...]]:
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
        "package interface requires src, tests, lints, and alembic"
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

    def import_from_target(importer_path: Path, node: ast.ImportFrom) -> str:
        if node.level == 0:
            return node.module or ""
        # A package's __init__.py resolves relative imports against the
        # package itself, any other module against its containing package.
        importer = module_by_path[importer_path]
        package = (
            importer
            if importer_path.name == "__init__.py"
            else importer.rpartition(".")[0]
        )
        package_parts = package.split(".") if package else []
        assert node.level <= len(package_parts)
        parent = package_parts[: len(package_parts) - (node.level - 1)]
        return ".".join(
            (*parent, *(node.module.split(".") if node.module else ()))
        )

    uses: dict[Path, set[str]] = {path: set() for path in files}
    entrypoint_pattern = re.compile(
        r"(?<![\w.])([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*):([A-Za-z_]\w*)"
    )
    for importer_path, tree in trees.items():
        module_aliases: dict[str, set[tuple[Path, tuple[str, ...]]]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                target_module = import_from_target(importer_path, node)
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

    diagnostics: dict[Path, list[_Diagnostic]] = {path: [] for path in files}
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
                target_module = import_from_target(importer_path, node)
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


class PackageInterfacePlugin:
    name = "dirdiff-package-interface"
    version = "0.1.0"

    def __init__(self, tree: ast.AST, filename: str) -> None:
        self.filename = filename

    def run(
        self,
    ) -> Iterator[tuple[int, int, str, type[PackageInterfacePlugin]]]:
        source_path = Path(self.filename).resolve()
        project_root = next(
            parent
            for parent in source_path.parents
            if (parent / ".flake8").is_file()
        )
        # The whole project is analysed once per process and cached; each
        # flake8 worker repeats it, which costs time but not correctness.
        for line, column, message in _project_diagnostics(project_root).get(
            source_path, ()
        ):
            yield line, column, message, type(self)
