from __future__ import annotations

from pathlib import Path, PurePosixPath

from dirdiff.sources.base import (
    RepoDiffPath,
    SideName,
    TextDiffError,
    TextVersion,
    WorkspaceBackend,
    _count_changed_line_stats,
)


class PresetBackend(WorkspaceBackend):
    def __init__(self, presets_root: Path, *, cwd: Path | None = None) -> None:
        self.presets_root = presets_root.expanduser().resolve()
        self._repo_root = self.presets_root
        self._cwd = (cwd or Path.cwd()).resolve()

    @property
    def repo_root(self) -> Path | None:
        return self._repo_root

    @property
    def cwd(self) -> Path:
        return self._cwd

    @classmethod
    def discover(
        cls,
        cwd: Path | None = None,
        *,
        presets_root: Path | None = None,
    ) -> PresetBackend:
        working_dir = (cwd or Path.cwd()).resolve()
        root = presets_root or working_dir / "tests" / "presets" / "difftastic"
        return cls(root, cwd=working_dir)

    def _preset_group_dirs(self) -> list[Path]:
        if not self.presets_root.exists():
            return []
        return sorted(
            path for path in self.presets_root.iterdir() if path.is_dir()
        )

    def _preset_dirs_for_group(self, group_name: str) -> list[Path]:
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
        return [
            group_dir.name
            for group_dir in self._preset_group_dirs()
            if self._preset_dirs_for_group(group_dir.name)
        ]

    def list_preset_groups(self) -> list[dict[str, object]]:
        return [
            {
                "name": group_dir.name,
                "display_name": group_dir.name.replace("-", " ").title(),
            }
            for group_dir in self._preset_group_dirs()
            if self._preset_dirs_for_group(group_dir.name)
        ]

    def default_preset_name(self) -> str:
        names = self._list_preset_names()
        if not names:
            raise TextDiffError(f"No presets found in {self.presets_root}.")
        return names[0]

    def _preset_group_name(self, preset_name: str) -> str:
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
        old_files = sorted(preset_dir.glob("old.*"))
        new_files = sorted(preset_dir.glob("new.*"))
        if len(old_files) != 1 or len(new_files) != 1:
            raise TextDiffError(
                f"Preset {preset_dir.name} must contain exactly one old.* and one new.* file."
            )
        return old_files[0], new_files[0]

    def _path_for_side(self, path: str, side: SideName) -> Path:
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
        side = raw_side.strip()
        if side == "new":
            return side
        return self._preset_group_name(side)

    def discover_default_path(self) -> str:
        preset_group = self.default_preset_name()
        preset_dir = self._preset_dirs_for_group(preset_group)[0]
        old_path, _ = self._preset_pair(preset_dir)
        return f"{preset_group}/{preset_dir.name}/{old_path.name}"

    def current_branch_name(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a current Git branch."
        )

    def list_branch_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git branches.")

    def list_remote_ref_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git remote refs.")

    def list_remote_names(self) -> list[str]:
        raise TextDiffError("Preset backend does not have Git remotes.")

    def list_ref_choices(self) -> dict[str, list[str]]:
        return {
            "builtins": [],
            "locals": [],
            "remotes": [],
            "remote_names": [],
        }

    def default_remote_name(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a default Git remote."
        )

    def branch_upstream_name(self, branch_name: str) -> str:
        raise TextDiffError(
            "Preset backend does not have Git branch upstreams."
        )

    def default_base_branch(self) -> str:
        raise TextDiffError(
            "Preset backend does not have a default base branch."
        )

    def preferred_review_branch(self, *, base_branch: str | None = None) -> str:
        raise TextDiffError("Preset backend does not support branch review.")

    def resolve_branch_diff_sides(
        self,
        *,
        base_branch: str,
        branch: str,
    ) -> tuple[str, str]:
        raise TextDiffError("Preset backend does not support branch review.")

    def list_repo_diff_paths(
        self,
        *,
        left: SideName,
        right: SideName,
        show_untracked: bool = False,
    ) -> list[RepoDiffPath]:
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
        normalized_path = self.normalize_repo_path(path)
        file_path = self._path_for_side(normalized_path, side)
        if not file_path.exists():
            return TextVersion(label=side, exists=False, text=None)
        return TextVersion(
            label=side,
            exists=True,
            text=file_path.read_text(encoding="utf-8"),
        )
