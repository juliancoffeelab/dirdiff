from __future__ import annotations

import os
import posixpath
import stat
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeIs, override

from dirdiff.change_source.base import (
    ChangeSourceProtocol,
    FileChange,
    FileProperties,
    LazyReason,
    ListedChanges,
    ListedPath,
    LoadedFile,
    PresetGroup,
    SideName,
    SourceFiles,
    StorageFile,
    WorkspaceChanges,
    WorkspaceStorageProtocol,
)
from dirdiff.engines import DirdiffError
from dirdiff.util import Result, catch

__all__ = [
    "PresetCatalog",
    "PresetCatalogDir",
    "PresetChangeSource",
    "preset_catalogs",
]

_LAZY_REASONS: tuple[LazyReason, ...] = (
    "too_big",
    "generated",
    "deleted",
    "untracked",
    "pure_renamed",
)


def _is_lazy_reason(value: object) -> TypeIs[LazyReason]:
    return isinstance(value, str) and value in _LAZY_REASONS


@dataclass(frozen=True)
class PresetCatalogDir:
    catalog_id: str
    name: str
    root: Path


def preset_catalogs(presets_root: Path) -> tuple[PresetCatalogDir, ...]:
    if not presets_root.is_dir():
        return ()
    catalogs: list[PresetCatalogDir] = []
    for path in sorted(presets_root.iterdir()):
        if not path.is_dir():
            continue
        metadata_path = path / "preset.toml"
        if not metadata_path.exists():
            raise DirdiffError(f"Preset catalog is missing preset.toml: {path}")
        metadata = tomllib.loads(metadata_path.read_text(encoding="utf-8"))
        if set(metadata) != {"name"}:
            raise DirdiffError(
                f"Preset catalog metadata must contain only name: "
                f"{metadata_path}"
            )
        name = metadata["name"]
        if not isinstance(name, str) or name.strip() == "":
            raise DirdiffError(
                f"Preset catalog name must be a non-empty string: "
                f"{metadata_path}"
            )
        catalogs.append(
            PresetCatalogDir(catalog_id=path.name, name=name, root=path)
        )
    return tuple(catalogs)


