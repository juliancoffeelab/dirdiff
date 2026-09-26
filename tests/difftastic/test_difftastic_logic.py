"""Check Difftastic row construction against focused alignment cases.

The tests combine sparse Difftastic facts with complete source text and assert
lossless rows, token status, and line status. They call private row-building
helpers where the public result cannot isolate an alignment invariant.
Subprocess execution and final API serialization are outside this module.
"""

import re
from pathlib import Path

from helpers import ToolText, tool_json

from dirdiff.engines import DiffEngineRow, DiffSide, InlineToken
from dirdiff.engines.difftastic import DifftasticDiffEngine, DifftasticJson
from dirdiff.engines.difftastic.logic import (
    difftastic_engine_warning,
    difftastic_rows_from_json,
)
from dirdiff.rendering import enrich_rows_for_display

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"
"""Regression fixture catalog used by focused Difftastic row-building tests.

Helpers address cases relative to this root and require one old/new source pair,
keeping fixture identity visible in assertion failures.
"""


def _word_like_token_atoms(text: str) -> list[str]:
    """Extract identifier and numeric atoms used by regression assertions.

    Punctuation and whitespace are deliberately ignored because these helpers
    detect source words incorrectly painted on one side.
    """
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text)


def _pure_unchanged_one_sided_change_texts(
    rows: list[DiffEngineRow],
) -> list[str]:
    """Find one-sided changed rows whose meaningful text is all unchanged.

    Such rows are contradictory: a deletion or insertion cannot consist only
    of word-like tokens marked unchanged. Returned text makes failures readable.

    # Usage

    Use through `_assert_no_pure_unchanged_one_sided_changes` unless a test needs
    the contradictory row texts for a more specific assertion.

    # Failures

    Asserts when a row selected as contradictory does not carry string text on
    its present side.
    """
    broken_texts: list[str] = []
    for row in rows:
        status = row.get("status")
        if status not in {"delete", "insert"}:
            continue

        side: str | None = None
        if row.get("left_no") is not None and row.get("right_no") is None:
            side = "left"
        elif row.get("left_no") is None and row.get("right_no") is not None:
            side = "right"
        if side is None:
            continue

        tokens: list[InlineToken] | None
        if side == "left":
            tokens = row.get("left_tokens")
        else:
            tokens = row.get("right_tokens")
        if tokens is None:
            continue

        meaningful_tokens: list[InlineToken] = []
        for token in tokens:
            text = token.get("text")
            if _word_like_token_atoms(text) != []:
                meaningful_tokens.append(token)

        if meaningful_tokens == []:
            continue

        if all(
            token.get("status") == "unchanged" for token in meaningful_tokens
        ):
            row_text = row.get(f"{side}_text")
            assert isinstance(row_text, str)
            broken_texts.append(row_text)
    return broken_texts


def _assert_no_pure_unchanged_one_sided_changes(
    rows: list[DiffEngineRow],
) -> None:
    """Reject contradictory one-sided rows containing only unchanged words.

    Punctuation-only one-sided changes remain valid. The helper targets rows
    whose meaningful source atoms claim both one-sided change and no change.

    # Usage

    Call this after building Difftastic rows for a regression case where
    one-sided token status must remain semantically honest.

    # Failures

    Raises `AssertionError` with the contradictory row texts when any are found.
    """
    broken_texts = _pure_unchanged_one_sided_change_texts(rows)
    assert broken_texts == [], broken_texts


def _changed_word_like_atoms_for_line(
    rows: list[DiffEngineRow],
    *,
    side: str,
    line_no: int,
) -> list[str]:
    """Collect changed identifier atoms from one exact source line.

    # Parameters

    - `rows`: Projected rows containing the addressed side.
    - `side`: `left` or `right`, used for line and token field selection.
    - `line_no`: One-based source line whose changed atoms are wanted.

    # Usage

    Focused status regressions call this after identifying the exact side and
    line whose changed identifiers matter.

    # Failures

    Raises `AssertionError` when a matching row contains malformed token data.
    """
    changed_atoms: list[str] = []
    for row in rows:
        if row.get(f"{side}_no") != line_no:
            continue
        tokens = row.get(f"{side}_tokens")
        if tokens is None:
            continue
        assert isinstance(tokens, list)
        for token in tokens:
            assert isinstance(token, dict)
            if token.get("status") == "unchanged":
                continue
            text = token.get("text")
            assert isinstance(text, str)
            changed_atoms.extend(_word_like_token_atoms(text))
    return changed_atoms


def test_difftastic_engine_warning_reports_graph_limit_fallback() -> None:
    """Expose Difftastic's graph-limit label without masking ordinary languages.

    The warning discriminator is part of the engine result contract; a normal
    parser label must not produce a degraded-mode warning.
    """
    assert difftastic_engine_warning(
        {"language": "Text (exceeded DFT_GRAPH_LIMIT)"}
    ) == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
    }
    assert difftastic_engine_warning({"language": "TypeScript"}) is None


