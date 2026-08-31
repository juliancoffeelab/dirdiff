"""Build the bundled HUD and Preset corpus for standard dirdiff wheels.

## Public interface

Hatch discovers `CustomBuildHook` through the wheel target's custom-hook
configuration. A standard wheel build installs locked frontend dependencies,
compiles the HUD into a fresh temporary directory, validates its entry and asset
directory, and includes that directory as `dirdiff/frontend` package data. It
also copies the regular Preset fixtures into `dirdiff/tests/presets`, excluding
the approved symlink-backed cases. Editable wheels do no bundling work.

## Purpose and boundaries

This module exists at the repository root because Hatch requires an importable
build-hook file. The hook retains one temporary directory only for the lifetime
of a standard wheel build and disposes it afterward. It does not place compiled
files or copied Presets in the source package, reuse prior output, run frontend
tests or ESLint, or participate in installed runtime behavior.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import override

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface[BuilderConfig]):
    """Add a fresh HUD and filtered Preset corpus to standard wheels.

    Hatch constructs one hook for a wheel build. The instance retains the
    temporary output between `initialize`, when forced inclusion is configured,
    and `finalize`, after the wheel has consumed those files. No state crosses
    builds or reaches the installed package.
    """

    # The directory must outlive initialize because Hatch reads forced files
    # afterward. TemporaryDirectory also cleans it if artifact construction fails.
    _temporary_output: TemporaryDirectory[str] | None = None

    @override
    def initialize(self, version: str, build_data: dict[str, object]) -> None:
        """Build and register package data for one standard wheel build.

        `version` must be Hatch's `standard` or `editable` wheel variant.
        Editable builds return without invoking Bun or copying Presets. Standard
        builds propagate dependency installation, compilation, filesystem, and
        build-data contract failures.
        """

        if version == "editable":
            return
        assert version == "standard", f"unexpected wheel version: {version}"

        temporary_output = TemporaryDirectory(prefix="dirdiff-wheel-")
        self._temporary_output = temporary_output
        output_path = Path(temporary_output.name) / "frontend"
        presets_output_path = Path(temporary_output.name) / "presets"
        frontend_path = Path(self.root) / "frontend"
        presets_path = Path(self.root) / "tests" / "presets"
        environment = os.environ.copy()
        environment["DIRDIFF_FRONTEND_OUT_DIR"] = str(output_path)
        try:
            subprocess.run(
                ["bun", "install", "--frozen-lockfile"],
                cwd=frontend_path,
                check=True,
            )
            subprocess.run(
                ["bun", "run", "build"],
                cwd=frontend_path,
                env=environment,
                check=True,
            )
            index_path = output_path / "index.html"
            assets_path = output_path / "assets"
            assert index_path.is_file(), (
                f"frontend build did not produce an index: {index_path}"
            )
            assert assets_path.is_dir(), (
                f"frontend build did not produce assets: {assets_path}"
            )

            def ignored_preset_entries(
                directory: str,
                names: list[str],
            ) -> list[str]:
                """Exclude non-release Preset entries from the copied corpus."""

                relative_directory = Path(directory).relative_to(presets_path)
                ignored = {
                    name
                    for name in names
                    if (Path(directory) / name).is_symlink()
                    or name == ".ipynb_checkpoints"
                }
                if relative_directory == Path("formats"):
                    ignored.add("symlinks")
                elif relative_directory == Path("formats/invalid"):
                    ignored.update(("broken-link", "link-loop"))
                return sorted(ignored.intersection(names))

            shutil.copytree(
                presets_path,
                presets_output_path,
                ignore=ignored_preset_entries,
            )
            force_include = build_data["force_include"]
            if not isinstance(force_include, dict):
                raise TypeError("force_include build data must be a dict")
            force_include[str(output_path)] = "dirdiff/frontend"
            force_include[str(presets_output_path)] = "dirdiff/tests/presets"
        except BaseException:
            temporary_output.cleanup()
            self._temporary_output = None
            raise

    @override
    def finalize(
        self,
        version: str,
        _build_data: dict[str, object],
        _artifact_path: str,
    ) -> None:
        """Dispose of the standard wheel's temporary frontend output.

        Hatch calls this after it has copied forced files into the artifact.
        Editable builds created no resource and return immediately. A missing
        resource for a standard build is an internal lifecycle violation.
        """

        if version == "editable":
            return
        assert self._temporary_output is not None, (
            "standard wheel frontend output is missing"
        )
        self._temporary_output.cleanup()
        self._temporary_output = None
