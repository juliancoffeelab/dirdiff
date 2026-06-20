"""Raw GumTree execution and JSON contract.

This module is the only place in the GumTree service package that invokes the
external GumTree executable. It writes the two already-loaded file contents to
temporary files, runs ``gumtree textdiff -f JSON`` on that single file pair, and
returns the parsed JSON payload without projecting it into dirdiff rows.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import NotRequired, Required, TypedDict, cast

from dirdiff.sources import TextDiffError

GUMTREE_BIN_ENV = "DIRDIFF_GUMTREE_BIN"
GUMTREE_RELATIVE_BIN = Path("gumtree/dist/build/install/gumtree/bin/gumtree")


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


class GumTreeInvalidJsonError(TextDiffError):
    pass


def gumtree_executable_for_cwd(cwd: Path) -> Path:
    configured = os.environ.get(GUMTREE_BIN_ENV)
    if configured is not None:
        configured_path = Path(configured).expanduser()
        if configured_path.is_file():
            return configured_path
        raise TextDiffError(
            f"GumTree executable from {GUMTREE_BIN_ENV} does not exist: "
            f"{configured_path}"
        )

    path_executable = shutil.which("gumtree")
    if path_executable is not None:
        return Path(path_executable)

    candidate = cwd.parent / GUMTREE_RELATIVE_BIN
    if candidate.is_file():
        return candidate

    raise TextDiffError(
        "GumTree engine requires `gumtree` on PATH, GumTree at ../gumtree, "
        f"or {GUMTREE_BIN_ENV} pointing to the GumTree executable."
    )


def _temp_file_name(label: str, path_hint: str) -> str:
    suffix = Path(path_hint).suffix
    if suffix == "":
        return label
    return f"{label}{suffix}"


def run_gumtree_json(
    *,
    gumtree_bin: Path,
    left_text: str,
    right_text: str,
    left_path_hint: str,
    right_path_hint: str,
) -> GumTreeJson:
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
            raise TextDiffError(
                f"GumTree executable does not exist: {gumtree_bin}"
            ) from exc

    if result.returncode != 0:
        message = result.stderr.strip()
        if message == "":
            message = "GumTree could not build this diff."
        raise TextDiffError(message)

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GumTreeInvalidJsonError("GumTree returned invalid JSON.") from exc

    if isinstance(parsed, dict):
        return cast("GumTreeJson", parsed)
    raise GumTreeInvalidJsonError(
        "GumTree returned an unexpected JSON payload."
    )