def test_difftastic_engine_keeps_identical_source_rows() -> None:
    """Render identical source as readable context with no changed lines.

    Difftastic reports no structural rows for equal inputs. The engine must
    still return the complete source because composed notebook bays rely on
    opening an unchanged cell to reveal its contents.
    """
    source = """value = 1
print(value)
"""

    payload = DifftasticDiffEngine(ignore_comments=False).render_diff(
        old=DiffSide(exists=True, text=source, path_hint="cell.py"),
        new=DiffSide(exists=True, text=source, path_hint="cell.py"),
    )

    assert payload["rows"] == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = 1",
            "right_text": "value = 1",
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": "print(value)",
            "right_text": "print(value)",
        },
    ]
    assert payload["summary"] == {
        "changed_lines": 0,
        "modified_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }


def test_difftastic_rows_classify_makefile_target_suffix_insert() -> None:
    """Classify a Make target suffix as changed, not unchanged context.

    This fixture guards the row status derived from Difftastic tokens when an
    existing target grows one syntactic suffix.
    """
    preset_dir = (
        PRESETS_ROOT
        / "makefile"
        / "makefile-target-dependency-suffix-not-counted"
    )
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))

    diff_json = tool_json(
        "difftastic",
        f"presets/{preset_dir.relative_to(PRESETS_ROOT).as_posix()}",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_path.read_bytes().decode("utf-8"),
        right_text=new_path.read_bytes().decode("utf-8"),
    )

    assert [row["status"] for row in rows] == ["equal"]


def test_difftastic_makefile_plain_render_keeps_inline_tokens() -> None:
    """Keep Difftastic token changes through display enrichment for Makefiles.

    Syntax highlighting may be unavailable, but weaving must still preserve
    the engine's inline insert and unchanged partitions.
    """
    preset_dir = (
        PRESETS_ROOT
        / "makefile"
        / "makefile-target-dependency-suffix-not-counted"
    )
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        f"presets/{preset_dir.relative_to(PRESETS_ROOT).as_posix()}",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    display = enrich_rows_for_display(
        rows=rows,
        left_text=old_text,
        right_text=new_text,
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )

    assert "render_mode" not in display
    assert "truncated_rows" not in display
    assert display["rows"][0]["right_parts"] == [
        {
            "text": "fullcheck: checkFormatPython checkFormatJs ruff mypy tscheck eslint flake-sbt ",
            "syntax_classes": [],
            "diff_status": "unchanged",
            "is_whitespace": False,
            "is_leading_whitespace": False,
        },
        {
            "text": "pytest",
            "syntax_classes": [],
            "diff_status": "insert",
            "is_whitespace": False,
            "is_leading_whitespace": False,
        },
    ]


def test_difftastic_rows_classify_makefile_wrapped_command_suffix_inserts() -> (
    None
):
    """Classify suffix additions on wrapped Make commands as changed lines.

    The case covers repeated structural punctuation around an inserted command
    fragment, where row status must follow token changes.
    """
    preset_dir = (
        PRESETS_ROOT
        / "makefile"
        / "makefile-wrapped-target-dependency-suffix-not-counted"
    )
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))

    diff_json = tool_json(
        "difftastic",
        f"presets/{preset_dir.relative_to(PRESETS_ROOT).as_posix()}",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_path.read_bytes().decode("utf-8"),
        right_text=new_path.read_bytes().decode("utf-8"),
    )

    assert all(row["status"] != "replace" for row in rows)
    assert (
        sum(
            token["status"] != "unchanged"
            for row in rows
            for token in [
                *row.get("left_tokens", []),
                *row.get("right_tokens", []),
            ]
        )
        > 0
    )


