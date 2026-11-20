from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Annotated, Literal, NotRequired, Protocol, TypedDict, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DiffEngineProtocol",
    "DiffEngineResult",
    "DiffEngineRow",
    "DiffSide",
    "DiffSummary",
    "DiffsitterEngineConfig",
    "DifftasticEngineConfig",
    "DirdiffEngineConfig",
    "DirdiffError",
    "EngineConfig",
    "EngineWarning",
    "GitAlgorithm",
    "GitColorMoved",
    "GitColorMovedWs",
    "GitEngineConfig",
    "GitWordBoundary",
    "GitWordBoundaryCustom",
    "GitWordBoundaryDefault",
    "GitWordBoundaryLanguage",
    "GitWordBoundaryMode",
    "GumTreeEngineConfig",
    "GumTreeMatcher",
    "InlineToken",
    "InlineTokenStatus",
    "MoveSpan",
    "PairedMove",
    "TokenDiffEngineConfig",
    "UnpairedMove",
    "git_executable",
]


type GitAlgorithm = Literal["myers", "minimal", "patience", "histogram"]

type GitColorMoved = Literal[
    "no", "plain", "blocks", "zebra", "dimmed-zebra", "default"
]

type GitColorMovedWs = Literal[
    "no",
    "ignore-space-at-eol",
    "ignore-space-change",
    "ignore-all-space",
    "allow-indentation-change",
]

type GitWordBoundaryMode = Literal["default", "language", "custom"]


class GitWordBoundaryDefault(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["default"]


class GitWordBoundaryLanguage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["language"]


class GitWordBoundaryCustom(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    mode: Literal["custom"]
    regex: Annotated[str, Field(min_length=1)] | None


type GitWordBoundary = Annotated[
    Union[
        GitWordBoundaryDefault, GitWordBoundaryLanguage, GitWordBoundaryCustom
    ],
    Field(discriminator="mode"),
]


class GitEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["git"]
    algorithm: GitAlgorithm
    ignore_space_change: bool
    word_diff: bool
    word_boundary: GitWordBoundary
    color_moved: GitColorMoved
    color_moved_ws: GitColorMovedWs


class DifftasticEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["difftastic"]
    ignore_comments: bool


type GumTreeMatcher = Literal[
    "gumtree-simple",
    "gumtree-classic",
    "gumtree-hybrid",
    "gumtree-simple-stable",
    "gumtree-simple-auto",
]


class GumTreeEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["gumtree"]
    matcher: GumTreeMatcher


class DirdiffEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["dirdiff"]


class DiffsitterEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["diffsitter"]


class TokenDiffEngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    engine: Literal["tokendiff"]


type EngineConfig = Annotated[
    GitEngineConfig
    | DifftasticEngineConfig
    | GumTreeEngineConfig
    | DirdiffEngineConfig
    | DiffsitterEngineConfig
    | TokenDiffEngineConfig,
    Field(discriminator="engine"),
]


class DirdiffError(ValueError): ...


type InlineTokenStatus = Literal[
    "unchanged",
    "replace",
    "insert",
    "delete",
    "move",
]

type DiffEngineRowStatus = Literal[
    "equal",
    "replace",
    "insert",
    "delete",
    "move",
]


@cache
def git_executable() -> str:
    if sys.platform != "darwin":
        return "git"
    located = subprocess.run(
        ["xcrun", "--find", "git"],
        check=False,
        capture_output=True,
        text=True,
    )
    resolved = located.stdout.strip()
    if located.returncode == 0 and resolved != "" and Path(resolved).is_file():
        return resolved
    return "git"


@dataclass(frozen=True)
class DiffSide:
    exists: bool

    text: str | None

    path_hint: str | None = None


class DiffSummary(TypedDict):
    changed_lines: int

    modified_lines: int

    added_lines: int

    removed_lines: int

    moved_lines: int


class MoveSpan(TypedDict):
    start_line: int

    start_column: int

    end_line: int

    end_column: int


class PairedMove(TypedDict):
    kind: Literal["paired"]

    left: MoveSpan

    right: MoveSpan


type MoveGroup = Literal["primary", "alternative"]


class UnpairedMove(TypedDict):
    kind: Literal["unpaired"]

    group: MoveGroup

    dimmed: bool


type Move = PairedMove | UnpairedMove


class InlineToken(TypedDict):
    text: str

    is_ws: bool

    move: NotRequired[PairedMove]

    status: InlineTokenStatus


class DiffEngineRow(TypedDict):
    move: NotRequired[UnpairedMove]

    status: DiffEngineRowStatus

    left_no: int | None

    right_no: int | None

    left_text: str

    right_text: str

    left_tokens: NotRequired[list[InlineToken]]

    right_tokens: NotRequired[list[InlineToken]]


class EngineWarning(TypedDict):
    type: Literal[
        "difftastic_graph_limit",
        "difftastic_empty_rows",
        "gumtree_invalid_json",
        "gumtree_unsupported_language",
        "gumtree_ambiguous_offsets",
        "diffsitter_unsupported_language",
        "diffsitter_empty_result",
        "tokendiff_region_limit",
    ]

    message: str


class DiffEngineResult(TypedDict):
    summary: DiffSummary

    rows: list[DiffEngineRow]

    interleave_inline_rows: bool

    engine_warning: NotRequired[EngineWarning]


class DiffEngineProtocol(Protocol):
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult: ...
