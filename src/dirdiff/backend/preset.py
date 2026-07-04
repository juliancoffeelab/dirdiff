"""Preset-backed implementation of ``WorkspaceBackendProtocol``.

``PresetBackend`` treats test preset directories as read-only backend data.  It lets
the same manifest and rendering paths exercise fixture pairs without requiring
a Git repository.  It should stay limited to preset discovery, path listing,
and file loading for those fixtures.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from dirdiff.backend.base import (
    BranchSelection,
    DefaultBaseSelection,
    RefChoices,
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackendProtocol,
    _count_changed_line_stats,
)

__all__ = [
    "PresetBackend",
]


class PresetBackend(WorkspaceBackendProtocol):
    """Read diff fixtures from a preset catalog through the backend interface."""

    def __init__(self, presets_root: Path, *, cwd: Path | None = None) -> None:
        """Bind this backend to one preset root and caller working directory."""
        self.presets_root = presets_root.expanduser().resolve()
        self._repo_root = self.presets_root
        self._cwd = (cwd or Path.cwd()).resolve()

    @property
    def repo_root(self) -> Path | None:
        """Expose the preset catalog root as the backend root."""
        return self._repo_root

    @property
    def cwd(self) -> Path:
        """Expose the caller working directory for renderers."""
        return self._cwd

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        presets_root: Path | None = None,
    ) -> PresetBackend:
        """Resolve the preset catalog root from arguments or test defaults."""
        working_dir = (cwd or Path.cwd()).resolve()
        root = presets_root or working_dir / "tests" / "presets" / "difftastic"
        return cls(root, cwd=working_dir)

    def _preset_group_dirs(self) -> list[Path]:
        """List preset groups that may contain fixture pairs."""
        if not self.presets_root.exists():
            return []
        return sorted(
            path for path in self.presets_root.iterdir() if path.is_dir()
        )

    def _preset_dirs_for_group(self, group_name: str) -> list[Path]:
        """List valid old/new fixture directories inside one preset group."""
        group_dir = self.presets_root / group_name
        if not group_dir.is_dir():
            raise TextDiffError(f"Unknown preset group: {group_name}")
        return sorted(
            path
            for path in group_dir.iterdir()
            if path.is_dir()
            and len(list(path.glob("old.*"))) == 1
            and len(list(path.glob("new.*"))) == 1
        )

    def _list_preset_names(self) -> list[str]:
        """List groups that contain at least one usable fixture pair."""
        return [
            group_dir.name
            for group_dir in self._preset_group_dirs()
            if self._preset_dirs_for_group(group_dir.name)
        ]

    def list_preset_groups(self) -> list[dict[str, object]]:
        """Build catalog entries for the preset picker."""
        return [
            {
                "name": group_dir.name,
                "display_name": group_dir.name.replace("-", " ").title(),
            }
            for group_dir in self._preset_group_dirs()
            if self._preset_dirs_for_group(group_dir.name)
        ]

    def default_preset_name(self) -> str:
        """Choose the first available preset group for initial UI state."""
        names = self._list_preset_names()
        if not names:
            raise TextDiffError(f"No presets found in {self.presets_root}.")
        return names[0]

    def _preset_group_name(self, preset_name: str) -> str:
        """Validate and normalize a user-selected preset group name."""
        normalized = preset_name.strip()
        if not normalized:
            normalized = self.default_preset_name()
        candidate = PurePosixPath(normalized)
        if candidate.is_absolute():
            raise TextDiffError("Preset name must be preset-relative.")
        if normalized.startswith("../") or normalized == "..":
            raise TextDiffError(
                "Preset name must stay inside the presets root."
            )
        if len(candidate.parts) != 1:
            raise TextDiffError("Preset name must be a top-level group.")
        preset_dir = self.presets_root / normalized
        if not preset_dir.is_dir():
            raise TextDiffError(f"Unknown preset: {normalized}")
        if not self._preset_dirs_for_group(normalized):
            raise TextDiffError(f"Preset group has no fixtures: {normalized}")
        return normalized

    def _preset_pair(self, preset_dir: Path) -> tuple[Path, Path]:
        """Return the old/new fixture files for one preset directory."""
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))
        if len(old_files) != 1 or len(new_files) != 1:
            raise TextDiffError(
                f"Preset {preset_dir.name} must contain exactly one old.* and one new.* file."
            )
        return old_files[0], new_files[0]

    def _path_for_side(self, path: str, side: SideName) -> Path:
        """Resolve a preset-relative manifest path to the requested side file."""
        normalized_path = self.normalize_repo_path(path)
        full_path = self.presets_root / normalized_path
        if full_path.is_file():
            return full_path

        preset_dir = self.presets_root / PurePosixPath(normalized_path).parent
        old_path, new_path = self._preset_pair(preset_dir)
        wanted_name = PurePosixPath(normalized_path).name
        if wanted_name == old_path.name:
            return old_path
        if wanted_name == new_path.name:
            return new_path
        raise TextDiffError(f"Preset file is missing: {normalized_path}")

    def normalize_side(self, raw_side: str) -> SideName:
        """Normalize preset side names where the left side is the group name."""
        side = raw_side.strip()
        if side == "new":
            return side
        return self._preset_group_name(side)

    def discover_default_path(self) -> str:
        """Pick the first old.* fixture path for single-file startup mode."""
        preset_group = self.default_preset_name()
        preset_dir = self._preset_dirs_for_group(preset_group)[0]
        old_path, _ = self._preset_pair(preset_dir)
        return f"{preset_group}/{preset_dir.name}/{old_path.name}"

    def current_branch_name(self) -> str:
        """Reject Git branch access for preset-backed fixtures."""
        raise TextDiffError(
            "Preset backend does not have a current Git branch."
        )

    def list_branch_names(self) -> list[str]:
        """Reject local branch listing for preset-backed fixtures."""
        raise TextDiffError("Preset backend does not have Git branches.")

    def list_remote_ref_names(self) -> list[str]:
        """Reject remote ref listing for preset-backed fixtures."""
        raise TextDiffError("Preset backend does not have Git remote refs.")

    def list_remote_names(self) -> list[str]:
        """Reject remote listing for preset-backed fixtures."""
        raise TextDiffError("Preset backend does not have Git remotes.")

    def list_ref_choices(self) -> RefChoices:
        """Return an empty ref-choice shape for preset-backed fixtures."""
        return {
            "builtins": [],
            "local_branches": [],
            "remotes": [],
            "remote_branches": [],
        }

    def branch_upstream_name(self, branch_name: str) -> str:
        """Reject upstream lookup for preset-backed fixtures."""
        raise TextDiffError(
            "Preset backend does not have Git branch upstreams."
        )

    def default_base_selection(self) -> DefaultBaseSelection:
        """Reject branch-review defaults for preset-backed fixtures."""
        raise TextDiffError(
            "Preset backend does not have a default base branch."
        )

    def preferred_review_selection(
        self, *, base_selection: DefaultBaseSelection | None = None
    ) -> BranchSelection:
        """Reject branch-review review defaults for preset-backed fixtures."""
        raise TextDiffError("Preset backend does not support branch review.")

    def resolve_branch_diff_sides(
        self,
        *,
        base_selection: BranchSelection,
        review_selection: BranchSelection,
    ) -> tuple[str, str, str]:
        """Reject branch-review resolution for preset-backed fixtures."""
        raise TextDiffError("Preset backend does not support branch review.")

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
        """Represent each old/new fixture pair as one modified repo path."""
        normalized_left = self.normalize_side(left)
        if right != "new":
            raise TextDiffError(
                "Preset diffs compare a preset's old.* and new.* files."
            )
        entries: list[RepoDiffPath] = []
        for preset_dir in self._preset_dirs_for_group(normalized_left):
            old_path, new_path = self._preset_pair(preset_dir)
            old_text = old_path.read_text(encoding="utf-8")
            new_text = new_path.read_text(encoding="utf-8")
            added, removed, replaced = _count_changed_line_stats(
                old_text,
                new_text,
            )
            entries.append(
                RepoDiffPath(
                    left_path=f"{normalized_left}/{preset_dir.name}/{old_path.name}",
                    right_path=(
                        f"{normalized_left}/{preset_dir.name}/{new_path.name}"
                    ),
                    display_name=(
                        f"{normalized_left}/{preset_dir.name}/{new_path.name}"
                    ),
                    change_type="modify",
                    changed_lines=added + removed + replaced,
                    added_lines=added + replaced,
                    removed_lines=removed + replaced,
                )
            )
        return entries

    def normalize_repo_path(self, raw_path: str) -> str:
        """Validate the <group>/<fixture>/<file> path shape used by presets."""
        if not raw_path.strip():
            raise TextDiffError("Preset path is required.")
        if raw_path.endswith("/"):
            raise TextDiffError("Preset path must point to a file.")
        candidate = PurePosixPath(raw_path)
        if candidate.is_absolute():
            raise TextDiffError("Use a preset-relative path.")
        normalized = candidate.as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise TextDiffError(
                "Preset path must stay inside the presets root."
            )
        parts = candidate.parts
        if len(parts) != 3:
            raise TextDiffError(
                "Preset path must look like <group>/<preset>/<old-or-new-file>."
            )
        preset_dir = self.presets_root / candidate.parent
        if not preset_dir.is_dir():
            raise TextDiffError(f"Unknown preset path: {normalized}")
        return normalized

    def load_version(self, path: str, side: SideName) -> TextVersion:
        """Load one old/new fixture file for a preset manifest path."""
        normalized_path = self.normalize_repo_path(path)
        file_path = self._path_for_side(normalized_path, side)
        if not file_path.exists():
            return TextVersion(label=side, exists=False, text=None)
        return TextVersion(
            label=side,
            exists=True,
            text=file_path.read_text(encoding="utf-8"),
        )