def test_difftastic_z_enum_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
    """Keep existing Z enum members out of one-sided change rows.

    Structural expansion may realign the surrounding list, but unchanged member
    words must remain context rather than contradictory inserted/deleted text.
    """
    old_path = PRESETS_ROOT / "typescript/z-enum-adds-top-level-member/old.ts"
    new_path = PRESETS_ROOT / "typescript/z-enum-adds-top-level-member/new.ts"
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/typescript/z-enum-adds-top-level-member",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_filter_expansion_does_not_render_existing_condition_as_one_sided_change() -> (
    None
):
    """Keep the original filter condition as context when its body expands.

    This catches Difftastic alignments that place shared condition text on a
    one-sided row while marking every meaningful token unchanged.
    """
    old_path = (
        PRESETS_ROOT / "typescript/filter-condition-adds-top-level-kind/old.ts"
    )
    new_path = (
        PRESETS_ROOT / "typescript/filter-condition-adds-top-level-kind/new.ts"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/typescript/filter-condition-adds-top-level-kind",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_python_literal_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
    """Keep shared Python literal members paired through list expansion.

    Added members may change wrapping, but pre-existing words cannot become
    one-sided rows containing only unchanged tokens.
    """
    old_path = PRESETS_ROOT / "python/python-literal-adds-top-level-kind/old.py"
    new_path = PRESETS_ROOT / "python/python-literal-adds-top-level-kind/new.py"
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/python-literal-adds-top-level-kind",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_json_rows_use_structural_alignment_and_changed_ranges() -> (
    None
):
    """Project Difftastic alignment and byte ranges into lossless row tokens.

    The test fixes the boundary between external structural facts and original
    source text: alignment chooses rows, while changed ranges choose tokens.
    """
    left_text = """def alpha():
    return 1
"""
    right_text = """def alpha():
    return 2

def beta():
    return 3
"""
    diff_json = tool_json(
        "difftastic",
        "focused/json-rows-use-structural-alignment-and-changed-ranges",
        left=ToolText(left_text, "old.py"),
        right=ToolText(right_text, "new.py"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    # The blank third row exists only on the right; its presence is the
    # change, so it renders as an insertion rather than unchanged context.
    assert [row["status"] for row in rows] == [
        "equal",
        "equal",
        "insert",
        "equal",
        "equal",
    ]
    assert rows[1]["left_tokens"] == [
        {"text": "    return ", "status": "unchanged", "is_ws": False},
        {"text": "1", "status": "delete", "is_ws": False},
    ]
    assert rows[3]["right_text"] == "def beta():"
    assert rows[3]["right_tokens"] == [
        {"text": "def", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "beta", "status": "insert", "is_ws": False},
        {"text": "(", "status": "insert", "is_ws": False},
        {"text": ")", "status": "insert", "is_ws": False},
        {"text": ":", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_unchanged_tail_after_split_call_change() -> (
    None
):
    """Do not synthesize a shared call tail after Difftastic splits the call.

    Row building must follow supplied alignment exactly; reconstructing attractive
    context locally can duplicate or reorder source fragments.
    """
    left_text = """return compute(foo.bar, baz);
"""
    right_text = """return compute(
  foo.barWrapped,
  baz,
);
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-do-not-reconstruct-unchanged-tail-after-split-call-change",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows[0]["status"] == "equal"
    assert rows[0]["left_text"] == "return compute(foo.bar, baz);"
    assert rows[0]["right_text"] == "return compute("
    assert rows[0]["left_tokens"] == [
        {"text": "return compute(foo.", "status": "unchanged", "is_ws": False},
        {"text": "bar", "status": "delete", "is_ws": False},
        {"text": ", baz);", "status": "unchanged", "is_ws": False},
    ]
    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  foo.barWrapped,"
    assert rows[1]["right_tokens"] == [
        {"text": "  foo.", "status": "unchanged", "is_ws": False},
        {"text": "barWrapped", "status": "insert", "is_ws": False},
        {"text": ",", "status": "unchanged", "is_ws": False},
    ]
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  baz,"
    assert rows[2]["right_tokens"] == []
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["right_text"] == ");"
    assert rows[3]["right_tokens"] == []


def test_difftastic_rows_do_not_reconstruct_typescript_array_tail_after_wrap() -> (
    None
):
    """Preserve Difftastic's TypeScript array wrapping without a rebuilt tail.

    Shared closing syntax may move across lines, but row building may not invent a
    paired suffix outside the external alignment.
    """
    old_path = (
        PRESETS_ROOT
        / "typescript/typescript-array-tail-after-wrap-should-stay-right-only/old.ts"
    )
    new_path = (
        PRESETS_ROOT
        / "typescript/typescript-array-tail-after-wrap-should-stay-right-only/new.ts"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/typescript/typescript-array-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  omega,"
    assert rows[2]["right_tokens"] == [
        {"text": "  omega", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_python_dict_tail_after_wrap() -> (
    None
):
    """Preserve wrapped Python dictionary alignment without tail repair.

    The source replay invariant wins over visually pairing a shared closing
    fragment that Difftastic placed on separate rows.
    """
    left_text = """value = make({"left": thing, "mid": keep, "right": done})
"""
    right_text = """value = make({
    "left": wrap(thing_changed),
    "right": done,
})
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-do-not-reconstruct-python-dict-tail-after-wrap",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == '    "right": done,'
    assert rows[2]["right_tokens"] == []


def test_difftastic_rows_do_not_reconstruct_clojure_vector_tail_after_wrap() -> (
    None
):
    """Keep a wrapped Clojure vector tail on Difftastic's chosen rows.

    Row building must not rebuild shared delimiters into a synthetic pair after
    structural wrapping changes their line placement.
    """
    old_path = (
        PRESETS_ROOT
        / "clojure/clojure-vector-tail-after-wrap-should-stay-right-only/old.clj"
    )
    new_path = (
        PRESETS_ROOT
        / "clojure/clojure-vector-tail-after-wrap-should-stay-right-only/new.clj"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/clojure/clojure-vector-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  baz"


def test_difftastic_rows_do_not_reconstruct_clojure_map_tail_after_wrap_removal() -> (
    None
):
    """Keep Clojure map closing syntax faithful after wrapped content removal.

    The regression rejects local tail reconstruction that would detach a
    delimiter from its source line or consume it twice.
    """
    old_path = (
        PRESETS_ROOT
        / "clojure/clojure-map-tail-after-wrap-should-stay-right-only/old.clj"
    )
    new_path = (
        PRESETS_ROOT
        / "clojure/clojure-map-tail-after-wrap-should-stay-right-only/new.clj"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/clojure/clojure-map-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  :end done"


def test_difftastic_rows_do_not_reconstruct_rust_range_tail_after_wrap() -> (
    None
):
    """Preserve Rust range tail placement across a wrapping change.

    A repeated range delimiter is not permission to override Difftastic's line
    alignment or create context that the source rows do not support.
    """
    old_path = (
        PRESETS_ROOT
        / "rust/rust-range-tail-after-wrap-should-stay-right-only/old.rs"
    )
    new_path = (
        PRESETS_ROOT
        / "rust/rust-range-tail-after-wrap-should-stay-right-only/new.rs"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/rust/rust-range-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "    tail,"
    assert rows[2]["right_tokens"] == []


def test_difftastic_rows_do_not_reconstruct_rust_range_inclusive_tail_after_wrap() -> (
    None
):
    """Preserve inclusive Rust range syntax without reconstructed context.

    This variant fixes the same replay boundary for `..=` tokens, whose shared
    punctuation previously invited an incorrect paired tail.
    """
    old_path = (
        PRESETS_ROOT
        / "rust/rust-range-inclusive-tail-after-wrap-should-stay-right-only/old.rs"
    )
    new_path = (
        PRESETS_ROOT
        / "rust/rust-range-inclusive-tail-after-wrap-should-stay-right-only/new.rs"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/rust/rust-range-inclusive-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "    tail,"
    assert rows[2]["right_tokens"] == []


def test_difftastic_rows_do_not_reconstruct_ocaml_atat_tail_after_wrap() -> (
    None
):
    """Keep OCaml `@@` tail fragments on their structurally aligned rows.

    Row building may decorate the exact source slices but must not repair the
    external alignment around repeated application operators.
    """
    old_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-atat-tail-after-wrap-should-stay-right-only/old.ml"
    )
    new_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-atat-tail-after-wrap-should-stay-right-only/new.ml"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/ocaml/ocaml-atat-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  render"
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  @@ wrap changed"
    assert rows[2]["right_tokens"] == [
        {"text": "  @@ ", "status": "unchanged", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "changed", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == ""
    assert rows[3]["right_text"] == "  tail"


def test_difftastic_rows_do_not_reconstruct_ocaml_atat_nested_tail_after_wrap() -> (
    None
):
    """Preserve nested OCaml `@@` tails through a wrapping change.

    Nested repeated operators make textual pairing tempting; the rows must
    still replay each side exactly in Difftastic order.
    """
    old_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-atat-nested-tail-after-wrap-should-stay-right-only/old.ml"
    )
    new_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-atat-nested-tail-after-wrap-should-stay-right-only/new.ml"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/ocaml/ocaml-atat-nested-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  outer"
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  @@ wrap changed"
    assert rows[2]["right_tokens"] == [
        {"text": "  @@ ", "status": "unchanged", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "changed", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == ""
    assert rows[3]["right_text"] == "  tail"


def test_difftastic_rows_do_not_reconstruct_ocaml_pipe_tail_after_wrap() -> (
    None
):
    """Keep an OCaml pipe tail on the source rows Difftastic aligned.

    Shared operator text cannot be pulled into a synthetic context row when its
    line placement differs between the documents.
    """
    old_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-pipe-tail-after-wrap-should-stay-right-only/old.ml"
    )
    new_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-pipe-tail-after-wrap-should-stay-right-only/new.ml"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/ocaml/ocaml-pipe-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  input"
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  |> wrap changed"
    assert rows[2]["right_tokens"] == [
        {"text": "  |> ", "status": "unchanged", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "changed", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == ""
    assert rows[3]["right_text"] == "  |> tail"


def test_difftastic_rows_do_not_reconstruct_ocaml_pipe_double_tail_after_wrap() -> (
    None
):
    """Keep repeated OCaml pipe tails lossless after wrapping.

    The test covers two neighboring operators so row building cannot accidentally
    reuse one source fragment while trying to align the other.
    """
    old_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-pipe-double-tail-after-wrap-should-stay-right-only/old.ml"
    )
    new_path = (
        PRESETS_ROOT
        / "ocaml/ocaml-pipe-double-tail-after-wrap-should-stay-right-only/new.ml"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/ocaml/ocaml-pipe-double-tail-after-wrap-should-stay-right-only",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  input"
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  |> wrap changed"
    assert rows[2]["right_tokens"] == [
        {"text": "  |> ", "status": "unchanged", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "changed", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == ""
    assert rows[3]["right_text"] == "  |> second tail"


def test_difftastic_does_not_pair_bare_brace_residual_fragment() -> None:
    """Leave a residual bare brace one-sided when Difftastic does not pair it.

    Punctuation identity alone is insufficient evidence to reconstruct a row;
    doing so would contradict the structural alignment and source order.
    """
    left_text = """  let cursor = 0;
  for (const span of syntax) {
    const start = clamp(span.start, 0, text.length);
"""
    right_text = """  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {
    const start = sortedBoundaries[index];
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/does-not-pair-bare-brace-residual-fragment",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows[0]["status"] == "equal"
    assert rows[0]["right_text"].endswith(") {")
    assert rows[1]["status"] == "equal"
    assert rows[1]["left_text"] == "  for (const span of syntax) {"
    assert rows[1]["right_no"] is None
    assert rows[1]["right_text"] == ""


def test_difftastic_rows_do_not_duplicate_reconstructed_right_line_numbers() -> (
    None
):
    """Emit every real right line number once when alignment has split tails.

    This catches reconstruction that consumes a right-side source line twice,
    breaking both navigation coordinates and replay.
    """
    left_text = """        "right_path": entry.right_path,
        "change_type": entry.change_type,
        "lazy": True,
    }
    if (
        entry.changed_lines is not None
        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
    ):
        payload["lazy_reason"] = (
            f"{entry.display_name} has {entry.changed_lines} changed lines, "
            "so it is folded by default. Click to fetch and open it."
        )
    return payload
"""
    right_text = """        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
    }
    lazy = _lazy_reason_for_repo_entry(entry)
    if lazy is not None:
        payload["lazy"] = lazy
    return payload
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/lazy-manifest-condition-fragment",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    right_numbers = [
        right_no
        for row in rows
        if isinstance((right_no := row.get("right_no")), int)
    ]

    assert right_numbers == sorted(set(right_numbers))


def test_difftastic_rows_follow_changed_condition_ranges_in_partial_context() -> (
    None
):
    """Preserve real changed ranges for a condition in an indented fragment.

    Without the enclosing function, Difftastic marks the entire new condition
    as inserted tokens. The row builder must retain that external decision.
    """
    left_text = """        "right_path": entry.right_path,
        "change_type": entry.change_type,
        "lazy": True,
    }
    if (
        entry.changed_lines is not None
        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
    ):
        payload["lazy_reason"] = (
            f"{entry.display_name} has {entry.changed_lines} changed lines, "
            "so it is folded by default. Click to fetch and open it."
        )
    return payload
"""
    right_text = """        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
    }
    lazy = _lazy_reason_for_repo_entry(entry)
    if lazy is not None:
        payload["lazy"] = lazy
    return payload
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/lazy-manifest-condition-fragment",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    condition_row = next(row for row in rows if row.get("right_no") == 5)

    assert condition_row["status"] == "equal"
    assert condition_row["right_tokens"] == [
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": "if", "status": "insert", "is_ws": False},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": "lazy", "status": "insert", "is_ws": False},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": "is", "status": "insert", "is_ws": False},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": "not", "status": "insert", "is_ws": False},
        {"text": " ", "status": "insert", "is_ws": True},
        {"text": "None", "status": "insert", "is_ws": False},
        {"text": ":", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_assignment_rhs_as_insert_argument() -> (
    None
):
    """Do not reinterpret an assignment value as a newly inserted argument.

    Similar source text appears in two syntax roles. Row building must honor
    Difftastic's ranges instead of pairing by textual coincidence.
    """
    left_text = """        )
        payload["change_type"] = change_type
        payload["left_path"] = normalized_left
"""
    right_text = """        )
        payload["file_kind"] = _file_kind_for_change_type(
            change_type,
            file_kind=file_kind,
        )
        payload["left_path"] = normalized_left
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-do-not-reconstruct-assignment-rhs-as-insert-argument",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "        )",
            "right_text": "        )",
            "left_tokens": [],
            "right_tokens": [],
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = '
            "_file_kind_for_change_type(",
            "left_tokens": [
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "payload", "status": "delete", "is_ws": False},
                {"text": "[", "status": "delete", "is_ws": False},
                {"text": '"', "status": "delete", "is_ws": False},
                {"text": "change_type", "status": "delete", "is_ws": False},
                {"text": '"', "status": "delete", "is_ws": False},
                {"text": "]", "status": "delete", "is_ws": False},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "=", "status": "delete", "is_ws": False},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "change_type", "status": "delete", "is_ws": False},
            ],
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "payload", "status": "insert", "is_ws": False},
                {"text": "[", "status": "insert", "is_ws": False},
                {"text": '"', "status": "insert", "is_ws": False},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": '"', "status": "insert", "is_ws": False},
                {"text": "]", "status": "insert", "is_ws": False},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "=", "status": "insert", "is_ws": False},
                {"text": " ", "status": "insert", "is_ws": True},
                {
                    "text": "_file_kind_for_change_type",
                    "status": "insert",
                    "is_ws": False,
                },
                {"text": "(", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 3,
            "left_text": "",
            "right_text": "            change_type,",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "change_type", "status": "insert", "is_ws": False},
                {"text": ",", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 4,
            "left_text": "",
            "right_text": "            file_kind=file_kind,",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": "=", "status": "insert", "is_ws": False},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": ",", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 5,
            "left_text": "",
            "right_text": "        )",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": ")", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "equal",
            "left_no": 3,
            "right_no": 6,
            "left_text": '        payload["left_path"] = normalized_left',
            "right_text": '        payload["left_path"] = normalized_left',
            "left_tokens": [],
            "right_tokens": [],
        },
    ]


