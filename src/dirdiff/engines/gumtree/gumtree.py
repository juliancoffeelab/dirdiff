"""Run GumTree and validate the JSON facts dirdiff consumes.

## Public interface

`gumtree_executable_for_cwd` applies the configured executable search policy.
`run_gumtree_json` compares two supplied strings and returns `GumTreeJson`; the
exported JSON record types describe the validated result.

## Purpose and boundaries

GumTree requires paths, so this module writes supplied text to temporary files
and uses the path hints only for parser-selecting suffixes. It returns external
tree matches and actions without building dirdiff rows. Row construction lives
in `dirdiff.engines.gumtree.logic`.
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
"""Environment variable for an explicit GumTree executable path.

When set, executable discovery treats the value as authoritative and reports a
missing file instead of trying another location.
"""
GUMTREE_RELATIVE_BIN = Path("gumtree/dist/build/install/gumtree/bin/gumtree")
"""Development-checkout location tried after PATH-based discovery.

The path is interpreted relative to the parent of dirdiff's current working
directory, matching adjacent local GumTree checkouts.
"""

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
    """One source-to-destination node match reported by GumTree.

    Raw JSON validation returns this shape to move and update row building. The
    row builder uses `src` to find the old syntax range and `dest` to find its
    corresponding new range.

    Both values remain GumTree tree descriptions. This record does not expose a
    parsed syntax node or dirdiff review coordinate.
    """

    src: str
    """Exact source-tree description used as the match-table key.

    Update and move actions repeat this spelling. Row building requires an exact
    match before it can locate the corresponding new-source range.
    """

    dest: str
    """Destination-tree description paired with `src` by GumTree.

    Its terminal range addresses the new source; row building never treats the
    human-readable node label as source content.
    """


def _is_gumtree_match(value: object) -> TypeIs[GumTreeJsonMatch]:
    """Narrow an unknown JSON value to one source/destination match.

    Both tree descriptions are required strings. Range syntax and source bounds
    remain projector checks because validation has no source text.
    """
    if not isinstance(value, dict):
        return False
    return isinstance(value.get("src"), str) and isinstance(
        value.get("dest"), str
    )


class GumTreeJsonAction(TypedDict):
    """One raw edit action from GumTree's JSON output.

    Raw JSON validation returns these records to action classification.
    `action` and `tree` are always present; move, insert, and update actions may
    carry the fields needed by that GumTree operation.

    The type mirrors accepted external data. It does not promise that every
    action is supported or that its ranges are valid for the supplied source.
    """

    action: Required[str]
    """External operation name used to select dirdiff token status.

    Prefixes `insert`, `delete`, `update`, and `move` are interpreted. Other
    names remain valid raw data but produce no decoration.
    """

    tree: Required[str]
    """Affected tree in the operation's source coordinate space.

    Deletes, updates, and moves address old source here; inserts address new
    source. Updates and moves use the match table for the other side.
    """

    parent: NotRequired[str]
    """Destination parent description carried by operations that need placement.

    Current token mapping does not infer a range from it; the field is kept
    validated so supported action records are not partially typed.
    """

    at: NotRequired[int]
    """Destination child ordinal supplied by insertion or move operations.

    Range mapping does not use sibling order because the action tree and
    match table already identify visible source spans.
    """

    label: NotRequired[str]
    """Replacement label supplied by update operations, when any.

    Dirdiff renders exact source slices instead of this label, so it remains
    integration metadata and cannot substitute for missing source text.
    """


def _is_gumtree_action(value: object) -> TypeIs[GumTreeJsonAction]:
    """Validate the action fields consumed by GumTree range mapping.

    Operation and affected tree are required. Optional destination metadata is
    accepted only with its declared runtime type; unsupported action names stay
    valid raw integration data and are ignored by classification.
    """
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
    """Validated subset of one `gumtree textdiff -f JSON` result.

    `run_gumtree_json` returns this validated shape to
    `build_gumtree_rows_from_json`, which combines it with the original texts.

    GumTree may omit matches or actions when none exist. The type models only
    fields dirdiff consumes and is not a public engine result.
    """

    matches: NotRequired[list[GumTreeJsonMatch]]
    """Validated source-to-destination tree descriptions, omitted when empty.

    Update and move classification uses this mapping to find the new-side range
    corresponding to an old-side action node. Missing required matches are
    structural GumTree damage and remain errors.
    """

    actions: NotRequired[list[GumTreeJsonAction]]
    """Validated edit actions in GumTree's reported source order.

    Row construction converts only their addressed ranges and kinds to token
    decoration. Omission means GumTree reported no actions, not that validation
    should synthesize an empty document diff.
    """


def _is_gumtree_json(value: object) -> TypeIs[GumTreeJson]:
    """Validate the supported top-level GumTree JSON fields.

    GumTree may omit empty match and action lists. Unknown fields remain opaque,
    while every consumed record is checked in full.
    """
    if not isinstance(value, dict):
        return False

    matches = value.get("matches")
    if matches is not None and not _is_gumtree_matches(matches):
        return False

    actions = value.get("actions")
    return actions is None or _is_gumtree_actions(actions)


class GumTreeInvalidJsonError(DirdiffError):
    """Report GumTree output that is JSON but violates `GumTreeJson`.

    `run_gumtree_json` raises this after JSON parsing succeeds but structural
    validation fails. `GumTreeDiffEngine` catches it at the engine boundary and
    returns textual rows with an explicit warning.

    This error does not cover process failure, missing executables, or invalid
    dirdiff source invariants.
    """

    pass


def gumtree_executable_for_cwd(cwd: Path) -> Path:
    """Locate the GumTree executable for one dirdiff working directory.

    An explicit environment path wins, followed by `gumtree` on PATH and the
    adjacent development checkout. A configured but missing path is an error;
    it is not silently replaced by another installation.

    # Usage

    `GumTreeDiffEngine` passes `Path.cwd()` before each two-sided comparison.
    Tests may pass another working directory to exercise adjacent-checkout
    discovery.

    # Failures

    Raises `DirdiffError` when an explicitly configured executable is missing
    or no supported discovery location contains GumTree.
    """
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

    `gumtree_bin` must identify the executable. The path hints provide only
    the temporary input suffixes and need not identify existing files. The
    temporary directory is removed before return. A missing or unsuccessful
    executable raises `DirdiffError`; malformed or structurally invalid JSON
    raises `GumTreeInvalidJsonError`.

    # Parameters

    - `gumtree_bin`: Exact executable selected by discovery or a test.
    - `left_text`: Complete old source written to the left temporary file.
    - `right_text`: Complete new source written to the right temporary file.
    - `left_path_hint`: Old source name used only for its parser suffix.
    - `right_path_hint`: New source name used only for its parser suffix.

    # Usage

    Call after `gumtree_executable_for_cwd`. The engine uses this only when both
    sides exist; one-sided Files do not need an external comparison.

    # Failures

    Raises `DirdiffError` when the executable is absent, exits unsuccessfully,
    or reports a parser failure instead of JSON. Raises
    `GumTreeInvalidJsonError` for malformed or unsupported JSON output.
    """

    def _temp_file_name(label: str, path_hint: str) -> str:
        """Use the hinted suffix, or the bare internal label without one.

        # Parameters

        - `label`: Internal side name used as the filename stem.
        - `path_hint`: Source name from which to copy a suffix, if present.
        """
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

    if _is_gumtree_json(parsed):
        return parsed
    raise GumTreeInvalidJsonError(
        "GumTree returned an unexpected JSON payload."
    )


def _is_gumtree_matches(value: object) -> TypeIs[list[GumTreeJsonMatch]]:
    """Validate every source/destination match in GumTree's result.

    Duplicate mappings remain visible to row building rather than being combined
    during schema validation.
    """
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_match(match) for match in value)


def _is_gumtree_actions(value: object) -> TypeIs[list[GumTreeJsonAction]]:
    """Validate every edit action in GumTree's result.

    Action order is preserved because it breaks ties between identical visible
    ranges during row building.
    """
    if not isinstance(value, list):
        return False
    return all(_is_gumtree_action(action) for action in value)
