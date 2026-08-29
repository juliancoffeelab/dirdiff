"""Check Difftastic row construction against focused alignment cases.

The tests combine sparse Difftastic facts with complete source text and assert
lossless rows, token status, and line status. They call private row-building
helpers where the public result cannot isolate an alignment invariant.
Subprocess execution and final API serialization are outside this module.
"""

import re
from pathlib import Path

from dirdiff.engines import DiffEngineRow, DiffSide, InlineToken
from dirdiff.engines.difftastic import DifftasticDiffEngine
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


def _preset_rows(preset_name: str) -> list[DiffEngineRow]:
    """Run and project one named two-sided fixture through real Difftastic.

    `preset_name` is relative to `PRESETS_ROOT` and must identify exactly one
    old and new source file. The helper returns neutral rows before display
    enrichment.

    # Usage

    Focused regressions call this when a checked-in fixture already expresses
    the source pair more clearly than inline strings.

    # Failures

    Raises `StopIteration` when either fixture side is absent. Difftastic and
    row-validation failures propagate.
    """
    preset_dir = PRESETS_ROOT / preset_name
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    service = DifftasticDiffEngine()
    diff_json = service._run_difftastic_json(
        left_text=old_text,
        right_text=new_text,
        left_path_hint=old_path.name,
        right_path_hint=new_path.name,
    )
    return difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )


def _text_rows(
    *,
    left_text: str,
    right_text: str,
    extension: str = "ts",
) -> list[DiffEngineRow]:
    """Build Difftastic rows for an inline source pair.

    # Parameters

    - `left_text`: Complete old source used by Difftastic and row building.
    - `right_text`: Complete new source under the same contract.
    - `extension`: Parser-selecting suffix assigned to both temporary files.

    # Usage

    Focused regressions call this when the exact source pair belongs beside the
    assertion. The returned rows have no display enrichment.

    # Failures

    Difftastic execution, JSON validation, and row-validation failures
    propagate.
    """
    service = DifftasticDiffEngine()
    diff_json = service._run_difftastic_json(
        left_text=left_text,
        right_text=right_text,
        left_path_hint=f"old.{extension}",
        right_path_hint=f"new.{extension}",
    )
    return difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )


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
    source = "value = 1\nprint(value)\n"

    payload = DifftasticDiffEngine().render_diff(
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


def test_difftastic_summary_counts_makefile_target_suffix_insert() -> None:
    """Count a Make target suffix as inserted content, not unchanged context.

    This fixture guards the summary derived from Difftastic tokens when an
    existing target grows one syntactic suffix.
    """
    preset_dir = (
        PRESETS_ROOT
        / "makefile"
        / "makefile-target-dependency-suffix-not-counted"
    )
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))

    payload = DifftasticDiffEngine().render_diff(
        old=DiffSide(
            exists=True,
            text=old_path.read_text(),
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=new_path.read_text(),
            path_hint=new_path.name,
        ),
    )

    assert payload["summary"] == {
        "changed_lines": 1,
        "modified_lines": 1,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }


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
    old_text = old_path.read_text()
    new_text = new_path.read_text()
    rendered = DifftasticDiffEngine().render_diff(
        old=DiffSide(
            exists=True,
            text=old_text,
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=new_text,
            path_hint=new_path.name,
        ),
    )

    display = enrich_rows_for_display(
        rows=rendered["rows"],
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


def test_difftastic_summary_counts_makefile_wrapped_command_suffix_inserts() -> (
    None
):
    """Count suffix additions on wrapped Make commands as changed lines.

    The regression covers repeated structural punctuation around an inserted
    command fragment, where line summary must follow token changes.
    """
    preset_dir = (
        PRESETS_ROOT
        / "makefile"
        / "makefile-wrapped-target-dependency-suffix-not-counted"
    )
    old_path = next(preset_dir.glob("old.*"))
    new_path = next(preset_dir.glob("new.*"))

    payload = DifftasticDiffEngine().render_diff(
        old=DiffSide(
            exists=True,
            text=old_path.read_text(),
            path_hint=old_path.name,
        ),
        new=DiffSide(
            exists=True,
            text=new_path.read_text(),
            path_hint=new_path.name,
        ),
    )

    assert payload["summary"] == {
        "changed_lines": 2,
        "modified_lines": 2,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }


def test_difftastic_z_enum_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
    """Keep existing Z enum members out of one-sided change rows.

    Structural expansion may realign the surrounding list, but unchanged member
    words must remain context rather than contradictory inserted/deleted text.
    """
    rows = _preset_rows("typescript/z-enum-adds-top-level-member")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_filter_expansion_does_not_render_existing_condition_as_one_sided_change() -> (
    None
):
    """Keep the original filter condition as context when its body expands.

    This catches Difftastic alignments that place shared condition text on a
    one-sided row while marking every meaningful token unchanged.
    """
    rows = _preset_rows("typescript/filter-condition-adds-top-level-kind")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_python_literal_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
    """Keep shared Python literal members paired through list expansion.

    Added members may change wrapping, but pre-existing words cannot become
    one-sided rows containing only unchanged tokens.
    """
    rows = _preset_rows("python/python-literal-adds-top-level-kind")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_json_rows_use_structural_alignment_and_changed_ranges() -> (
    None
):
    """Project Difftastic alignment and byte ranges into lossless row tokens.

    The test fixes the boundary between external structural facts and original
    source text: alignment chooses rows, while changed ranges choose tokens.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [1, 1], [None, 2], [None, 3], [None, 4]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 11, "end": 12}],
                        },
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 11, "end": 12}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 4, "end": 10}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 11, "end": 12}],
                        },
                    },
                ]
            ],
        },
        left_text="def alpha():\n    return 1\n",
        right_text="def alpha():\n    return 2\n\ndef beta():\n    return 3\n",
    )

    # The blank third row exists only on the right; its presence is the
    # change, so it renders as an insertion rather than unchanged context.
    assert [row["status"] for row in rows] == [
        "equal",
        "replace",
        "insert",
        "replace",
        "replace",
    ]
    assert rows[1]["left_tokens"] == [
        {"text": "    return ", "status": "unchanged", "is_ws": False},
        {"text": "1", "status": "replace", "is_ws": False},
    ]
    assert rows[3]["right_text"] == "def beta():"
    assert rows[3]["right_tokens"] == [
        {"text": "def ", "status": "unchanged", "is_ws": False},
        {"text": "beta()", "status": "insert", "is_ws": False},
        {"text": ":", "status": "unchanged", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_unchanged_tail_after_split_call_change() -> (
    None
):
    """Do not synthesize a shared call tail after Difftastic splits the call.

    Row building must follow supplied alignment exactly; reconstructing attractive
    context locally can duplicate or reorder source fragments.
    """
    rows = _text_rows(
        left_text="return compute(foo.bar, baz);\n",
        right_text="return compute(\n  foo.barWrapped,\n  baz,\n);\n",
    )

    assert rows[0]["status"] == "replace"
    assert rows[0]["left_text"] == "return compute(foo.bar, baz);"
    assert rows[0]["right_text"] == "return compute("
    assert rows[0]["left_tokens"] == [
        {"text": "return compute(foo.", "status": "unchanged", "is_ws": False},
        {"text": "bar", "status": "delete", "is_ws": False},
        {"text": ", baz);", "status": "unchanged", "is_ws": False},
    ]
    assert rows[1]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 2, "end": 6},
                                {"start": 6, "end": 7},
                                {"start": 7, "end": 19},
                                {"start": 19, "end": 20},
                            ],
                        }
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 7, "end": 8}],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 16, "end": 21},
                                {"start": 23, "end": 27},
                                {"start": 27, "end": 28},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="const x = make([alpha, keep, omega]);\n",
        right_text="const x = make([\n  wrap(alphaChanged),\n  omega,\n]);\n",
    )

    assert rows[2]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 12, "end": 16},
                                {"start": 16, "end": 17},
                                {"start": 17, "end": 30},
                                {"start": 30, "end": 31},
                            ],
                        }
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 17, "end": 18}],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 22, "end": 27},
                                {"start": 29, "end": 34},
                                {"start": 34, "end": 35},
                                {"start": 36, "end": 40},
                                {"start": 40, "end": 41},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text='value = make({"left": thing, "mid": keep, "right": done})\n',
        right_text=(
            'value = make({\n    "left": wrap(thing_changed),\n    "right": done,\n})\n'
        ),
    )

    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == '    "right": done,'
    assert rows[2]["right_tokens"] == [
        {"text": '    "right": done', "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_clojure_vector_tail_after_wrap() -> (
    None
):
    """Keep a wrapped Clojure vector tail on Difftastic's chosen rows.

    Row building must not rebuild shared delimiters into a synthetic pair after
    structural wrapping changes their line placement.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 2, "end": 3},
                                {"start": 3, "end": 7},
                                {"start": 8, "end": 15},
                                {"start": 15, "end": 16},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 9, "end": 16},
                                {"start": 17, "end": 21},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="(render [foo-bar keep baz])\n",
        right_text="(render [\n  (wrap foo-baz)\n  baz\n])\n",
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 8, "end": 9},
                                {"start": 9, "end": 13},
                                {"start": 14, "end": 27},
                                {"start": 27, "end": 28},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 15, "end": 20},
                                {"start": 21, "end": 26},
                                {"start": 27, "end": 31},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="(render {:left thing :tail keep :end done})\n",
        right_text="(render {\n  :left (wrap thing-changed)\n  :end done\n})\n",
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 4, "end": 8},
                                {"start": 8, "end": 9},
                                {"start": 12, "end": 14},
                                {"start": 14, "end": 15},
                            ],
                        }
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 8, "end": 9}],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 20, "end": 22},
                                {"start": 24, "end": 28},
                                {"start": 28, "end": 29},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = call(5..10, keep, tail);\n",
        right_text="let value = call(\n    wrap(5..20),\n    tail,\n);\n",
    )

    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "    tail,"
    assert rows[2]["right_tokens"] == [
        {"text": "    tail", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_rust_range_inclusive_tail_after_wrap() -> (
    None
):
    """Preserve inclusive Rust range syntax without reconstructed context.

    This variant fixes the same replay boundary for `..=` tokens, whose shared
    punctuation previously invited an incorrect paired tail.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 4, "end": 8},
                                {"start": 8, "end": 9},
                                {"start": 13, "end": 15},
                                {"start": 15, "end": 16},
                            ],
                        }
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 8, "end": 9}],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 21, "end": 23},
                                {"start": 25, "end": 29},
                                {"start": 29, "end": 30},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = call(5..=10, keep, tail);\n",
        right_text="let value = call(\n    wrap(5..=20),\n    tail,\n);\n",
    )

    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "    tail,"
    assert rows[2]["right_tokens"] == [
        {"text": "    tail", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_ocaml_atat_tail_after_wrap() -> (
    None
):
    """Keep OCaml `@@` tail fragments on their structurally aligned rows.

    Row building may decorate the exact source slices but must not repair the
    external alignment around repeated application operators.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 5, "end": 9},
                                {"start": 10, "end": 17},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 22, "end": 29},
                                {"start": 30, "end": 34},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = render @@ compute keep tail\n",
        right_text="let value =\n  render\n  @@ wrap changed\n  tail\n",
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  render"
    assert rows[2]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 5, "end": 9},
                                {"start": 10, "end": 17},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 21, "end": 26},
                                {"start": 27, "end": 31},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = outer @@ inner keep tail\n",
        right_text="let value =\n  outer\n  @@ wrap changed\n  tail\n",
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  outer"
    assert rows[2]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {"rhs": {"line_number": 1, "changes": []}},
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 5, "end": 9},
                                {"start": 10, "end": 17},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 21, "end": 25},
                                {"start": 26, "end": 30},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = input |> step keep |> tail\n",
        right_text="let value =\n  input\n  |> wrap changed\n  |> tail\n",
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  input"
    assert rows[2]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [None, 3], [1, 4]],
            "chunks": [
                [
                    {"rhs": {"line_number": 1, "changes": []}},
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 5, "end": 9},
                                {"start": 10, "end": 17},
                            ],
                        }
                    },
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [
                                {"start": 21, "end": 26},
                                {"start": 27, "end": 31},
                            ],
                        },
                        "rhs": {"line_number": 0, "changes": []},
                    },
                ]
            ],
        },
        left_text="let value = input |> first keep |> second tail\n",
        right_text="let value =\n  input\n  |> wrap changed\n  |> second tail\n",
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] is None
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == ""
    assert rows[1]["right_text"] == "  input"
    assert rows[2]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [1, None], [2, 1]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [{"start": 2, "end": 8}],
                        },
                        "rhs": {
                            "line_number": 0,
                            "changes": [{"start": 2, "end": 5}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 2, "end": 28}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "  let cursor = 0;\n"
            "  for (const span of syntax) {\n"
            "    const start = clamp(span.start, 0, text.length);\n"
        ),
        right_text=(
            "  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {\n"
            "    const start = sortedBoundaries[index];\n"
        ),
    )

    assert rows[0]["status"] == "replace"
    assert rows[0]["right_text"].endswith(") {")
    assert rows[1]["status"] == "replace"
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, None],
                [3, 2],
                [None, 3],
                [4, 4],
                [5, None],
                [6, None],
                [7, None],
                [8, 5],
                [9, None],
                [10, None],
                [11, None],
                [12, 6],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 8, "end": 40}],
                        },
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 8, "end": 53}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 8, "end": 21}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 4, "end": 45}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 4,
                            "changes": [{"start": 7, "end": 8}],
                        },
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 7, "end": 11}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 5,
                            "changes": [{"start": 8, "end": 27}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 6,
                            "changes": [{"start": 8, "end": 68}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 7,
                            "changes": [{"start": 4, "end": 5}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 8,
                            "changes": [{"start": 16, "end": 34}],
                        },
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 16, "end": 30}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 9,
                            "changes": [{"start": 12, "end": 77}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 10,
                            "changes": [{"start": 12, "end": 69}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 11,
                            "changes": [{"start": 8, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            '        "right_path": entry.right_path,\n'
            '        "change_type": entry.change_type,\n'
            '        "lazy": True,\n'
            "    }\n"
            "    if (\n"
            "        entry.changed_lines is not None\n"
            "        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD\n"
            "    ):\n"
            '        payload["lazy_reason"] = (\n'
            '            f"{entry.display_name} has {entry.changed_lines} changed lines, "\n'
            '            "so it is folded by default. Click to fetch and open it."\n'
            "        )\n"
            "    return payload\n"
        ),
        right_text=(
            '        "right_path": entry.right_path,\n'
            '        "file_kind": _file_kind_for_repo_entry(entry),\n'
            "    }\n"
            "    lazy = _lazy_reason_for_repo_entry(entry)\n"
            "    if lazy is not None:\n"
            '        payload["lazy"] = lazy\n'
            "    return payload\n"
        ),
    )

    right_numbers = [
        right_no
        for row in rows
        if isinstance((right_no := row.get("right_no")), int)
    ]

    assert right_numbers == sorted(set(right_numbers))


def test_difftastic_rows_keep_collapsed_condition_suffix_unchanged() -> None:
    """Keep the shared suffix of a multi-line condition as structural context.

    Only the condition change should receive changed tokens; stable trailing
    syntax must remain unchanged even when its row pairing shifts.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, None],
                [3, 2],
                [None, 3],
                [4, 4],
                [5, None],
                [6, None],
                [7, None],
                [8, 5],
                [9, None],
                [10, None],
                [11, None],
                [12, 6],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 8, "end": 40}],
                        },
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 8, "end": 53}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 8, "end": 21}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 4, "end": 45}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 4,
                            "changes": [{"start": 7, "end": 8}],
                        },
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 7, "end": 11}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 5,
                            "changes": [{"start": 8, "end": 27}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 6,
                            "changes": [{"start": 8, "end": 68}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 7,
                            "changes": [{"start": 4, "end": 5}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 8,
                            "changes": [{"start": 16, "end": 34}],
                        },
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 16, "end": 30}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 9,
                            "changes": [{"start": 12, "end": 77}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 10,
                            "changes": [{"start": 12, "end": 69}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 11,
                            "changes": [{"start": 8, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            '        "right_path": entry.right_path,\n'
            '        "change_type": entry.change_type,\n'
            '        "lazy": True,\n'
            "    }\n"
            "    if (\n"
            "        entry.changed_lines is not None\n"
            "        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD\n"
            "    ):\n"
            '        payload["lazy_reason"] = (\n'
            '            f"{entry.display_name} has {entry.changed_lines} changed lines, "\n'
            '            "so it is folded by default. Click to fetch and open it."\n'
            "        )\n"
            "    return payload\n"
        ),
        right_text=(
            '        "right_path": entry.right_path,\n'
            '        "file_kind": _file_kind_for_repo_entry(entry),\n'
            "    }\n"
            "    lazy = _lazy_reason_for_repo_entry(entry)\n"
            "    if lazy is not None:\n"
            '        payload["lazy"] = lazy\n'
            "    return payload\n"
        ),
    )

    condition_row = next(row for row in rows if row.get("right_no") == 5)

    assert condition_row["status"] == "replace"
    assert condition_row["right_tokens"] == [
        {"text": "    if ", "status": "unchanged", "is_ws": False},
        {"text": "lazy", "status": "replace", "is_ws": False},
        {"text": " is not None:", "status": "unchanged", "is_ws": False},
    ]


def test_difftastic_rows_do_not_reconstruct_assignment_rhs_as_insert_argument() -> (
    None
):
    """Do not reinterpret an assignment value as a newly inserted argument.

    Similar source text appears in two syntax roles. Row building must honor
    Difftastic's ranges instead of pairing by textual coincidence.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [None, 2],
                [None, 3],
                [None, 4],
                [2, 5],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 16, "end": 29}],
                        },
                        "rhs": {
                            "line_number": 1,
                            "changes": [
                                {"start": 16, "end": 27},
                                {"start": 31, "end": 57},
                                {"start": 57, "end": 58},
                            ],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 23, "end": 24}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [
                                {"start": 12, "end": 21},
                                {"start": 21, "end": 22},
                                {"start": 22, "end": 31},
                                {"start": 31, "end": 32},
                            ],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 8, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "        )\n"
            '        payload["change_type"] = change_type\n'
            '        payload["left_path"] = normalized_left\n'
        ),
        right_text=(
            "        )\n"
            '        payload["file_kind"] = _file_kind_for_change_type(\n'
            "            change_type,\n"
            "            file_kind=file_kind,\n"
            "        )\n"
            '        payload["left_path"] = normalized_left\n'
        ),
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
            "status": "replace",
            "left_no": 2,
            "right_no": 2,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = _file_kind_for_change_type(',
            "left_tokens": [
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"change_type"', "status": "delete", "is_ws": False},
                {
                    "text": "] = change_type",
                    "status": "unchanged",
                    "is_ws": False,
                },
            ],
            "right_tokens": [
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"file_kind"', "status": "insert", "is_ws": False},
                {"text": "] = ", "status": "unchanged", "is_ws": False},
                {
                    "text": "_file_kind_for_change_type",
                    "status": "insert",
                    "is_ws": False,
                },
                {"text": "(", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "replace",
            "left_no": None,
            "right_no": 3,
            "left_text": "",
            "right_text": "            change_type,",
            "right_tokens": [
                {
                    "text": "            change_type",
                    "status": "unchanged",
                    "is_ws": False,
                },
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
                {"text": "            ", "status": "unchanged", "is_ws": True},
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
                {"text": "        ", "status": "unchanged", "is_ws": True},
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
    rows = _preset_rows(
        "typescript/typescript-repo-fold-controls-show-placeholder-aligns-poorly"
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
    rows = _text_rows(
        left_text="return compute(foo.bar, baz);\n",
        right_text="return compute(\n  foo.barWrapped,\n  baz,\n);\n",
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


def test_difftastic_rows_status_is_replace_for_real_mixed_unchanged_and_insert_tokens() -> (
    None
):
    """Classify mixed unchanged and inserted token context as replacement.

    A line retaining meaningful old context is not a pure insertion even when
    every changed token appears on the right.
    """
    rows = _text_rows(
        left_text="return compute(foo.bar, baz);\n",
        right_text="return compute(\n  foo.barWrapped,\n  baz,\n);\n",
    )

    assert rows[1] == {
        "status": "replace",
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
    rows = _text_rows(
        left_text="value = arg\n",
        right_text="value = arg\nnew_value\n",
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


def test_difftastic_rows_status_is_replace_when_changed_tokens_are_not_inserted() -> (
    None
):
    """Use replacement when changed tokens do not form a pure insertion.

    Mixed or paired changed classifications must not combine into a one-sided
    row status merely because the alignment is asymmetric.
    """
    rows = _text_rows(
        left_text="value = old\n",
        right_text="value = new\n",
    )

    assert rows == [
        {
            "status": "replace",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = old",
            "right_text": "value = new",
            "left_tokens": [
                {"text": "value = ", "status": "unchanged", "is_ws": False},
                {"text": "old", "status": "replace", "is_ws": False},
            ],
            "right_tokens": [
                {"text": "value = ", "status": "unchanged", "is_ws": False},
                {"text": "new", "status": "replace", "is_ws": False},
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
    rows = _text_rows(
        left_text="value = arg\nold_value\n",
        right_text="value = arg\n",
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
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, 2],
                [3, None],
                [4, 3],
                [None, 4],
                [5, 5],
                [6, None],
                [7, None],
                [8, None],
                [9, 6],
                [10, None],
                [11, None],
                [12, None],
                [13, 7],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 8, "end": 21}],
                        },
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 8, "end": 19},
                                {"start": 21, "end": 53},
                            ],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 3,
                            "changes": [{"start": 8, "end": 21}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 4, "end": 45}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 5,
                            "changes": [{"start": 7, "end": 8}],
                        },
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 7, "end": 11}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 6,
                            "changes": [{"start": 8, "end": 27}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 7,
                            "changes": [{"start": 8, "end": 68}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 8,
                            "changes": [{"start": 4, "end": 5}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 9,
                            "changes": [{"start": 16, "end": 34}],
                        },
                        "rhs": {
                            "line_number": 6,
                            "changes": [
                                {"start": 16, "end": 22},
                                {"start": 26, "end": 30},
                            ],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 10,
                            "changes": [{"start": 12, "end": 77}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 11,
                            "changes": [{"start": 12, "end": 69}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 12,
                            "changes": [{"start": 8, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:\n"
            "    payload: dict[str, Any] = {\n"
            '        "change_type": entry.change_type,\n'
            '        "lazy": True,\n'
            "    }\n"
            "    if (\n"
            "        entry.changed_lines is not None\n"
            "        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD\n"
            "    ):\n"
            '        payload["lazy_reason"] = (\n'
            '            f"{entry.display_name} has {entry.changed_lines} changed lines, "\n'
            '            "so it is folded by default. Click to fetch and open it."\n'
            "        )\n"
            "    return payload\n"
        ),
        right_text=(
            "def _to_lazy_repo_manifest_file_entry(entry: RepoDiffPath) -> dict[str, Any]:\n"
            "    payload: dict[str, Any] = {\n"
            '        "file_kind": _file_kind_for_repo_entry(entry),\n'
            "    }\n"
            "    lazy = _lazy_reason_for_repo_entry(entry)\n"
            "    if lazy is not None:\n"
            '        payload["lazy"] = lazy\n'
            "    return payload\n"
        ),
    )

    assert [row["status"] for row in rows] == [
        "equal",
        "equal",
        "replace",
        "delete",
        "equal",
        "insert",
        "replace",
        "replace",
        "delete",
        "replace",
        "replace",
        "delete",
        "delete",
        "delete",
        "equal",
    ]
    condition_row = next(
        row
        for row in rows
        if row.get("right_text") == "    if lazy is not None:"
    )
    assert condition_row["right_tokens"] == [
        {"text": "    if ", "status": "unchanged", "is_ws": False},
        {"text": "lazy", "status": "replace", "is_ws": False},
        {"text": " is not None:", "status": "unchanged", "is_ws": False},
    ]


def test_difftastic_rows_statuses_for_real_file_kind_assignment_hunk() -> None:
    """Pin row classification for a real File-kind assignment change.

    Similar assignments and punctuation surround the edit, exercising the
    distinction between unchanged context, replacement, and insertion.
    """
    rows = difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, 2],
                [None, 3],
                [None, 4],
                [None, 5],
                [3, 6],
            ],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 23, "end": 24}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 4,
                            "changes": [
                                {"start": 12, "end": 21},
                                {"start": 21, "end": 22},
                                {"start": 22, "end": 31},
                                {"start": 31, "end": 32},
                            ],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 8, "end": 9}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 16, "end": 29}],
                        },
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 16, "end": 27},
                                {"start": 31, "end": 57},
                                {"start": 57, "end": 58},
                            ],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "            right_path_hint=normalized_right,\n"
            "        )\n"
            '        payload["change_type"] = change_type\n'
            '        payload["left_path"] = normalized_left\n'
        ),
        right_text=(
            "            right_path_hint=normalized_right,\n"
            "        )\n"
            '        payload["file_kind"] = _file_kind_for_change_type(\n'
            "            change_type,\n"
            "            file_kind=file_kind,\n"
            "        )\n"
            '        payload["left_path"] = normalized_left\n'
        ),
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
            "status": "replace",
            "left_no": 3,
            "right_no": 3,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = _file_kind_for_change_type(',
            "left_tokens": [
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"change_type"', "status": "delete", "is_ws": False},
                {
                    "text": "] = change_type",
                    "status": "unchanged",
                    "is_ws": False,
                },
            ],
            "right_tokens": [
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"file_kind"', "status": "insert", "is_ws": False},
                {"text": "] = ", "status": "unchanged", "is_ws": False},
                {
                    "text": "_file_kind_for_change_type",
                    "status": "insert",
                    "is_ws": False,
                },
                {"text": "(", "status": "insert", "is_ws": False},
            ],
        },
        {
            "status": "replace",
            "left_no": None,
            "right_no": 4,
            "left_text": "",
            "right_text": "            change_type,",
            "right_tokens": [
                {
                    "text": "            change_type",
                    "status": "unchanged",
                    "is_ws": False,
                },
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
                {"text": "            ", "status": "unchanged", "is_ws": True},
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
                {"text": "        ", "status": "unchanged", "is_ws": True},
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


def test_difftastic_rows_mark_runtime_config_service_tail_context() -> None:
    """Keep the runtime-config service tail as unchanged context.

    The shared closing portion remains readable context and must not inherit the
    changed status of the preceding structural fragment.
    """
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-copy"
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
        "delete",
        "delete",
        "replace",
        "replace",
        "delete",
        "delete",
        "delete",
        "delete",
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
    rows = _text_rows(
        left_text=(
            "repo_root = (\n"
            "    Path(config.repo_root).expanduser() if config.repo_root else None\n"
            ")\n"
        ),
        right_text=(
            "def handle_mark_command(args: argparse.Namespace) -> None:\n"
        ),
        extension="py",
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
    assert left_row["status"] == "replace"
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-copy"
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-copy"
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-copy"
    )

    row = next(
        row for row in rows if row["left_text"] == "        right=config.right,"
    )

    assert row["left_tokens"][-1] == {
        "text": ",",
        "status": "unchanged",
        "is_ws": False,
    }
    assert row["status"] == "replace"


def test_difftastic_rows_do_not_paint_review_branch_equals_as_delete() -> None:
    """Never paint the new-side review-branch equals sign as deleted.

    This real alignment once leaked an old-side verdict onto shared right-side
    punctuation; the regression fixes side-local token status.
    """
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-copy"
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
    assert row["status"] == "replace"


def test_difftastic_rows_keep_normalized_base_branch_comma_unchanged() -> None:
    """Keep the normalized base-branch comma as unchanged context.

    Neighboring expression changes do not alter this delimiter, so its token
    must remain stable after structural normalization.
    """
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-normalized"
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-normalized"
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-normalized"
    )

    row = next(row for row in rows if row.get("left_no") == 37)

    assert row["left_text"] == "    )"
    assert row["left_tokens"] == []
    assert row["status"] == "equal"