def test_difftastic_rows_keep_split_show_condition_as_context() -> None:
    """Keep a split `show` condition as context around the actual edit.

    The regression distinguishes unchanged structural text from the nearby
    insertion after Difftastic spreads the expression across rows.
    """
    old_path = (
        PRESETS_ROOT
        / "typescript/typescript-repo-fold-controls-show-placeholder-aligns-poorly/old.tsx"
    )
    new_path = (
        PRESETS_ROOT
        / "typescript/typescript-repo-fold-controls-show-placeholder-aligns-poorly/new.tsx"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/typescript/typescript-repo-fold-controls-show-placeholder-aligns-poorly",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    condition_atoms = {"when", "ui", "displayFiles", "length", "0"}
    left_changed_atoms = set(
        _changed_word_like_atoms_for_line(rows, side="left", line_no=4)
    )
    right_changed_atoms = set(
        _changed_word_like_atoms_for_line(rows, side="right", line_no=6)
    )

    assert condition_atoms.isdisjoint(left_changed_atoms)
    assert condition_atoms.isdisjoint(right_changed_atoms)


def test_difftastic_rows_status_is_equal_for_real_right_only_context_line() -> (
    None
):
    """Treat a one-sided aligned line with no changed spans as context.

    Difftastic can emit a real right-only alignment row whose presence is not an
    insertion; row status must follow changed ranges, not null pairing alone.
    """
    left_text = """return compute(foo.bar, baz);
"""
    right_text = """return compute(
  foo.barWrapped,
  baz,
);
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-status-is-equal-for-real-right-only-context-line",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows[2] == {
        "status": "equal",
        "left_no": None,
        "right_no": 3,
        "left_text": "",
        "right_text": "  baz,",
        "right_tokens": [],
    }
    assert rows[3] == {
        "status": "equal",
        "left_no": None,
        "right_no": 4,
        "left_text": "",
        "right_text": ");",
        "right_tokens": [],
    }


def test_difftastic_rows_status_is_equal_for_real_mixed_unchanged_and_insert_tokens() -> (
    None
):
    """Keep mixed unchanged and inserted token context on an equal row.

    A line retaining meaningful old context is not a pure insertion even when
    every changed token appears on the right.
    """
    left_text = """return compute(foo.bar, baz);
"""
    right_text = """return compute(
  foo.barWrapped,
  baz,
);
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-status-is-replace-for-real-mixed-unchanged-and-insert-tokens",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows[1] == {
        "status": "equal",
        "left_no": None,
        "right_no": 2,
        "left_text": "",
        "right_text": "  foo.barWrapped,",
        "right_tokens": [
            {"text": "  foo.", "status": "unchanged", "is_ws": False},
            {"text": "barWrapped", "status": "insert", "is_ws": False},
            {"text": ",", "status": "unchanged", "is_ws": False},
        ],
    }


