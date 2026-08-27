"""Unit tests for difftastic row projection.

This module feeds sparse difftastic-shaped facts plus source text into the row
projector and asserts the rendered row contract.  It is allowed to use private
projection helpers because the tests pin tricky alignment invariants directly;
it does not test subprocess execution or final API payload assembly.
"""

import re
from pathlib import Path

from dirdiff.engines import DiffSide
from dirdiff.engines.difftastic import (
    DifftasticDiffEngine,
    DifftasticInlineToken,
    DifftasticRow,
)
from dirdiff.engines.difftastic.logic import (
    _difftastic_engine_warning,
    _difftastic_rows_from_json,
)
from dirdiff.rendering import enrich_rows_for_display

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"

__all__: list[str] = []


def _preset_rows(preset_name: str) -> list[DifftasticRow]:
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
    return _difftastic_rows_from_json(
        diff_json,
        left_text=old_text,
        right_text=new_text,
    )


def _text_rows(
    *,
    left_text: str,
    right_text: str,
    extension: str = "ts",
) -> list[DifftasticRow]:
    service = DifftasticDiffEngine()
    diff_json = service._run_difftastic_json(
        left_text=left_text,
        right_text=right_text,
        left_path_hint=f"old.{extension}",
        right_path_hint=f"new.{extension}",
    )
    return _difftastic_rows_from_json(
        diff_json,
        left_text=left_text,
        right_text=right_text,
    )


def _word_like_token_atoms(text: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+", text)


def _pure_unchanged_one_sided_change_texts(
    rows: list[DifftasticRow],
) -> list[str]:
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

        tokens: list[DifftasticInlineToken] | None
        if side == "left":
            tokens = row.get("left_tokens")
        else:
            tokens = row.get("right_tokens")
        if tokens is None:
            continue

        meaningful_tokens: list[DifftasticInlineToken] = []
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
    rows: list[DifftasticRow],
) -> None:
    broken_texts = _pure_unchanged_one_sided_change_texts(rows)
    assert broken_texts == [], broken_texts


def _changed_word_like_atoms_for_line(
    rows: list[DifftasticRow],
    *,
    side: str,
    line_no: int,
) -> list[str]:
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
    assert _difftastic_engine_warning(
        {"language": "Text (exceeded DFT_GRAPH_LIMIT)"}
    ) == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
    }
    assert _difftastic_engine_warning({"language": "TypeScript"}) is None


def test_difftastic_summary_counts_makefile_target_suffix_insert() -> None:
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
    rows = _preset_rows("typescript/z-enum-adds-top-level-member")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_filter_expansion_does_not_render_existing_condition_as_one_sided_change() -> (
    None
):
    rows = _preset_rows("typescript/filter-condition-adds-top-level-kind")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_python_literal_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
    rows = _preset_rows("python/python-literal-adds-top-level-kind")

    _assert_no_pure_unchanged_one_sided_changes(rows)


def test_difftastic_json_rows_use_structural_alignment_and_changed_ranges() -> (
    None
):
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _difftastic_rows_from_json(
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
    rows = _preset_rows(
        "python/create-app-runtime-config-collapses-service-block-normalized"
    )

    row = next(row for row in rows if row.get("left_no") == 37)

    assert row["left_text"] == "    )"
    assert row["left_tokens"] == []
    assert row["status"] == "equal"