class _PresetWorkspace(WorkspaceStorageProtocol):
    def __init__(self, presets_root: Path) -> None:
        self.presets_root = presets_root.expanduser().resolve()

    def _preset_group_dirs(self) -> list[Path]:
        if not self.presets_root.exists():
            return []
        return sorted(
            path for path in self.presets_root.iterdir() if path.is_dir()
        )

    def _fixture_pairs_for_group(
        self, group_name: str
    ) -> list[tuple[Path, Path | None, Path | None]]:
        group_dir = self.presets_root / group_name
        if not group_dir.is_dir():
            raise DirdiffError(f"Unknown preset group: {group_name}")
        pairs: list[tuple[Path, Path | None, Path | None]] = []
        for path in sorted(group_dir.iterdir()):
            if not path.is_dir():
                continue
            old_files = sorted(path.glob("old.*"))
            new_files = sorted(path.glob("new.*"))
            if len(old_files) > 1 or len(new_files) > 1:
                continue
            if old_files == [] and new_files == []:
                continue
            pairs.append(
                (
                    path,
                    old_files[0] if old_files != [] else None,
                    new_files[0] if new_files != [] else None,
                )
            )
        return pairs

    def _list_preset_names(self) -> list[str]:
        return [
            group_dir.name
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def list_preset_groups(self) -> list[PresetGroup]:
        return [
            {
                "id": group_dir.name,
                "display_name": group_dir.name.replace("-", " ").title(),
            }
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def default_preset_name(self) -> str:
        names = self._list_preset_names()
        if names == []:
            raise DirdiffError(f"No presets found in {self.presets_root}.")
        return names[0]

    def _preset_pair(self, preset_dir: Path) -> tuple[Path | None, Path | None]:
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))
        if len(old_files) > 1 or len(new_files) > 1:
            raise DirdiffError(
                f"Preset {preset_dir.name} must contain at most one old.* and one new.* file."
            )
        if old_files == [] and new_files == []:
            raise DirdiffError(
                f"Preset {preset_dir.name} must contain an old.* or a new.* file."
            )
        return (
            old_files[0] if old_files != [] else None,
            new_files[0] if new_files != [] else None,
        )

    def lazy_reason_metadata(
        self,
        repository_path: str,
    ) -> tuple[LazyReason, str] | None:
        normalized_path = self.normalize_repo_path(repository_path)
        preset_dir = self.presets_root / PurePosixPath(normalized_path).parent
        metadata_path = preset_dir / "preset.toml"
        if not metadata_path.exists():
            return None
        content = metadata_path.read_text(encoding="utf-8")
        metadata = tomllib.loads(content)
        if set(metadata) != {"lazy_reason"}:
            raise DirdiffError(
                f"Preset metadata must contain only lazy_reason: {metadata_path}"
            )
        reason = metadata["lazy_reason"]
        if not _is_lazy_reason(reason):
            raise DirdiffError(f"Unsupported preset lazy_reason: {reason!r}")
        return reason, content

    def list_changes(
        self,
        group: str,
    ) -> ListedChanges:
        # Shape validation only: the fixture listing below is the single
        # directory scan, and it doubles as the emptiness check the full
        # group validation would otherwise repeat.
        normalized_group = group.strip()
        if normalized_group == "":
            normalized_group = self.default_preset_name()
        candidate = PurePosixPath(normalized_group)
        if candidate.is_absolute():
            raise DirdiffError("Preset name must be preset-relative.")
        if normalized_group.startswith("../") or normalized_group == "..":
            raise DirdiffError("Preset name must stay inside the presets root.")
        if len(candidate.parts) != 1:
            raise DirdiffError("Preset name must be a top-level group.")
        if not (self.presets_root / normalized_group).is_dir():
            raise DirdiffError(f"Unknown preset: {normalized_group}")
        pairs = self._fixture_pairs_for_group(normalized_group)
        if pairs == []:
            raise DirdiffError(
                f"Preset group has no fixtures: {normalized_group}"
            )
        entries: list[ListedPath] = []
        for preset_dir, old_path, new_path in pairs:
            prefix = f"{normalized_group}/{preset_dir.name}"
            left_side = (
                None if old_path is None else f"{prefix}/{old_path.name}"
            )
            right_side = (
                None if new_path is None else f"{prefix}/{new_path.name}"
            )
            # The File is named by the side it ends on, and by the side it
            # started on when the Tab deletes it. Metadata is read from
            # the same path: it lives beside both sides in the fixture
            # directory, so either one reaches it.
            display_name = right_side if right_side is not None else left_side
            assert display_name is not None, (
                "a listed fixture has at least one present side"
            )
            lazy_reason = self.lazy_reason_metadata(display_name)
            entries.append(
                ListedPath(
                    left_path=left_side,
                    right_path=right_side,
                    display_name=display_name,
                    # No declared change type: which of old.*/new.* exist is it.
                    change_type=(
                        "modify"
                        if left_side is not None and right_side is not None
                        else "add"
                        if left_side is None
                        else "delete"
                    ),
                    lazy_reason_override=(
                        lazy_reason[0] if lazy_reason is not None else None
                    ),
                    lazy_metadata_content=(
                        lazy_reason[1] if lazy_reason is not None else None
                    ),
                    left_properties=(
                        None
                        if old_path is None
                        else self.file_properties(
                            f"{prefix}/{old_path.name}", "old"
                        )
                    ),
                    right_properties=(
                        None
                        if new_path is None
                        else self.file_properties(
                            f"{prefix}/{new_path.name}", "new"
                        )
                    ),
                )
            )
        return ListedChanges(tuple(entries), None, None)

    @override
    def normalize_repo_path(self, raw_path: str) -> str:
        if raw_path.strip() == "":
            raise DirdiffError("Preset path is required.")
        if raw_path.endswith("/"):
            raise DirdiffError("Preset path must point to a file.")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise DirdiffError("Use a preset-relative path.")
        normalized = posixpath.normpath(candidate.as_posix())
        if normalized.startswith("../") or normalized == "..":
            raise DirdiffError("Preset path must stay inside the presets root.")
        parts = PurePosixPath(normalized).parts
        if len(parts) != 3:
            raise DirdiffError(
                "Preset path must look like <group>/<preset>/<file>."
            )
        preset_dir = self.presets_root / PurePosixPath(normalized).parent
        if not preset_dir.is_dir():
            raise DirdiffError(f"Unknown preset path: {normalized}")
        return normalized

    @override
    def file_properties(self, path: str, side: SideName) -> FileProperties:
        normalized = self.normalize_repo_path(path)
        file_path = self.presets_root / normalized
        try:
            mode = file_path.lstat().st_mode
        except OSError as exc:
            # TODO(hosting): Redact `exc` at this reviewer-facing boundary;
            # OSError text exposes the server's absolute preset filesystem path.
            raise DirdiffError(
                f"Could not inspect preset file {normalized}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode):
            return FileProperties("symbolic_link", False)
        if not stat.S_ISREG(mode):
            raise DirdiffError(
                f"Preset path is not a regular file or symbolic link: {normalized}"
            )
        return FileProperties("file", bool(mode & stat.S_IXUSR))

    @override
    def file_size(self, path: str, side: SideName) -> int:
        normalized = self.normalize_repo_path(path)
        file_path = self.presets_root / normalized
        try:
            metadata = file_path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return len(os.readlink(os.fsencode(file_path)))
            if stat.S_ISREG(metadata.st_mode):
                return metadata.st_size
        except OSError as exc:
            # TODO(hosting): Redact `exc` at this reviewer-facing boundary;
            # OSError text exposes the server's absolute preset filesystem path.
            raise DirdiffError(
                f"Could not inspect preset file {normalized}: {exc}"
            ) from exc
        raise DirdiffError(
            f"Preset path is not a regular file or symbolic link: {normalized}"
        )

    @override
    def load_version(self, path: str, side: SideName) -> bytes:
        normalized_path = self.normalize_repo_path(path)
        # Resolve the manifest path to its on-disk fixture: a direct file when
        # the path exists as written, otherwise the old/new member of its
        # fixture pair matching the file name. `side` does not participate:
        # preset manifest paths already name their exact side-specific file.
        file_path = self.presets_root / normalized_path
        if not file_path.is_symlink() and not file_path.is_file():
            preset_dir = (
                self.presets_root / PurePosixPath(normalized_path).parent
            )
            old_path, new_path = self._preset_pair(preset_dir)
            wanted_name = PurePosixPath(normalized_path).name
            if old_path is not None and wanted_name == old_path.name:
                file_path = old_path
            elif new_path is not None and wanted_name == new_path.name:
                file_path = new_path
            else:
                raise DirdiffError(f"Preset file is missing: {normalized_path}")
        if file_path.is_symlink():
            try:
                return os.readlink(os.fsencode(file_path))
            except OSError as exc:
                raise DirdiffError(
                    f"Could not read preset link {normalized_path}: {exc}"
                ) from exc
        if not file_path.exists():
            raise DirdiffError(f"Preset file is missing: {normalized_path}")
        try:
            return file_path.read_bytes()
        except OSError as exc:
            raise DirdiffError(
                f"Could not read preset file {normalized_path}: {exc}"
            ) from exc

    @override
    def load_versions(
        self, sides: tuple[StorageFile, ...]
    ) -> tuple[Result[bytes, DirdiffError], ...]:
        load = catch(DirdiffError)(self.load_version)
        return tuple(
            load(path, side) for path, side, _properties, _object_id in sides
        )


class PresetCatalog:
    root: Path

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()
        self._workspace = _PresetWorkspace(self.root)

    def list_preset_groups(self) -> list[PresetGroup]:
        return self._workspace.list_preset_groups()

    def default_preset_name(self) -> str:
        return self._workspace.default_preset_name()


class PresetChangeSource(ChangeSourceProtocol):
    def __init__(
        self, catalog_root: Path, *, group: str, max_link_target_bytes: int
    ) -> None:
        assert max_link_target_bytes >= 0
        self._workspace = _PresetWorkspace(catalog_root)
        self._group = group
        # Bounds only the reached non-link target of a link; outer content and
        # raw link payloads are unbounded.
        self._max_link_target_bytes = max_link_target_bytes
        # Fixture bytes are acquired once; later edits need a fresh source.
        self._files: SourceFiles | None = None

    @override
    def changes(self) -> WorkspaceChanges:
        if self._files is None:
            self._files = SourceFiles(
                self._workspace,
                self._workspace.list_changes(self._group),
                left=self._group,
                right="new",
                identity_namespace=b"preset-materialized-bytes-v1-links-v1",
                max_link_target_bytes=self._max_link_target_bytes,
            )
        return self._files.changes

    @override
    def load(self, files: Sequence[FileChange]) -> Sequence[LoadedFile]:
        assert self._files is not None, "changes() must precede load()"
        return self._files.load(files)