def test_difftastic_rows_status_is_insert_when_real_right_only_line_is_changed() -> (
    None
):
    """Classify a genuinely changed right-only row as an insertion.

    Unlike context-only right rows, reported inserted token content gives the
    row a one-sided change status.
    """
    left_text = """value = arg
"""
    right_text = """value = arg
new_value
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-status-is-insert-when-real-right-only-line-is-changed",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "left_tokens": [],
            "right_text": "value = arg",
            "right_tokens": [],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 2,
            "left_text": "",
            "right_text": "new_value",
            "right_tokens": [
                {"text": "new_value", "status": "insert", "is_ws": False},
            ],
        },
    ]


def test_difftastic_omitted_gaps_become_unchanged_at_public_boundary() -> None:
    """Expose omitted Difftastic gaps without inventing a change status.

    Difftastic reports sparse syntax spans and omits the separating whitespace.
    The private lossless partition retains an omitted gap as a private
    classification until the public boundary, then exposes it as unchanged
    without leaking an internal status. That mixed public partition keeps the
    row equal.
    """
    text = "export async\n"
    diff_json: DifftasticJson = {
        "aligned_lines": [[0, None], [None, 0]],
        "chunks": [
            [
                {
                    "lhs": {
                        "line_number": 0,
                        "changes": [
                            {"start": 0, "end": 6, "content": "export"},
                            {"start": 7, "end": 12, "content": "async"},
                        ],
                    }
                },
                {
                    "rhs": {
                        "line_number": 0,
                        "changes": [
                            {"start": 0, "end": 6, "content": "export"},
                            {"start": 7, "end": 12, "content": "async"},
                        ],
                    }
                },
            ]
        ],
    }

    rows = difftastic_rows_from_json(
        diff_json,
        left_text=text,
        right_text=text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": None,
            "left_text": "export async",
            "right_text": "",
            "left_tokens": [
                {"text": "export", "status": "delete", "is_ws": False},
                {"text": " ", "status": "unchanged", "is_ws": True},
                {"text": "async", "status": "delete", "is_ws": False},
            ],
        },
        {
            "status": "equal",
            "left_no": None,
            "right_no": 1,
            "left_text": "",
            "right_text": "export async",
            "right_tokens": [
                {"text": "export", "status": "insert", "is_ws": False},
                {"text": " ", "status": "unchanged", "is_ws": True},
                {"text": "async", "status": "insert", "is_ws": False},
            ],
        },
    ]


def test_difftastic_rows_status_is_equal_when_changed_tokens_are_mixed() -> (
    None
):
    """Keep the row equal when changed tokens do not form a pure insertion.

    Mixed or paired changed classifications must not combine into a one-sided
    row status merely because the alignment is asymmetric.
    """
    left_text = """value = old
