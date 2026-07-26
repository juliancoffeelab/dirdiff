"""Raw GumTree execution and JSON contract.

This module is the only place in the GumTree service package that invokes the
external GumTree executable. It writes the two already-loaded file contents to
temporary files, runs `gumtree textdiff -f JSON` on that single file pair, and
returns the parsed JSON payload without projecting it into dirdiff rows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NotRequired, Required, TypedDict, TypeIs

from dirdiff.engines.base import DirdiffError

GUMTREE_BIN_ENV = "DIRDIFF_GUMTREE_BIN"
GUMTREE_RELATIVE_BIN = Path("gumtree/dist/build/install/gumtree/bin/gumtree")

__all__ = [
    "GUMTREE_BIN_ENV",
    "GUMTREE_RELATIVE_BIN",
    "GumTreeInvalidJsonError",
    "GumTreeJson",
    "GumTreeJsonAction",
    "GumTreeJsonMatch",
    "gumtree_executable_for_cwd",
    "run_gumtree_json",
]


class GumTreeJsonMatch(TypedDict):
    src: str
    dest: str


class GumTreeJsonAction(TypedDict):
    action: Required[str]
    tree: Required[str]
    parent: NotRequired[str]
    at: NotRequired[int]
    label: NotRequired[str]


class GumTreeJson(TypedDict):
    matches: NotRequired[list[GumTreeJsonMatch]]
    actions: NotRequired[list[GumTreeJsonAction]]


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
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> GumTreeJson:
    """Run GumTree on one already-loaded text pair and validate its response.

    ``gumtree_bin`` must identify the executable. The path hints provide only
    the temporary input suffixes and need not identify existing files. The
    temporary directory is removed before return. A missing or unsuccessful
    executable raises ``DirdiffError``; malformed or structurally invalid JSON
    raises ``GumTreeInvalidJsonError``.
    """

    def _temp_file_name(label: str, path_hint: str) -> str:
        """Use the hinted source suffix, or the bare internal label without one."""
        suffix = Path(path_hint).suffix
        if suffix == "":
            return label
        return f"{label}{suffix}"

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

    if result.returncode != 0:
        message = result.stderr.strip()
        if message == "":
            message = "GumTree could not build this diff."
        raise DirdiffError(message)

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GumTreeInvalidJsonError("GumTree returned invalid JSON.") from exc

    if _is_gumtree_json(parsed):
        return parsed
    raise GumTreeInvalidJsonError(
        "GumTree returned an unexpected JSON payload."
    )


def _is_gumtree_json(value: object) -> TypeIs[GumTreeJson]:
    if not isinstance(value, dict):
        return False

    matches = value.get("matches")
    if matches is not None and not _is_gumtree_matches(matches):
        return False

    actions = value.get("actions")
    return actions is None or _is_gumtree_actions(actions)


def _is_gumtree_matches(value: object) -> TypeIs[list[GumTreeJsonMatch]]:
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_match(match) for match in value)


def _is_gumtree_match(value: object) -> TypeIs[GumTreeJsonMatch]:
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("src"), str) and isinstance(
        value.get("dest"), str
    )


def _is_gumtree_actions(value: object) -> TypeIs[list[GumTreeJsonAction]]:
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_action(action) for action in value)


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
