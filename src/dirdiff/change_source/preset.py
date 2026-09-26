"""Prepare preset catalog choices and observe selected fixture Files.

## Public interface

`preset_catalogs` lists the catalogs available below one presets root.
`PresetCatalogDir` carries each catalog's id, label, and directory.
`PresetCatalog` lists selectable groups. `PresetChangeSource` reads one selected
group through `ChangeSourceProtocol.changes` and `ChangeSourceProtocol.load`.

## Purpose and boundaries

Presets exercise the normal manifest, format, and rendering paths without a Git
repository. This module interprets only the directory layout and fixture-level
metadata. It does not choose a catalog, render fixture contents, or keep a
catalog list between calls.
"""

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
"""Complete lazy-reason vocabulary accepted from fixture `preset.toml` files.

Membership in this tuple is the runtime validation that lets
`_is_lazy_reason` narrow untyped TOML values to `LazyReason`. Keep it aligned
with that public literal type.
"""


def _is_lazy_reason(value: object) -> TypeIs[LazyReason]:
    """Narrow untyped preset metadata to a supported lazy reason.

    TOML values enter as `Any`, so membership validation must also provide the
    static type boundary promised by preset metadata parsing.
    """
    return isinstance(value, str) and value in _LAZY_REASONS


@dataclass(frozen=True)
class PresetCatalogDir:
    """Describe one preset catalog discovered below the presets root.

    Catalog discovery returns these records to the picker and manifest
    boundary. A caller constructs `PresetCatalog` with `root` to inspect the
    catalog's groups and fixtures.

    This is directory metadata. It holds no group listing, no fixture, and no
    rendered content.
    """

    catalog_id: str
    """Directory name that identifies this catalog across UI and Room state.

    Callers use the exact value to select `root`; the presentation name is not a
    substitute identity.
    """

    name: str
    """Non-empty picker label read from the catalog-level `preset.toml`.

    It is presentation metadata and may change without renaming the catalog id.
    """

    root: Path
    """Catalog directory path used to construct `PresetCatalog`.

    Discovery preserves the `presets_root` spelling supplied by its caller;
    `PresetCatalog` resolves the path when constructed. The record does not
    promise the directory remains available after the catalog scan.
    """