"""
    right_text = """value = new
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-status-is-replace-when-changed-tokens-are-not-inserted",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = old",
            "right_text": "value = new",
            "left_tokens": [
                {"text": "value = ", "status": "unchanged", "is_ws": False},
                {"text": "old", "status": "delete", "is_ws": False},
            ],
            "right_tokens": [
                {"text": "value = ", "status": "unchanged", "is_ws": False},
                {"text": "new", "status": "insert", "is_ws": False},
            ],
        },
    ]


def test_difftastic_rows_status_is_delete_when_every_changed_token_is_delete() -> (
    None
):
    """Use deletion for a row whose meaningful changed tokens are all deleted.

    With no unchanged non-whitespace context, the token verdict is a complete
    one-sided removal rather than a replacement.
    """
    left_text = """value = arg
old_value
"""
    right_text = """value = arg
"""
    extension = "ts"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-status-is-delete-when-every-changed-token-is-delete",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "left_tokens": [],
            "right_text": "value = arg",
            "right_tokens": [],
        },
        {
            "status": "delete",
            "left_no": 2,
            "right_no": None,
            "left_text": "old_value",
            "right_text": "",
            "left_tokens": [
                {"text": "old_value", "status": "delete", "is_ws": False},
            ],
        },
    ]


def test_difftastic_rows_statuses_for_real_lazy_manifest_hunk() -> None:
    """Pin token-derived statuses for the lazy-manifest regression hunk.

    The real fixture mixes paired context and one-sided structural spans; each
    row status must agree with its rendered tokens.
    """
    left_text = """def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "change_type": entry.change_type,
        "lazy": True,
    }
    if (
        entry.changed_lines is not None
        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
    ):
        payload["lazy_reason"] = (
            f"{entry.display_name} has {entry.changed_lines} changed lines, "
            "so it is folded by default. Click to fetch and open it."
        )
    return payload
"""
    right_text = """def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "file_kind": _file_kind_for_repo_entry(entry),
    }
    lazy = _lazy_reason_for_repo_entry(entry)
    if lazy is not None:
        payload["lazy"] = lazy
    return payload
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-statuses-for-real-lazy-manifest-hunk",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert [row["status"] for row in rows] == [
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
    ]
    condition_row = next(
        row
        for row in rows
        if row.get("right_text") == "    if lazy is not None:"
    )
    assert condition_row["right_tokens"] == [
        {"text": "    if ", "status": "unchanged", "is_ws": False},
        {"text": "lazy", "status": "insert", "is_ws": False},
        {"text": " is not None:", "status": "unchanged", "is_ws": False},
    ]


def test_difftastic_rows_statuses_for_real_file_kind_assignment_hunk() -> None:
    """Pin row classification for a real File-kind assignment change.

    Similar assignments and punctuation surround the edit, exercising the
    distinction between unchanged context, replacement, and insertion.
    """
    left_text = """            right_path_hint=normalized_right,
        )
        payload["change_type"] = change_type
        payload["left_path"] = normalized_left
