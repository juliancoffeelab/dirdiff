"""Difftastic JSON to dirdiff row AST contract.

This module defines the boundary between raw difftastic output and the rendered
row AST used by the difftastic service. It accepts difftastic-shaped JSON plus
the original source text and returns dirdiff-shaped rows.

This module must not own raw difftastic execution or final API payload assembly:

* `dirdiff.services.difftastic.difft` owns invoking `difft` and parsing its JSON.
* the service/textdiff layer owns syntax highlighting, fold hints, and frontend
  payload assembly.

Input contract
--------------
The main entrypoint is `build_difftastic_ast`:

* `left_text` and `right_text` are the complete source documents. They are the
  authority for line text, line counts, and user-visible content.
* `left_path_hint` and `right_path_hint` are file-name hints for difftastic
  parser selection.

Accepted difftastic facts
-------------------------
`DifftasticJson.aligned_lines` contains zero-based line index pairs. `None` means
there is no line on that side.

`DifftasticJson.chunks` contains changed ranges keyed by difftastic side names:
`lhs` for the left/old document and `rhs` for the right/new document. The range
offsets are treated as Python string slice offsets into the corresponding source
line.

The `language` field is opaque except for known difftastic fallback labels that
may be exposed through `DifftasticAst.engine_warning`.

Output contract
---------------
`build_difftastic_ast` returns `DifftasticAst`:

* `rows`: a list of `DifftasticRow` values.
* `engine_warning`: optional metadata for known difftastic fallback modes.

Each `DifftasticRow` is a display row, not a difftastic JSON row. Its fields are:

* `status`: one of `equal`, `replace`, `insert`, or `delete`.
* `left_no` and `right_no`: one-based source line numbers, or `None` for
  one-sided rendered rows.
* `left_text` and `right_text`: the exact text shown on each side for this row.
* `left_tokens` and `right_tokens`: optional inline token lists. If present, the
  concatenated token text for a side must correspond to that side's displayed
  row text.

Each `DifftasticInlineToken` has:

* `text`: the rendered token text.
* `status`: `unchanged`, `replace`, `insert`, or `delete`.
* `is_ws`: whether the token is whitespace.

The row list is the exported AST for difftastic rendering. The service layer may
cast it back to the generic row shape at the boundary where shared textdiff
payload code takes over, but inside this module the row contract is explicit.

Required invariants
-------------------
* row text must come from the supplied source text;
* token text should not invent source content;
* one-based row line numbers should always refer back to source lines;
* unchanged semantic tokens should not appear as pure one-sided changes;
* changed semantic tokens on one side should have a corresponding changed token
  on the other side when difftastic supplies a semantic counterpart;
* empty `aligned_lines` returns an empty row list so the service can choose a
  fallback renderer.

Non-goals
---------
This module does not validate the full difftastic JSON schema, does not shell
out to difftastic, does not perform syntax highlighting, does not build fold
hints, and does not assemble the final HTTP/API payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypedDict

from dirdiff.services.difftastic.difft import (
    DifftasticJson,
    run_difftastic_json,
)

type DifftasticRowStatus = Literal["equal", "replace", "insert", "delete"]
type DifftasticTokenStatus = Literal["unchanged", "replace", "insert", "delete"]


class DifftasticInlineToken(TypedDict):
    text: str
    status: DifftasticTokenStatus
    is_ws: bool


class DifftasticRow(TypedDict, total=False):
    """Rendered row shape exported from difftastic logic to the service."""

    status: DifftasticRowStatus
    left_no: int | None
    right_no: int | None
    left_text: str
    right_text: str
    left_tokens: list[DifftasticInlineToken]
    right_tokens: list[DifftasticInlineToken]


@dataclass(frozen=True)
class DifftasticAst:
    rows: list[DifftasticRow]
    engine_warning: dict[str, str] | None


def _difftastic_engine_warning(
    diff_json: DifftasticJson,
) -> dict[str, str] | None:
    language = diff_json.get("language")
    if isinstance(language, str) and "exceeded DFT_GRAPH_LIMIT" in language:
        return {
            "type": "difftastic_graph_limit",
            "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
        }
    return None


def _difftastic_rows_from_json(
    diff_json: DifftasticJson,
    *,
    left_text: str,
    right_text: str,
) -> list[DifftasticRow]:
    raise NotImplementedError


def build_difftastic_ast(
    *,
    left_text: str,
    right_text: str,
    left_path_hint: str | None,
    right_path_hint: str | None,
) -> DifftasticAst:
    diff_json = run_difftastic_json(
        left_text=left_text,
        right_text=right_text,
        left_path_hint=left_path_hint,
        right_path_hint=right_path_hint,
    )
    rows = _difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )
    return DifftasticAst(
        rows=rows,
        engine_warning=_difftastic_engine_warning(diff_json),
    )