def preset_catalogs(presets_root: Path) -> tuple[PresetCatalogDir, ...]:
    """List the currently available catalogs beneath a configured presets root.

    # Parameters

    - `presets_root`: Directory whose immediate subdirectories are preset catalogs.
      Each catalog must have `preset.toml` containing exactly a nonblank `name`.

    # Usage

    Call for each catalog-picker refresh. Choose a returned record and construct
    `PresetCatalog` from its `root`; then use that catalog's group listing to
    construct `PresetChangeSource`. Added catalogs appear on the next call.

    # Returns

    Catalog records ordered by `catalog_id`. An absent root or one without
    catalog directories returns an empty tuple. Each record provides:

    - `catalog_id`: Exact selection value for this catalog.
    - `name`: Configured label for the catalog picker.
    - `root`: Directory passed to `PresetCatalog` for group selection.

    Fixture pairs are discovered separately by the selected source.

    # Failures

    - Missing catalog metadata or unsupported keys or names raises `DirdiffError`.
    - Invalid TOML raises `TOMLDecodeError`.
    - Filesystem and text-decoding failures propagate.
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


class _PresetWorkspace(WorkspaceStorageProtocol):
    """Supply private fixture storage for one selected catalog.

    `PresetCatalog` uses this to list groups; `PresetChangeSource` uses it for
    discovery and shared side loading. A group id selects fixture discovery,
    while each canonical catalog-local path identifies its exact side data.
    This object does not enumerate sibling catalogs, expose repository refs,
    or compute aggregate line counts.
    """

    def __init__(self, presets_root: Path) -> None:
        """Bind fixture storage to one absolute catalog root."""
        self.presets_root = presets_root.expanduser().resolve()

    def _preset_group_dirs(self) -> list[Path]:
        """List immediate catalog directories in stable path order.

        A missing catalog produces an empty list. Files and deeper descendants
        are not group candidates.
        """
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
        present. A fixture holding only one side is a File the Tab adds or
        deletes.

        *Implementation detail: callers reuse this listing to avoid repeating
        directory traversal for each fixture side.*

        # Returns

        - In each tuple, the first item is the fixture directory that gives the
          pair its identity and ordering key.
        - The second item is its sole `old.*` File, or `None` for an addition.
        - The third item is its sole `new.*` File, or `None` for a deletion.
          At least one File path is present.
        - The list follows fixture-directory order and omits directories with
          no sides or more than one candidate on either side.
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
        """Return ids for groups that contain at least one usable fixture.

        Group order follows the sorted catalog directory listing. Invalid or
        empty fixture directories do not make a group selectable.
        """
        return [
            group_dir.name
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def list_preset_groups(self) -> list[PresetGroup]:
        """Build picker records for groups containing usable fixtures.

        # Usage

        The preset catalog endpoint publishes these records. Callers must send
        the returned `id`, not `display_name`, when selecting a group.

        # Returns

        Groups containing usable fixtures, in catalog order. A missing catalog
        returns an empty list. Invalid or empty fixture directories do not
        become groups.

        # Failures

        - Filesystem iteration failures propagate.
        """
        return [
            {
                "id": group_dir.name,
                "display_name": group_dir.name.replace("-", " ").title(),
            }
            for group_dir in self._preset_group_dirs()
            if self._fixture_pairs_for_group(group_dir.name)
        ]

    def default_preset_name(self) -> str:
        """Choose the first selectable group in stable catalog order.

        # Usage

        The catalog endpoint uses this as its initial group id, and blank side
        normalization delegates here.

        # Failures

        - An empty or unavailable catalog raises `DirdiffError` rather than
          returning a group that cannot produce a diff.
        """
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
        """Return a safe top-level group id that contains usable fixtures.

        Blank input selects the default. Invalid path shapes, missing groups, and
        groups without fixture pairs raise `DirdiffError`.
        """
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

        # Returns

        - First, the old File path, or `None` when the fixture is an addition.
        - Second, the new File path, or `None` when the fixture is a deletion.
          At least one item is always present.
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
        rejected through the same source error contract as path listing.

        # Usage

        `list_changes` calls this with the present side path while building each
        `ListedPath`. A caller that receives a value must retain both the
        narrowed reason and the exact metadata text.

        This is the fixture-level `preset.toml`, which states a `lazy_reason`.
        The catalog directory above holds one too, stating a `name`, and
        `preset_catalogs()` is what reads that one.

        # Returns

        - First, the validated lazy reason read from the fixture metadata.
        - Second, the complete metadata file text retained with that reason.
        - `None`: The fixture has no metadata file and therefore no
          preset-specific lazy policy.

        # Failures

        - Invalid paths, unreadable metadata, malformed TOML, extra keys, and
          unsupported reasons raise rather than being ignored.
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

    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize the built-in right side or one selectable left-side group.

        # Usage

        Normalize the selected group and literal `new` before calling
        `list_changes` on this same instance.

        # Returns

        - The built-in `new` side passes through unchanged.
        - A group name identifies a validated fixture group.

        # Failures

        - Unknown, path-shaped, or empty groups with no default raise
          `DirdiffError`.
        """
        side = raw_side.strip()
        if side == "new":
            return side
        return self._preset_group_name(side)

    def list_changes(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> ListedChanges:
        """Return each fixture pair and explicitly absent aggregate totals.

        A fixture holding both sides is a modification, one holding only a
        new.* file is an addition, and one holding only an old.* file is a
        deletion. The change type is read from which files exist rather than
        declared anywhere, so a fixture directory is the whole statement of
        what the Tab does to that File.

        # Parameters

        - `left`: Normalized preset group whose `old.*` sides are compared.
        - `right`: Must be the normalized built-in preset side `new`.
        - `show_untracked`: Must be false because presets have no VCS status.

        # Usage

        Pass a normalized group as `left` and `new` as `right`. `SourceFiles`
        acquires the present sides to establish this source observation.

        # Returns

        - `ListedChanges.paths` contains the usable fixture pairs in directory
          order. Ambiguous or empty fixture directories are omitted.
        - `ListedChanges.added_lines` and `removed_lines` are absent.

        *Implementation detail: presets do not calculate aggregate line totals.*

        # Failures

        - Unknown groups or groups without usable fixtures raise `DirdiffError`.
        - Invalid fixture policy metadata raises a parsing or filesystem
          exception, or `DirdiffError` for invalid keys or reasons.
        - A right side other than `new` raises `DirdiffError`.
        - `show_untracked=True` violates an assertion.
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
        entries: list[ListedPath] = []
        for preset_dir, old_path, new_path in pairs:
            prefix = f"{normalized_left}/{preset_dir.name}"
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
                            f"{prefix}/{old_path.name}", left
                        )
                    ),
                    right_properties=(
                        None
                        if new_path is None
                        else self.file_properties(
                            f"{prefix}/{new_path.name}", right
                        )
                    ),
                )
            )
        assert not show_untracked, "preset diffs do not support untracked Files"
        return ListedChanges(tuple(entries), None, None)

    @override
    def normalize_repo_path(self, raw_path: str) -> str:
        """Validate a catalog-relative `<group>/<fixture>/<file>` path.

        # Usage

        Pass catalog paths returned by `list_changes` or formed while following a
        fixture link before direct loading. This shape check does not prove
        the final filename exists; `load_version` checks it against the
        fixture pair.

        # Failures

        - Blank, absolute, parent-prefixed, directory-shaped, wrongly deep,
          and unknown fixture-directory paths raise `DirdiffError`.
        """
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
        """Return one fixture path's semantic properties without following links.

        The normalized path may name the old/new fixture itself or an auxiliary
        target reached from a fixture link. `side` is retained by the shared
        backend interface; the preset path already identifies its exact bytes.

        # Parameters

        - `path`: Normalized fixture or auxiliary-target path to inspect.
        - `side`: Preset side retained for the shared storage contract.

        # Failures

        - Raises `DirdiffError` when the path is missing, directory-shaped, or
          not a regular file or symbolic link inside the selected fixture
          directory.
        """
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
        """Return exact fixture content size without loading its bytes.

        The normalized path may name an old/new fixture or an auxiliary target
        reached from a fixture link. Filesystem inspection does not follow
        links; link size is the byte length of its raw target payload. `side`
        remains present only for the shared storage contract.

        # Parameters

        - `path`: Normalized fixture or auxiliary-target path to inspect.
        - `side`: Preset side retained for the shared storage contract.

        # Failures

        - Missing paths, unsupported File kinds, and filesystem inspection
          failures raise `DirdiffError`.
        """
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
        """Return exact contents for one present preset fixture File.

        The path may name a listed old/new side or an auxiliary File reached by
        a link. A missing File raises `DirdiffError`; absent manifest sides are
        represented by absent paths and are never loaded.

        # Parameters

        - `path`: Normalized fixture path that names the exact File to load.
        - `side`: Shared-protocol side name; the path, not this value, selects bytes.

        # Usage

        Load a present path returned by this catalog storage or reached through a
        fixture link. `side` does not select a different fixture File.

        # Failures

        - Invalid or missing fixture paths and unreadable bytes raise
          `DirdiffError`; malformed fixture shape is also rejected.
        """
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
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load fixture sides in order while retaining individual failures.

        # Usage

        Source observation supplies `dirdiff.change_source.base.StorageFile` values and consumes results
        by position. Preset paths identify their concrete fixture sides; they
        have no immutable storage object id.

        # Returns

        - One result position per private side input, preserving input order.
        - A `bytes` item is the fixture's exact File content at that position.
        - A `DirdiffError` item describes only that fixture-side failure;
          successful sibling results remain available.

        # Failures

        - Each `DirdiffError` occupies the result position of its own input.
        - Unexpected parsing and filesystem exceptions abort the complete
          batch.
        """
        results: list[bytes | DirdiffError] = []
        for path, side, _properties, _object_id in sides:
            try:
                results.append(self.load_version(path, side))
            except DirdiffError as exc:
                results.append(exc)
        return tuple(results)


class PresetCatalog:
    """Offer the selectable fixture groups in one preset catalog.

    # Usage

    Call `preset_catalogs` with the configured presets root, choose a catalog record,
    and construct this object with that record's `root`. `list_preset_groups`
    provides ids and labels for the group picker. `default_preset_name` supplies a
    usable initial group when the picker has no selection.

    Construct `PresetChangeSource` with this catalog's `root` and the selected group
    id. Listing choices does not establish a source observation; the source's
    `changes()` call does that.
    """

    root: Path
    """Stable absolute catalog path used for source construction.

    Reading it does no filesystem work. Fixture availability is checked by
    listing or loading operations after construction.
    """

    def __init__(self, root: Path) -> None:
        """Bind group discovery to the selected catalog root.

        # Parameters

        - `root`: Directory from `preset_catalogs`, containing group
          directories. Construction normalizes it but does not list fixtures.
        """
        self.root = root.expanduser().resolve()
        self._workspace = _PresetWorkspace(self.root)

    def list_preset_groups(self) -> list[PresetGroup]:
        """Return currently selectable group ids and display labels.

        # Usage

        Show each result's display name in the picker and retain its exact id for
        `PresetChangeSource` construction. Call again to refresh directory choices.

        # Returns

        Groups in stable directory order. An absent catalog returns an empty list.
        Groups without usable fixture pairs are omitted. Catalog labels are read by
        `preset_catalogs`; this method only lists groups.

        # Failures

        - Filesystem listing failures propagate.
        """
        return self._workspace.list_preset_groups()

    def default_preset_name(self) -> str:
        """Return a usable group id for picker initialization.

        # Usage

        Use when no group has been selected. Preserve an existing selection rather than
        replacing it with this initial choice.

        # Returns

        The first available group id in the same directory order as
        `list_preset_groups`. Pass it as `PresetChangeSource`'s `group`.

        # Failures

        - An empty or unavailable catalog raises `DirdiffError`.
        """
        return self._workspace.default_preset_name()


class PresetChangeSource(ChangeSourceProtocol):
    """Observe one fixture group and supply its acquired data for capture.

    Implements `dirdiff.change_source.ChangeSourceProtocol`. See that protocol
    for observing Files, loading their paired data, and source lifetime.

    # Usage

    Use the catalog root and exact group id selected by the preset picker:

    ```python
    from dirdiff.change_source import PresetChangeSource

    source = PresetChangeSource(
        catalog.root,
        group=selected_group["id"],
        max_link_target_bytes=1024 * 1024,
    )
    changes = source.changes()
    ```

    Here `catalog` is an existing `PresetCatalog` and `selected_group` is one
    of its `list_preset_groups()` results.

    # Fixture observation

    `changes()` acquires fixture bytes and link facts to establish complete
    identities. A later fixture edit or removal cannot change this observation.
    Lazy policy is fixed with the same observation. A fresh source is required
    to observe fixture or policy edits.
    """

    def __init__(
        self, catalog_root: Path, *, group: str, max_link_target_bytes: int
    ) -> None:
        """Bind one selected catalog group and reached-link policy.

        # Parameters

        - `catalog_root`: Selected catalog directory from `preset_catalogs`.
        - `group`: Exact group id selected by the picker.
        - `max_link_target_bytes`: Nonnegative size limit for the reached
          non-link target, checked before and after reading. Outer content and
          raw link payloads are unbounded.

        # Usage

        Construction normalizes the root without acquiring fixture data.
        `changes()` validates and observes the selected group.

        # Failures

        - A negative bound raises `AssertionError`.
        - Filesystem path-resolution failures propagate.
        """
        assert max_link_target_bytes >= 0
        self._workspace = _PresetWorkspace(catalog_root)
        self._group = group
        self._max_link_target_bytes = max_link_target_bytes
        self._files: SourceFiles | None = None

    @override
    def changes(self) -> WorkspaceChanges:
        """Observe usable fixture pairs and acquire their side data once.

        # Usage

        Call before `load()`. Ambiguous fixture directories with multiple
        candidates for a side, and directories with no sides, are skipped.
        Each accepted fixture supplies its source lazy policy alongside the
        acquired side data.

        # Returns

        A `dirdiff.change_source.WorkspaceChanges` in fixture presentation order.
        Both aggregate totals are absent. Repeated successful calls return the
        same observation.

        *Implementation detail: fixtures have `untracked=False` because VCS
        tracking does not apply to them.*

        # Failures

        - A missing group or one without usable fixtures raises
          `dirdiff.engines.DirdiffError`.
        - Invalid policy TOML fails before any partial observation returns.
        - Expected outer read failures belong to their individual sides;
          readable links with unreachable targets retain a target diagnosis.
        - Unexpected failures propagate.
        """
        if self._files is None:
            self._files = SourceFiles(
                self._workspace,
                self._workspace.list_changes(left=self._group, right="new"),
                left=self._group,
                right="new",
                identity_namespace=b"preset-materialized-bytes-v1-links-v1",
                max_link_target_bytes=self._max_link_target_bytes,
            )
        return self._files.changes

    @override
    def load(self, files: Sequence[FileChange]) -> Sequence[LoadedFile]:
        """Return the fixture data acquired by this observation without rereading.

        # Parameters

        - `files`: Exact File records from this source's successful `changes()`;
          membership and ordering follow
          `dirdiff.change_source.ChangeSourceProtocol.load`.

        # Usage

        A fixture edit or removal after observation cannot replace the acquired
        data. Construct a new source for a later capture instead of retrying
        this observation.

        # Returns

        A sequence of `dirdiff.change_source.LoadedFile` in input order, each
        retaining its exact observed File and named side outcomes. Present sides
        retain these content outcomes:

        - `bytes` for a successful outer read, with any acquired link facts.
        - `dirdiff.engines.DirdiffError` for an expected outer read failure,
          with no link facts.

        Every present side outcome keeps its observed content identity, including
        failures.
        No additional fixture or policy reads occur here.

        # Failures

        - Calling before successful observation or passing foreign,
          reconstructed, or unlisted File records raises `AssertionError`.
        """
        assert self._files is not None, "changes() must precede load()"
        return self._files.load(files)