"""
    right_text = """            right_path_hint=normalized_right,
        )
        payload["file_kind"] = _file_kind_for_change_type(
            change_type,
            file_kind=file_kind,
        )
        payload["left_path"] = normalized_left
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-statuses-for-real-file-kind-assignment-hunk",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "            right_path_hint=normalized_right,",
            "right_text": "            right_path_hint=normalized_right,",
            "left_tokens": [],
            "right_tokens": [],
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": "        )",
            "right_text": "        )",
            "left_tokens": [],
            "right_tokens": [],
        },
        {
            "status": "equal",
            "left_no": 3,
            "right_no": 3,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = '
            "_file_kind_for_change_type(",
            "left_tokens": [
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "payload", "status": "delete", "is_ws": False},
                {"text": "[", "status": "delete", "is_ws": False},
                {"text": '"', "status": "delete", "is_ws": False},
                {"text": "change_type", "status": "delete", "is_ws": False},
                {"text": '"', "status": "delete", "is_ws": False},
                {"text": "]", "status": "delete", "is_ws": False},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "=", "status": "delete", "is_ws": False},
                {"text": " ", "status": "delete", "is_ws": True},
                {"text": "change_type", "status": "delete", "is_ws": False},
            ],
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "payload", "status": "insert", "is_ws": False},
                {"text": "[", "status": "insert", "is_ws": False},
                {"text": '"', "status": "insert", "is_ws": False},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": '"', "status": "insert", "is_ws": False},
                {"text": "]", "status": "insert", "is_ws": False},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "=", "status": "insert", "is_ws": False},
                {"text": " ", "status": "insert", "is_ws": True},
                {
                    "text": "_file_kind_for_change_type",
                    "status": "insert",
                    "is_ws": False,
                },
                {"text": "(", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 4,
            "left_text": "",
            "right_text": "            change_type,",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "change_type", "status": "insert", "is_ws": False},
                {"text": ",", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 5,
            "left_text": "",
            "right_text": "            file_kind=file_kind,",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": "=", "status": "insert", "is_ws": False},
                {"text": "file_kind", "status": "insert", "is_ws": False},
                {"text": ",", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "insert",
            "left_no": None,
            "right_no": 6,
            "left_text": "",
            "right_text": "        )",
            "right_tokens": [
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": " ", "status": "insert", "is_ws": True},
                {"text": ")", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "equal",
            "left_no": 4,
            "right_no": 7,
            "left_text": '        payload["left_path"] = normalized_left',
            "right_text": '        payload["left_path"] = normalized_left',
            "left_tokens": [],
            "right_tokens": [],
        },
    ]


def test_difftastic_rows_classify_runtime_config_service_tail_tokens() -> None:
    """Classify runtime-config tail rows from reported and synthesised tokens.

    Synthesised whitespace becomes unchanged and therefore prevents a uniform
    deletion verdict. Omitted punctuation or absent spans likewise keep the
    remaining rows equal.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-copy",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    deleted_tail = [
        row
        for row in rows
        if row.get("left_no") in {29, 30, 31, 32, 33, 34, 35, 36, 37}
        and row.get("left_text")
        in {
            "        service,",
            "        defaults,",
            '        services={"git": git_service, "difftastic": difftastic_service},',
            "        preset_services={",
            '            "dirdiff": preset_service,',
            '            "git": preset_git_service,',
            '            "difftastic": preset_difftastic_service,',
            "        },",
            "    )",
        }
    ]

    assert [row["status"] for row in deleted_tail] == [
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
        "equal",
    ]
    assert all(row["right_no"] is None for row in deleted_tail)
    assert all(row["right_text"] == "" for row in deleted_tail)


def test_difftastic_rows_keep_member_access_dot_unchanged_across_wrapped_pair() -> (
    None
):
    """Keep a shared member-access dot unchanged across wrapped paired rows.

    Punctuation between stable members is context; wrapping alone must not
    repaint it as part of the adjacent replacement.
    """
    left_text = """repo_root = (
    Path(config.repo_root).expanduser() if config.repo_root else None
)
"""
    right_text = """def handle_mark_command(args: argparse.Namespace) -> None:
"""
    extension = "py"
    diff_json = tool_json(
        "difftastic",
        "focused/rows-keep-member-access-dot-unchanged-across-wrapped-pair",
        left=ToolText(left_text, f"old.{extension}"),
        right=ToolText(right_text, f"new.{extension}"),
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )

    left_row = next(
        row
        for row in rows
        if row["left_text"]
        == "    Path(config.repo_root).expanduser() if config.repo_root else None"
    )
    right_row = next(
        row
        for row in rows
        if row["right_text"]
        == "def handle_mark_command(args: argparse.Namespace) -> None:"
    )

    assert left_row["left_tokens"] == [
        {"text": "    ", "status": "unchanged", "is_ws": True},
        {"text": "Path", "status": "delete", "is_ws": False},
        {"text": "(", "status": "delete", "is_ws": False},
        {"text": "config", "status": "delete", "is_ws": False},
        {"text": ".", "status": "delete", "is_ws": False},
        {"text": "repo_root", "status": "delete", "is_ws": False},
        {"text": ")", "status": "delete", "is_ws": False},
        {"text": ".", "status": "delete", "is_ws": False},
        {"text": "expanduser", "status": "delete", "is_ws": False},
        {"text": "(", "status": "delete", "is_ws": False},
        {"text": ")", "status": "delete", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "if", "status": "delete", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "config", "status": "delete", "is_ws": False},
        {"text": ".", "status": "unchanged", "is_ws": False},
        {"text": "repo_root", "status": "delete", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "else", "status": "delete", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "None", "status": "delete", "is_ws": False},
    ]
    assert left_row["status"] == "equal"
    assert {"text": ".", "status": "unchanged", "is_ws": False} in right_row[
        "right_tokens"
    ]

    closing_row = next(row for row in rows if row["left_text"] == ")")
    assert closing_row["status"] == "equal"
    assert closing_row["left_tokens"] == []


def test_difftastic_rows_keep_inserted_structural_closer_context_unchanged() -> (
    None
):
    """Keep a structural closer unchanged beside inserted content.

    The closer already belongs to both sources. Its new line placement does not
    make it an insertion when Difftastic reports it as shared context.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-copy",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    paired_row = next(
        row
        for row in rows
        if row["left_text"]
        == "    preset_service = TextDiffService(preset_repo)"
    )
    closing_row = next(
        row for row in rows if row["right_text"] == "            )"
    )

    assert paired_row["left_tokens"][-1] == {
        "text": ")",
        "status": "unchanged",
        "is_ws": False,
    }
    assert paired_row["right_tokens"][-1] == {
        "text": "(",
        "status": "unchanged",
        "is_ws": False,
    }
    assert closing_row["status"] == "equal"
    assert closing_row["right_tokens"] == []


def test_difftastic_rows_keep_defaults_argument_punctuation_context_unchanged() -> (
    None
):
    """Preserve shared punctuation around a changed defaults argument.

    Only the argument content should be painted; commas and delimiters that
    remain in both sources stay unchanged context.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-copy",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    right_row = next(
        row for row in rows if row["left_text"] == "        right=config.right,"
    )
    review_branch_row = next(
        row
        for row in rows
        if row["left_text"] == "        review_branch=config.review_branch,"
    )

    assert right_row["left_tokens"][-1] == {
        "text": ",",
        "status": "unchanged",
        "is_ws": False,
    }
    assert review_branch_row["left_tokens"][2] == {
        "text": "=",
        "status": "unchanged",
        "is_ws": False,
    }


def test_difftastic_rows_do_not_paint_defaults_right_comma_as_delete() -> None:
    """Never mark a right-side defaults comma with a deletion status.

    Side and token classifications must agree. A token present on the new side
    can be unchanged, inserted, or replaced, but not deleted.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-copy",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    row = next(
        row for row in rows if row["left_text"] == "        right=config.right,"
    )

    assert row["left_tokens"][-1] == {
        "text": ",",
        "status": "unchanged",
        "is_ws": False,
    }
    assert row["status"] == "equal"


