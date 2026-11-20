import re
from pathlib import Path

from helpers import ToolText, tool_json

from dirdiff.engines import DiffEngineRow, DiffSide
from dirdiff.engines.difftastic import DifftasticDiffEngine, DifftasticJson
from dirdiff.engines.difftastic.logic import (
    difftastic_engine_warning,
    difftastic_rows_from_json,
)
from dirdiff.rendering import enrich_rows_for_display

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "diff"


_WORD_LIKE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")


def _pure_unchanged_one_sided_change_texts(
    rows: list[DiffEngineRow],
) -> list[str]:
    # A deleted or inserted row must mark at least one word-like token as
    # changed; these are the texts of rows that mark none.
    broken_texts: list[str] = []
    for row in rows:
        if row["status"] not in {"delete", "insert"}:
            continue
        if row["left_no"] is not None and row["right_no"] is None:
            tokens = row.get("left_tokens", [])
            text = row["left_text"]
        elif row["left_no"] is None and row["right_no"] is not None:
            tokens = row.get("right_tokens", [])
            text = row["right_text"]
        else:
            continue
        word_tokens = [
            token for token in tokens if _WORD_LIKE.search(token["text"])
        ]
        if word_tokens != [] and all(
            token["status"] == "unchanged" for token in word_tokens
        ):
            broken_texts.append(text)
    return broken_texts


def test_difftastic_engine_warning_reports_graph_limit_fallback() -> None:
    assert difftastic_engine_warning(
        {"language": "Text (exceeded DFT_GRAPH_LIMIT)"}
    ) == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
    }
    assert difftastic_engine_warning({"language": "TypeScript"}) is None


def test_difftastic_engine_keeps_identical_source_rows() -> None:
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

    assert _pure_unchanged_one_sided_change_texts(rows) == []


def test_difftastic_filter_expansion_does_not_render_existing_condition_as_one_sided_change() -> (
    None
):
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

    assert _pure_unchanged_one_sided_change_texts(rows) == []


def test_difftastic_python_literal_expansion_does_not_render_existing_members_as_one_sided_change() -> (
    None
):
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

    assert _pure_unchanged_one_sided_change_texts(rows) == []


def test_difftastic_json_rows_use_structural_alignment_and_changed_ranges() -> (
    None
):
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
    left_changed_atoms = {
        atom
        for row in rows
        if row["left_no"] == 4
        for token in row.get("left_tokens", [])
        if token["status"] != "unchanged"
        for atom in _WORD_LIKE.findall(token["text"])
    }
    right_changed_atoms = {
        atom
        for row in rows
        if row["right_no"] == 6
        for token in row.get("right_tokens", [])
        if token["status"] != "unchanged"
        for atom in _WORD_LIKE.findall(token["text"])
    }

    assert condition_atoms.isdisjoint(left_changed_atoms)
    assert condition_atoms.isdisjoint(right_changed_atoms)


def test_difftastic_rows_status_is_equal_for_real_right_only_context_line() -> (
    None
):
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
