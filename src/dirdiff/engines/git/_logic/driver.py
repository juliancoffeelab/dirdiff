"""Run `git diff --no-index` over supplied text.

## Public interface

`run_git_no_index_diff` returns either a controlled colored unified patch or a
porcelain word patch for two strings.

## Purpose and boundaries

No-index mode gives any loaded text pair Git's diff algorithm without tying the
engine to a repository. Path hints name temporary files but never authorize a
filesystem read. Repository refs and File loading therefore remain in
`dirdiff.backend`; row construction and engine behavior remain in
`dirdiff.engines.git.logic`.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from dirdiff.engines.base import (
    DirdiffError,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    git_executable,
)

__all__ = [
    "run_git_no_index_diff",
]


def run_git_no_index_diff(
    *,
    algorithm: GitAlgorithm,
    word_diff: bool,
    ignore_space_change: bool,
    color_moved: GitColorMoved,
    color_moved_ws: GitColorMovedWs,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> str:
    """Run `git diff --no-index` for one already-loaded text pair.

    Git no-index mode exits with `0` when files are equal and `1` when a
    diff exists, so both are successful engine outcomes.  Other exit codes are
    surfaced as `DirdiffError` because they mean Git failed to produce a
    trustworthy patch.

    # Parameters

    - `algorithm`: Validated line-matching choice, overriding Git configuration.
    - `word_diff`: Ask Git for machine-readable word classifications.
    - `ignore_space_change`: Pass `--ignore-space-change` when enabled.
    - `color_moved`: Exact move-color mode to apply.
    - `color_moved_ws`: Whitespace policy used only for move detection.
    - `left_text`: Complete old text written to Git's left temporary file.
    - `right_text`: Complete new text written to Git's right temporary file.
    - `left_path_hint`: Optional old path used only to choose a useful suffix.
    - `right_path_hint`: Optional new path used only to choose a useful suffix.

    # Usage

    `GitDiffEngine.render_diff` calls this only when both text sides exist.
    Pass already-loaded text and optional names used solely for temporary-file
    suffixes.

    # Failures

    Raises `DirdiffError` if Git cannot start or exits without producing a
    trustworthy no-index result.
    """

    def _temp_file_name(label: str, path_hint: str | None) -> str:
        """Return a temp filename with a useful suffix for Git headers.

        # Parameters

        - `label`: Internal side name used as the filename stem.
        - `path_hint`: Optional source name from which to copy the suffix.
        """
        if path_hint is None:
            return f"{label}.txt"
        suffix = Path(path_hint).suffix
        if suffix == "":
            return f"{label}.txt"
        return f"{label}{suffix}"

    with tempfile.TemporaryDirectory(prefix="dirdiff-git-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / _temp_file_name("left", left_path_hint)
        right_path = tmp / _temp_file_name("right", right_path_hint)
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    # The resolver skips macOS's xcrun PATH shim: measured
                    # 19.9ms vs 5.8ms per spawn, and this engine spawns once
                    # per rendered file.
                    git_executable(),
                    # These slots form the parser's wire alphabet. Reset every
                    # slot Git can emit so personal colors never affect parsing.
                    *[
                        arg
                        for slot, color in {
                            "meta": "normal",
                            "frag": "normal",
                            "func": "normal",
                            "context": "normal",
                            "old": "31",
                            "new": "32",
                            "oldMoved": "33",
                            "newMoved": "34",
                            "oldMovedAlternative": "35",
                            "newMovedAlternative": "36",
                            "oldMovedDimmed": "37",
                            "newMovedDimmed": "38",
                            "oldMovedAlternativeDimmed": "39",
                            "newMovedAlternativeDimmed": "40",
                            "whitespace": "normal",
                        }.items()
                        for arg in ("-c", f"color.diff.{slot}={color}")
                    ],
                    "-c",
                    "diff.wsErrorHighlight=none",
                    "-c",
                    "diff.suppressBlankEmpty=false",
                    "diff",
                    "--no-index",
                    f"--diff-algorithm={algorithm}",
                    *(["--ignore-space-change"] if ignore_space_change else []),
                    *(
                        [
                            "--word-diff=porcelain",
                            "--word-diff-regex=[^[:space:]]+",
                            "--unified=0",
                            "--no-color",
                        ]
                        if word_diff
                        else ["--color=always"]
                    ),
                    f"--color-moved={color_moved}",
                    f"--color-moved-ws={color_moved_ws}",
                    "--no-relative",
                    "--text",
                    "--no-ext-diff",
                    "--no-textconv",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
            )
        except FileNotFoundError as exc:
            raise DirdiffError(
                "Git engine requires the `git` executable on PATH."
            ) from exc

    if result.returncode in {0, 1}:
        return result.stdout.decode("utf-8")

    message = result.stderr.decode("utf-8").strip()
    if message == "":
        message = "Git could not build this diff."
    raise DirdiffError(message)