def test_difftastic_rows_do_not_paint_review_branch_equals_as_delete() -> None:
    """Never paint the new-side review-branch equals sign as deleted.

    This real alignment once leaked an old-side verdict onto shared right-side
    punctuation; the regression fixes side-local token status.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-copy/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-copy",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    row = next(
        row
        for row in rows
        if row["left_text"] == "        review_branch=config.review_branch,"
    )

    assert row["left_tokens"][2] == {
        "text": "=",
        "status": "unchanged",
        "is_ws": False,
    }
    assert row["status"] == "equal"


def test_difftastic_rows_keep_normalized_base_branch_comma_unchanged() -> None:
    """Keep the normalized base-branch comma as unchanged context.

    Neighboring expression changes do not alter this delimiter, so its token
    must remain stable after structural normalization.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-normalized",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    row = next(
        row
        for row in rows
        if row["left_text"] == "        base_branch=config.base_branch,"
    )

    assert row["left_tokens"][-1] == {
        "text": ",",
        "status": "unchanged",
        "is_ws": False,
    }


def test_difftastic_rows_keep_normalized_services_mapping_comma_unchanged() -> (
    None
):
    """Keep the services-mapping comma unchanged after normalized alignment.

    The regression checks that shared punctuation does not inherit a changed
    status from an adjacent mapping value.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-normalized",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    row = next(row for row in rows if row.get("left_no") == 31)

    assert row["left_text"] == (
        '        services={"git": git_service, "difftastic": difftastic_service},'
    )
    assert row["left_tokens"][-1] == {
        "text": ",",
        "status": "unchanged",
        "is_ws": False,
    }


def test_difftastic_rows_keep_normalized_create_app_closer_unchanged() -> None:
    """Keep the normalized `create_app` closer as unchanged context.

    Structural alignment may move the delimiter between rows, but its source
    identity and token status remain shared.
    """
    old_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/old.py"
    )
    new_path = (
        PRESETS_ROOT
        / "python/create-app-runtime-config-collapses-service-block-normalized/new.py"
    )
    old_text = old_path.read_bytes().decode("utf-8")
    new_text = new_path.read_bytes().decode("utf-8")
    diff_json = tool_json(
        "difftastic",
        "presets/python/create-app-runtime-config-collapses-service-block-normalized",
        left=old_path,
        right=new_path,
    )
    rows = difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )

    row = next(row for row in rows if row.get("left_no") == 37)

    assert row["left_text"] == "    )"
    assert row["left_tokens"] == []
    assert row["status"] == "equal"
