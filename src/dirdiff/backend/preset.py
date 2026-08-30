"""Read preset fixture catalogs through the backend contract.

## Public interface

`preset_catalogs` lists the catalogs available below one presets root.
`PresetCatalogDir` carries each catalog's id, label, and directory.
`PresetBackend` lists one selected catalog's fixture groups and exposes their
old/new files through `WorkspaceBackendProtocol`.

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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Never, TypeIs, override

from dirdiff.backend.base import (
    SYMLINK_MODE,
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
    boundary. A caller constructs `PresetBackend` with `root` to inspect the
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
    """Catalog directory path used to construct `PresetBackend`.

    Discovery preserves the `presets_root` spelling supplied by its caller;
    `PresetBackend` resolves the path when constructed. The record does not
    promise the directory remains available after the catalog scan.
    """


def preset_catalogs(presets_root: Path) -> tuple[PresetCatalogDir, ...]:
    """List the catalogs under `presets_root`, ordered by id.

    Every directory there is a catalog and must state its display name in a
    `preset.toml` holding exactly `name`. A directory without that file, or
    with anything else in it, raises `DirdiffError` naming the directory rather
    than being skipped: a mistyped key would otherwise delete a catalog from
    the picker without saying so. A presets root that does not exist has no
    catalogs, which is the true answer when dirdiff runs outside this
    repository.

    # Usage

    The server calls this on each catalog-listing operation, then constructs a
    `PresetBackend` from the selected record's `root`. Adding a valid catalog
    therefore requires no server restart.

    A fixture directory may also hold a `preset.toml`, with a `lazy_reason` key
    and no `name`. The two live at different depths and are read by different
    callers; `PresetBackend.lazy_reason_metadata` reads the other one.

    # Returns

    - Each item contains one immediate directory's `catalog_id`, configured
      display name, and root path suitable for constructing `PresetBackend`.
    - Items are ordered by `catalog_id`. An empty tuple means the presets root
      is absent or contains no catalog directories; fixtures are never expanded.

    # Failures

    A missing presets root returns an empty tuple. Missing, malformed, or
    wrongly shaped catalog metadata raises `DirdiffError`, `TOMLDecodeError`, or
    a filesystem decoding error; invalid catalogs are not skipped.
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
    """Expose one preset catalog through `WorkspaceBackendProtocol`.

    # Usage

    Construct one backend from the `root` returned by `preset_catalogs`. Use a
    group id as the left side and `new` as the right side, then follow the same
    `repo_diff` and loading flow used for other workspace backends.

    The backend reads one catalog and does not enumerate its siblings. Presets
    have no repository refs or aggregate line counts.
    """

    def __init__(self, presets_root: Path, *, cwd: Path | None = None) -> None:
        """Bind this backend to one preset catalog and command directory.

        # Parameters

        - `presets_root`: Catalog directory containing fixture groups.
        - `cwd`: Stable command directory, defaulting to the process directory.

        # Usage

        Pass `PresetCatalogDir.root` for the catalog selected by the caller.

        # Failures

        Construction normalizes both paths but does not read the catalog.
        Filesystem resolution failures propagate.
        """
        self.presets_root = presets_root.expanduser().resolve()
        self._repo_root = self.presets_root
        selected_cwd = cwd if cwd is not None else Path.cwd()
        self._cwd = selected_cwd.resolve()

    @property
    @override
    def repo_root(self) -> Path | None:
        """Return the stored absolute preset catalog root without filesystem work.

        Preset backends always have a root even when the directory later becomes
        unavailable. Catalog reads report that state through their own contract.

        # Usage

        Read this when Snapshot storage must stay outside fixture input. Access
        performs no filesystem work and has no expected failure.

        # Returns

        - The absolute catalog root retained at construction. This concrete
          backend always has one.
        - `None`: Never returned by `PresetBackend`; the shared protocol allows
          it only for a backend not bound to a repository-like root.
        """
        return self._repo_root

    @property
    @override
    def cwd(self) -> Path:
        """Return the stored absolute command directory without filesystem work.

        It is fixed at construction and does not change when fixture paths are
        read from the preset catalog.

        # Usage

        External renderers may reuse this command directory. Access performs no
        filesystem work and has no expected failure.
        """
        return self._cwd

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
        present. A fixture holding only one side is a File the comparison adds
        or deletes, which is an ordinary shape a preset must be able to
        express. Callers reuse the returned pairs instead of re-globbing per
        fixture, which measured 84 directory traversals for a single preset
        repo_diff.

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

        # Failures

        A missing catalog returns an empty list. Filesystem iteration failures
        propagate; invalid or empty fixture directories do not become groups.
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

        An empty or unavailable catalog raises `DirdiffError` rather than
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
        rejected through the same backend error contract as path listing.

        # Usage

        `repo_diff` calls this with the present side path while building each
        `RepoDiffPath`. A caller that receives a value must retain both the
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

        Absence returns `None`. Invalid paths, unreadable metadata, malformed
        TOML, extra keys, and unsupported reasons raise rather than being ignored.
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
        """Normalize the built-in right side or one selectable left-side group.

        # Usage

        Normalize the selected group and literal `new` before calling
        `repo_diff` on this same instance.

        # Failures

        Unknown, path-shaped, or empty groups with no default raise
        `DirdiffError`. The built-in `new` side passes through unchanged.
        """
        side = raw_side.strip()
        if side == "new":
            return side
        return self._preset_group_name(side)

    @override
    def discover_default_path(self) -> str:
        """Pick the first fixture's path for single-file startup mode.

        # Usage

        Single-File startup calls this when no fixture path was supplied, then
        passes the result back through `normalize_repo_path`.

        The old side when the fixture has one, and the new side otherwise: a
        fixture describing an addition has no old file to open, and startup
        must still name a File that exists.

        # Failures

        An unavailable or empty catalog raises `DirdiffError`; filesystem
        iteration failures propagate.
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
    ) -> Never:
        """Reject branch-review resolution for preset-backed fixtures.

        # Parameters

        - `base_selection`: Unused branch base supplied through the shared protocol.
        - `review_selection`: Unused review branch supplied through the protocol.

        # Usage

        Do not call this method on `PresetBackend`. Callers must select a Git
        backend before offering Branch Review or resolving its branch sides.

        # Failures

        Presets have no repository refs, so this always raises `DirdiffError`.
        """
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

        # Parameters

        - `left`: Normalized preset group whose `old.*` sides are compared.
        - `right`: Must be the normalized built-in preset side `new`.
        - `show_untracked`: Must be false because presets have no VCS status.

        # Usage

        Pass a normalized group as `left` and `new` as `right`. Snapshot capture
        loads the present path of every returned fixture pair.

        # Failures

        Invalid groups, fixture shapes, or metadata raise `DirdiffError` or a
        parsing/filesystem exception. A right side other than `new` raises
        `DirdiffError`; `show_untracked=True` violates an assertion. Aggregate
        line totals are always absent.
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
                    left_mode=(
                        None
                        if old_path is None
                        else self.file_mode(f"{prefix}/{old_path.name}", left)
                    ),
                    right_mode=(
                        None
                        if new_path is None
                        else self.file_mode(f"{prefix}/{new_path.name}", right)
                    ),
                )
            )
        assert not show_untracked, "preset diffs do not support untracked Files"
        return RepoDiff(tuple(entries), None, None)

    @override
    def normalize_repo_path(self, raw_path: str) -> str:
        """Validate a catalog-relative `<group>/<fixture>/<file>` path.

        # Usage

        Pass catalog paths returned by `repo_diff` or formed while following a
        fixture link before direct loading.

        # Failures

        Blank, absolute, parent-prefixed, directory-shaped, wrongly deep, and
        unknown fixture-directory paths raise `DirdiffError`. This shape check
        does not prove the final filename exists; `load_version` checks it
        against the fixture pair.
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
    def file_mode(self, path: str, side: SideName) -> str:
        """Return one fixture path's Git-compatible File mode without following links.

        The normalized path may name the old/new fixture itself or an auxiliary
        target reached from a fixture link. `side` is retained by the shared
        backend interface; the preset path already identifies its exact bytes.

        # Parameters

        - `path`: Normalized fixture or auxiliary-target path to inspect.
        - `side`: Preset side retained for the shared backend interface.

        # Failures

        Raises `DirdiffError` when the path is missing, directory-shaped, or not
        a regular file or symbolic link inside the selected fixture directory.
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
            return SYMLINK_MODE
        if not stat.S_ISREG(mode):
            raise DirdiffError(
                f"Preset path is not a regular file or symbolic link: {normalized}"
            )
        return "100755" if mode & stat.S_IXUSR else "100644"

    @override
    def file_size(self, path: str, side: SideName) -> int:
        """Return exact fixture content size without loading its bytes.

        The normalized path may name an old/new fixture or an auxiliary target
        reached from a fixture link. Filesystem inspection does not follow
        links; link size is the byte length of its raw target payload. `side`
        remains present only for the shared backend interface.

        # Parameters

        - `path`: Normalized fixture or auxiliary-target path to inspect.
        - `side`: Preset side retained for the shared backend interface.

        # Failures

        Missing paths, unsupported File kinds, and filesystem inspection
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

        Load a present path returned by this backend or reached through a
        fixture link. `side` does not select a different fixture File.

        # Failures

        Invalid or missing fixture paths and unreadable bytes raise
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
        self, requests: tuple[tuple[str, SideName], ...]
    ) -> tuple[bytes | DirdiffError, ...]:
        """Load fixture sides in order while retaining individual failures.

        # Usage

        Snapshot capture supplies present `(path, side)` pairs and consumes the
        result by position.

        # Returns

        - One result position per `(path, side)` input, preserving input order.
        - A `bytes` item is the fixture's exact File content at that position.
        - A `DirdiffError` item describes only that fixture-side failure;
          successful sibling results remain available.

        # Failures

        Each `DirdiffError` occupies the result position of its own input.
        Unexpected parsing and filesystem exceptions abort the complete batch.
        """
        results: list[bytes | DirdiffError] = []
        for path, side in requests:
            try:
                results.append(self.load_version(path, side))
            except DirdiffError as exc:
                results.append(exc)
        return tuple(results)
