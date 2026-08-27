"""Preset-backed implementation of `WorkspaceBackendProtocol`.

`PresetBackend` treats test preset directories as read-only backend data.  It lets
the same manifest and rendering paths exercise fixture pairs without requiring
a Git repository.  It should stay limited to preset discovery, path listing,
and file loading for those fixtures.

The directory layout is `<presets root>/<catalog>/<group>/<fixture>/`. One
`PresetBackend` reads one catalog: its root is the catalog directory, and the
groups it lists are that directory's children. `preset_catalogs()` is the level
above — it reads the presets root and says which catalogs exist, which is the
only place that set is stated. Nothing here enumerates catalog names.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TypeIs, override

from dirdiff.backend.base import (
    BranchSelection,
    LazyReason,
    PresetGroup,
    RepoDiff,
    RepoDiffPath,
    SideName,
    WorkspaceBackendProtocol,
)
from dirdiff.engines import DirdiffError

__all__ = [
    "PresetBackend",
    "PresetCatalogDir",
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
    """Narrow untyped preset metadata to a supported lazy reason.

    TOML values enter as `Any`, so membership validation must also provide the
    static type boundary promised by preset metadata parsing.
    """
    return isinstance(value, str) and value in _LAZY_REASONS


@dataclass(frozen=True)
class PresetCatalogDir:
    """One preset catalog: its id, the name it shows, and the directory it is.

    `catalog_id` is the directory's name. It is what a URL carries as
    `preset_type`, what the manifest receives as `project_id`, and what a
    Mark-less preset Room persists, so renaming the directory renames the
    catalog everywhere at once and nothing declares the id a second time.
    `name` is presentation text read from `preset.toml`; nothing selects by it.
    `root` is the absolute catalog directory one `PresetBackend` reads.

    This is directory metadata. It holds no group listing, no fixture, and no
    rendered content — a caller that wants those constructs a `PresetBackend`
    on `root`.
    """

    catalog_id: str
    name: str
    root: Path


def preset_catalogs(presets_root: Path) -> tuple[PresetCatalogDir, ...]:
    """List the catalogs under `presets_root`, ordered by id.

    Every directory there is a catalog and must state its display name in a
    `preset.toml` holding exactly `name`. A directory without that file, or
    with anything else in it, raises `DirdiffError` naming the directory rather
    than being skipped: a mistyped key would otherwise delete a catalog from
    the picker without saying so. A presets root that does not exist has no
    catalogs, which is the true answer when dirdiff runs outside this
    repository.

    Callers rescan per request. Adding a catalog is creating a directory with
    that one file in it, and nothing has to be restarted or edited to see it.

    A fixture directory also holds a `preset.toml`, with a `lazy_reason` key
    and no `name`. The two live at different depths and are read by different
    callers; `PresetBackend.lazy_reason_metadata` reads the other one.
    """
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


class PresetBackend(WorkspaceBackendProtocol):
    """Read diff fixtures from a preset catalog through the backend interface."""

    def __init__(self, presets_root: Path, *, cwd: Path | None = None) -> None:
        """Bind this backend to one preset root and caller working directory."""
        self.presets_root = presets_root.expanduser().resolve()
        self._repo_root = self.presets_root
        selected_cwd = cwd if cwd is not None else Path.cwd()
        self._cwd = selected_cwd.resolve()

    @property
    @override
    def repo_root(self) -> Path | None:
        """Expose the preset catalog root as the backend root."""
        return self._repo_root

    @property
    @override
    def cwd(self) -> Path:
        """Expose the caller working directory for renderers."""
        return self._cwd

    def _preset_group_dirs(self) -> list[Path]:
        """List preset groups that may contain fixture pairs."""
        if not self.presets_root.exists():
            return []
        return sorted(
            path for path in self.presets_root.iterdir() if path.is_dir()
        )

    def _fixture_pairs_for_group(
        self, group_name: str
    ) -> list[tuple[Path, Path | None, Path | None]]:
        """List each valid fixture directory with its old and new files.

        One listing globs every fixture directory exactly once; validity means
        at most one old.* and at most one new.* file, with at least one of them
        present. A fixture holding only one side is a File the comparison adds
        or deletes, which is an ordinary shape a preset must be able to
        express. Callers reuse the returned pairs instead of re-globbing per
        fixture, which measured 84 directory traversals for a single preset
        repo_diff.
        """
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
        """List groups that contain at least one usable fixture pair."""
        return [
            group_dir.name
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def list_preset_groups(self) -> list[PresetGroup]:
        """Build catalog entries for the preset picker."""
        return [
            {
                "id": group_dir.name,
                "display_name": group_dir.name.replace("-", " ").title(),
            }
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def default_preset_name(self) -> str:
        """Choose the first available preset group for initial UI state."""
        names = self._list_preset_names()
        if names == []:
            raise DirdiffError(f"No presets found in {self.presets_root}.")
        return names[0]

    def _preset_group_shape(self, preset_name: str) -> str:
        """Validate a group name's shape and existence without listing it.

        The blank name selects the default group. Fixture availability is a
        separate concern: callers that go on to list the group check the
        listing they already need instead of paying a second directory scan.
        """
        normalized = preset_name.strip()
        if normalized == "":
            normalized = self.default_preset_name()
        candidate = PurePosixPath(normalized)
        if candidate.is_absolute():
            raise DirdiffError("Preset name must be preset-relative.")
        if normalized.startswith("../") or normalized == "..":
            raise DirdiffError("Preset name must stay inside the presets root.")
        if len(candidate.parts) != 1:
            raise DirdiffError("Preset name must be a top-level group.")
        preset_dir = self.presets_root / normalized
        if not preset_dir.is_dir():
            raise DirdiffError(f"Unknown preset: {normalized}")
        return normalized

    def _preset_group_name(self, preset_name: str) -> str:
        """Validate and normalize a user-selected preset group name."""
        normalized = self._preset_group_shape(preset_name)
        if self._fixture_pairs_for_group(normalized) == []:
            raise DirdiffError(f"Preset group has no fixtures: {normalized}")
        return normalized

    def _preset_pair(self, preset_dir: Path) -> tuple[Path | None, Path | None]:
        """Return the old/new fixture files for one preset directory.

        Either side may be absent, which is a fixture describing an added or a
        deleted File. Both absent is not a fixture at all, and two files on one
        side name no single old or new version, so both are rejected here under
        the same rule the group listing skips them by.
        """
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
        """Return a preset File's reason and complete metadata content.

        `repository_path` must identify one old/new fixture inside this preset
        catalog. Absence of `preset.toml` returns `None`; malformed metadata is
        rejected through the same backend error contract as path listing.

        This is the fixture-level `preset.toml`, which states a `lazy_reason`.
        The catalog directory above holds one too, stating a `name`, and
        `preset_catalogs()` is what reads that one.
        """
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

    @override
    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize preset side names where the left side is the group name."""
        side = raw_side.strip()
        if side == "new":
            return side
        return self._preset_group_name(side)

    @override
    def discover_default_path(self) -> str:
        """Pick the first fixture's path for single-file startup mode.

        The old side when the fixture has one, and the new side otherwise: a
        fixture describing an addition has no old file to open, and startup
        must still name a File that exists.
        """
        preset_group = self.default_preset_name()
        preset_dir, old_path, new_path = self._fixture_pairs_for_group(
            preset_group
        )[0]
        side_path = old_path if old_path is not None else new_path
        assert side_path is not None, (
            "a listed fixture has at least one present side"
        )
        return f"{preset_group}/{preset_dir.name}/{side_path.name}"

    @override
    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str, str]:
        """Reject branch-review resolution for preset-backed fixtures."""
        raise DirdiffError("Preset backend does not support branch review.")

    @override
    def repo_diff(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> RepoDiff:
        """Return each fixture pair and explicitly absent aggregate totals.

        A fixture holding both sides is a modification, one holding only a
        new.* file is an addition, and one holding only an old.* file is a
        deletion. The change type is read from which files exist rather than
        declared anywhere, so a fixture directory is the whole statement of
        what the comparison does to that File.
        """
        # Shape validation only: the fixture listing below is the single
        # directory scan, and it doubles as the emptiness check the full
        # group validation would otherwise repeat.
        normalized_left = (
            left if left == "new" else self._preset_group_shape(left)
        )
        if right != "new":
            raise DirdiffError(
                "Preset diffs compare a preset's old.* and new.* files."
            )
        pairs = self._fixture_pairs_for_group(normalized_left)
        if pairs == []:
            raise DirdiffError(
                f"Preset group has no fixtures: {normalized_left}"
            )
        entries: list[RepoDiffPath] = []
        for preset_dir, old_path, new_path in pairs:
            prefix = f"{normalized_left}/{preset_dir.name}"
            left_side = (
                None if old_path is None else f"{prefix}/{old_path.name}"
            )
            right_side = (
                None if new_path is None else f"{prefix}/{new_path.name}"
            )
            # The File is named by the side it ends on, and by the side it
            # started on when the comparison deletes it. Metadata is read from
            # the same path: it lives beside both sides in the fixture
            # directory, so either one reaches it.
            display_name = right_side if right_side is not None else left_side
            assert display_name is not None, (
                "a listed fixture has at least one present side"
            )
            lazy_reason = self.lazy_reason_metadata(display_name)
            entries.append(
                RepoDiffPath(
                    left_path=left_side,
                    right_path=right_side,
                    display_name=display_name,
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
                )
            )
        assert not show_untracked, "preset diffs do not support untracked Files"
        return RepoDiff(tuple(entries), None, None)

    @override
    def normalize_repo_path(self, raw_path: str) -> str:
        """Validate the <group>/<fixture>/<file> path shape used by presets."""
        if raw_path.strip() == "":
            raise DirdiffError("Preset path is required.")
        if raw_path.endswith("/"):
            raise DirdiffError("Preset path must point to a file.")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise DirdiffError("Use a preset-relative path.")
        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise DirdiffError("Preset path must stay inside the presets root.")
        parts = candidate.parts
        if len(parts) != 3:
            raise DirdiffError(
                "Preset path must look like <group>/<preset>/<old-or-new-file>."
            )
        preset_dir = self.presets_root / candidate.parent
        if not preset_dir.is_dir():
            raise DirdiffError(f"Unknown preset path: {normalized}")
        return normalized

    @override
    def load_version(self, path: str, side: SideName) -> bytes:
        """Return exact contents for one present old/new preset fixture file.

        A fixture listed by the preset catalog but absent at load time raises
        `DirdiffError`; absence is represented by an absent manifest side.
        """
        normalized_path = self.normalize_repo_path(path)
        # Resolve the manifest path to its on-disk fixture: a direct file when
        # the path exists as written, otherwise the old/new member of its
        # fixture pair matching the file name. `side` does not participate:
        # preset manifest paths already name their exact side-specific file.
        file_path = self.presets_root / normalized_path
        if not file_path.is_file():
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
        self, requests: tuple[tuple[str, SideName], ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load fixture sides in order while retaining individual failures."""
        results: list[bytes | DirdiffError] = []
        for path, side in requests:
            try:
                results.append(self.load_version(path, side))
            except DirdiffError as exc:
                results.append(exc)
        return tuple(results)
