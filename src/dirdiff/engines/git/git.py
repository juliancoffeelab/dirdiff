"""Raw ``git diff --no-index`` execution for the Git diff engine.

This module is the subprocess boundary for Git-backed rendering.  It receives
already-loaded old/new text, writes that text to temporary files, invokes Git's
diff algorithm in no-index mode, and returns the unified patch text.  It does
not know about repository refs, manifests, lazy loading, or API response
metadata.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from dirdiff.backend import TextDiffError

__all__ = [
    "run_git_no_index_diff",
]


def _temp_file_name(label: str, path_hint: str | None) -> str:
    """Return a temp filename with a useful suffix for Git's diff headers."""
    if path_hint is None:
        return f"{label}.txt"
    suffix = Path(path_hint).suffix
    if suffix == "":
        return f"{label}.txt"
    return f"{label}{suffix}"


def run_git_no_index_diff(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> str:
    """Run ``git diff --no-index`` for one already-loaded text pair.

    Git no-index mode exits with ``0`` when files are equal and ``1`` when a
    diff exists, so both are successful engine outcomes.  Other exit codes are
    surfaced as ``TextDiffError`` because they mean Git failed to produce a
    trustworthy patch.
    """
    with tempfile.TemporaryDirectory(prefix="dirdiff-git-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / _temp_file_name("left", left_path_hint)
        right_path = tmp / _temp_file_name("right", right_path_hint)
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-index",
                    "--no-color",
                    "--no-ext-diff",
                    "--no-textconv",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise TextDiffError(
                "Git engine requires the `git` executable on PATH."
            ) from exc

    if result.returncode in {0, 1}:
        return result.stdout

    message = result.stderr.strip()
    if message == "":
        message = "Git could not build this diff."
    raise TextDiffError(message)
