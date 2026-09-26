"""Run `git diff --no-index` over supplied text.

## Public interface

`run_git_no_index_diff` returns either a controlled colored unified patch or a
porcelain word patch for two strings.

## Purpose and boundaries

No-index mode gives any loaded text pair Git's diff algorithm without tying the
engine to a repository. Path hints name temporary files but never authorize a
filesystem read. Repository refs and File loading therefore remain in
`dirdiff.change_source`; row construction and engine behavior remain in
`dirdiff.engines.git.logic`.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from dirdiff.engines.base import (
    DirdiffError,
    GitAlgorithm,
    GitColorMoved,
    GitColorMovedWs,
    GitWordBoundary,
    git_executable,
)

__all__ = [
    "run_git_no_index_diff",
]

_GIT_DIFF_DRIVER_SUFFIXES: dict[str, tuple[str, ...]] = {
    "ada": (".adb", ".ads"),
    "bash": (".sh", ".bash"),
    "bibtex": (".bib",),
    "cpp": (".c", ".h", ".cc", ".cpp", ".cxx", ".hh", ".hpp", ".hxx"),
    "csharp": (".cs",),
    "css": (".css",),
    "dts": (".dts", ".dtsi"),
    "elixir": (".ex", ".exs"),
    "fortran": (".f", ".f90", ".f95", ".for"),
    "fountain": (".fountain",),
    "golang": (".go",),
    "html": (".html", ".htm", ".xhtml"),
    "java": (".java",),
    "kotlin": (".kt", ".kts"),
    "markdown": (".md", ".markdown"),
    "matlab": (".m",),
    "objc": (".mm",),
    "pascal": (".pas", ".pp"),
    "perl": (".pl", ".pm", ".t"),
    "php": (".php",),
    "python": (".py", ".pyi"),
    "ruby": (".rb",),
    "rust": (".rs",),
    "scheme": (".scm", ".ss"),
    "tex": (".tex", ".sty", ".cls"),
}
"""File suffixes that select each of Git's built-in diff drivers.

Git ships these drivers but no mapping from paths to them; a repository names
its drivers in `.gitattributes`. The language word boundary writes this table
as an attributes file so `--no-index` mode reaches the same drivers by the
temporary file's suffix. `.m` goes to MATLAB rather than Objective-C, which
keeps `.mm`. A suffix absent here, or a driver name this Git does not know,
leaves Git on its whitespace boundary. Drivers also supply `xfuncname`, which
only changes the function context after each hunk header.
"""

_GIT_DIFF_DRIVER_ATTRIBUTES = "".join(
    f"*{suffix} diff={driver}\n"
    for driver, suffixes in _GIT_DIFF_DRIVER_SUFFIXES.items()
    for suffix in suffixes
)
"""Attributes-file text naming a driver per suffix, built once per process."""


def run_git_no_index_diff(
    *,
    algorithm: GitAlgorithm,
    word_diff: bool,
    word_boundary: GitWordBoundary,
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

    The patch depends only on the parameters below.  Git runs without the
    user's global and system configuration, without configuration injected
    through the environment, and without the user's or system's attributes
    file, so the same inputs produce the same patch on every machine.  The
    temporary directory is not a repository, so no repository configuration
    or attributes apply either.

    # Parameters

    - `algorithm`: Validated line-matching choice, overriding Git configuration.
    - `word_diff`: Ask Git for machine-readable word classifications.
    - `word_boundary`: How Git splits words; ignored unless `word_diff`.
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

    # Git reads configuration from files and from the environment. Point the
    # global file at an empty path, skip the system file, and drop every
    # environment channel (`git -c` from an outer Git, GIT_CONFIG_KEY_n/VALUE_n,
    # and GIT_DIFF_OPTS) so no user setting can alter the patch.
    #
    # TODO: When Git itself launches dirdiff (as `diff.external`, a difftool, a
    # `!` alias, or a hook), the `-c` options of that outer invocation arrive
    # in GIT_CONFIG_PARAMETERS and arguably express what the user wants this
    # diff to honour. They are stripped for now because nothing distinguishes
    # such an intentional pass-through from configuration that merely leaked
    # from an unrelated parent process, and an unnoticed leak silently changes
    # rows (`diff.interHunkContext` alone alters word-mode equal rows). Support
    # the inside-Git case explicitly once its contract is decided.
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {"GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT", "GIT_DIFF_OPTS"}
        and not (
            key.startswith("GIT_CONFIG_KEY_")
            or key.startswith("GIT_CONFIG_VALUE_")
        )
    }
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # Attributes are a second channel: even outside a repository Git reads
    # the system gitattributes and the user's attributes file, and a
    # `diff=<driver>` there would silently change the word boundary. Skip the
    # system file here and point the user file at the language table or at
    # nothing below, so only this call selects drivers.
    env["GIT_ATTR_NOSYSTEM"] = "1"
    # Git compiles word regexes with the C library, whose character semantics
    # follow the locale. A UTF-8 locale makes `.` and bracket classes match
    # whole codepoints, so a custom pattern never splits a multibyte character
    # into a token Git would print as invalid UTF-8. Git's built-in drivers
    # were written for byte matching: they end in a raw high-byte alternative
    # that glues a multibyte sequence together and that fails to compile under
    # UTF-8, so the language boundary runs in the C locale. Every other run
    # is pinned to UTF-8 as well so Git's messages stay in English.
    language = word_diff and word_boundary.mode == "language"
    env["LC_ALL"] = "C" if language else "C.UTF-8"

    with tempfile.TemporaryDirectory(prefix="dirdiff-git-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / _temp_file_name("left", left_path_hint)
        right_path = tmp / _temp_file_name("right", right_path_hint)
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")
        # The attributes file only selects word drivers, so it is written
        # only when a word boundary asks for them.
        attributes_path = tmp / "attributes"
        if language:
            attributes_path.write_text(
                _GIT_DIFF_DRIVER_ATTRIBUTES, encoding="utf-8"
            )

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
                    "-c",
                    "core.attributesFile="
                    + (str(attributes_path) if language else os.devnull),
                    "diff",
                    "--no-index",
                    f"--diff-algorithm={algorithm}",
                    *(["--ignore-space-change"] if ignore_space_change else []),
                    *(
                        [
                            "--word-diff=porcelain",
                            *(
                                [f"--word-diff-regex={word_boundary.regex}"]
                                if word_boundary.mode == "custom"
                                and word_boundary.regex is not None
                                else []
                            ),
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
                env=env,
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
