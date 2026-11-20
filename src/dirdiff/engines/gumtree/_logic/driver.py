from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NotRequired, Required, TypedDict, TypeIs

from dirdiff.engines.base import DirdiffError, GumTreeMatcher

__all__ = [
    "GumTreeJson",
    "GumTreeUnsupportedError",
    "gumtree_executable_for_cwd",
    "is_gumtree_json",
    "run_gumtree_json",
]

# When set, it is authoritative: a missing file is an error, no other location
# is tried.
GUMTREE_BIN_ENV = "DIRDIFF_GUMTREE_BIN"
# Tried after PATH, relative to the parent of the cwd (adjacent local checkout).
GUMTREE_RELATIVE_BIN = Path("gumtree/dist/build/install/gumtree/bin/gumtree")


# No generator for the filename; the caller may degrade to a unified diff with a
# warning.
class GumTreeUnsupportedError(DirdiffError): ...


class GumTreeJsonMatch(TypedDict):
    src: str
    dest: str


def _is_gumtree_match(value: object) -> TypeIs[GumTreeJsonMatch]:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("src"), str) and isinstance(
        value.get("dest"), str
    )


class GumTreeJsonAction(TypedDict):
    action: Required[str]
    tree: Required[str]
    parent: NotRequired[str]
    at: NotRequired[int]
    label: NotRequired[str]


def _is_gumtree_action(value: object) -> TypeIs[GumTreeJsonAction]:
    if not isinstance(value, dict):
        return False

    if not isinstance(value.get("action"), str):
        return False
    if not isinstance(value.get("tree"), str):
        return False

    parent = value.get("parent")
    if parent is not None and not isinstance(parent, str):
        return False

    at = value.get("at")
    if at is not None and not isinstance(at, int):
        return False

    label = value.get("label")
    return label is None or isinstance(label, str)


class GumTreeJson(TypedDict):
    matches: NotRequired[list[GumTreeJsonMatch]]
    actions: NotRequired[list[GumTreeJsonAction]]


def is_gumtree_json(value: object) -> TypeIs[GumTreeJson]:
    if not isinstance(value, dict):
        return False

    matches = value.get("matches")
    if matches is not None and not _is_gumtree_matches(matches):
        return False

    actions = value.get("actions")
    return actions is None or _is_gumtree_actions(actions)


class GumTreeInvalidJsonError(DirdiffError):
    pass


def gumtree_executable_for_cwd(cwd: Path) -> Path:
    configured = os.environ.get(GUMTREE_BIN_ENV)
    if configured is not None:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path
        raise DirdiffError(
            f"GumTree executable from {GUMTREE_BIN_ENV} does not exist: "
            f"{configured_path}"
        )

    path_executable = shutil.which("gumtree")
    if path_executable is not None:
        return Path(path_executable)

    candidate = cwd.parent / GUMTREE_RELATIVE_BIN
    if candidate.is_file():
        return candidate

    raise DirdiffError(
        "GumTree engine requires `gumtree` on PATH, GumTree at ../gumtree, "
        f"or {GUMTREE_BIN_ENV} pointing to the GumTree executable."
    )


def run_gumtree_json(
    *,
    gumtree_bin: Path,
    matcher: GumTreeMatcher,
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> GumTreeJson:
    def _temp_file_name(label: str, path_hint: str) -> str:
        if path_hint == "":
            return label
        return f"{label}-{Path(path_hint).name}"

    with tempfile.TemporaryDirectory(prefix="dirdiff-gumtree-") as raw_tmp:
        tmp = Path(raw_tmp)
        left_path = tmp / _temp_file_name("left", left_path_hint)
        right_path = tmp / _temp_file_name("right", right_path_hint)
        left_path.write_text(left_text, encoding="utf-8")
        right_path.write_text(right_text, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    str(gumtree_bin),
                    "textdiff",
                    "-m",
                    matcher,
                    "-f",
                    "JSON",
                    str(left_path),
                    str(right_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise DirdiffError(
                f"GumTree executable does not exist: {gumtree_bin}"
            ) from exc

    # The CLI can exit successfully after printing this precise registry error.
    # Only an absent generator is recoverable; parser and process failures are
    # not.
    if any(
        line.startswith(
            "java.lang.UnsupportedOperationException: No generator found for file: "
        )
        for line in result.stderr.splitlines()
    ):
        raise GumTreeUnsupportedError(
            "GumTree does not support this file type. Showing a unified text diff."
        )

    if result.returncode != 0:
        message = result.stderr.strip()
        if message == "":
            message = "GumTree could not build this diff."
        raise DirdiffError(message)

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        # GumTree exits 0 even when it cannot process the input (for example
        # "No generator found for file" on a language without a parser) and
        # prints the reason to stderr with an empty stdout. Surface that
        # reason instead of a misleading JSON complaint; stack-trace frames
        # are dropped, the exception summary lines are kept.
        stderr_lines = [
            line
            for line in result.stderr.splitlines()
            if line.strip() != "" and not line.lstrip().startswith("at ")
        ]
        if stderr_lines != []:
            raise DirdiffError(" ".join(stderr_lines)) from exc
        raise GumTreeInvalidJsonError("GumTree returned invalid JSON.") from exc

    if is_gumtree_json(parsed):
        return parsed
    raise GumTreeInvalidJsonError(
        "GumTree returned an unexpected JSON payload."
    )


def _is_gumtree_matches(value: object) -> TypeIs[list[GumTreeJsonMatch]]:
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_match(match) for match in value)


def _is_gumtree_actions(value: object) -> TypeIs[list[GumTreeJsonAction]]:
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_action(action) for action in value)
