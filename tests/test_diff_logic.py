import json

from dirdiff.diff import (
    _difftastic_engine_warning,
    _difftastic_rows_from_json,
    build_loaded_diff,
)


def test_counts_whitespace_only_changes_as_modified() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="    value = 1\n",
        right_text="\tvalue = 1\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["summary"]["changed_lines"] == 1
    assert diff["summary"]["modified_lines"] == 1
    assert diff["rows"][0]["status"] == "equal"
    assert diff["rows"][0]["left_tokens"]


def test_difftastic_engine_warning_reports_graph_limit_fallback() -> None:
    assert _difftastic_engine_warning(
        {"language": "Text (exceeded DFT_GRAPH_LIMIT)"}
    ) == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
    }
    assert _difftastic_engine_warning({"language": "TypeScript"}) is None


def test_inline_diff_keeps_camel_case_boundaries_intact() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="function findNearestIndex(positions, viewportCenter) {}\n",
        right_text="function positionsSignature(positions) {}\n",
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    left_name_tokens = []
    for token in diff["rows"][0]["left_tokens"]:
        if token["text"] == "(":
            break
        if token["text"] != "function" and not token["is_ws"]:
            left_name_tokens.append((token["text"], (token["status"] != "unchanged")))

    right_name_tokens = []
    for token in diff["rows"][0]["right_tokens"]:
        if token["text"] == "(":
            break
        if token["text"] != "function" and not token["is_ws"]:
            right_name_tokens.append((token["text"], (token["status"] != "unchanged")))

    assert left_name_tokens == [
        ("find", True),
        ("Nearest", True),
        ("Index", True),
    ]
    assert right_name_tokens == [
        ("positions", True),
        ("Signature", True),
    ]


def test_inline_diff_keeps_identifier_parts_whole_in_method_renames() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="await expectVisibleRow(page, 0);\n",
        right_text="await expectSelectedRowIndex(page, 0);\n",
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    left_tokens = [
        (token["text"], (token["status"] != "unchanged"))
        for token in diff["rows"][0]["left_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]
    right_tokens = [
        (token["text"], (token["status"] != "unchanged"))
        for token in diff["rows"][0]["right_tokens"]
        if token["text"] not in {"await", " ", "(", "page", ",", "0", ");"}
    ]

    assert left_tokens == [
        ("expect", False),
        ("Visible", True),
        ("Row", False),
    ]
    assert right_tokens == [
        ("expect", False),
        ("Selected", True),
        ("Row", False),
        ("Index", True),
    ]


def test_difftastic_json_rows_use_semantic_alignment_and_changed_ranges() -> None:
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

    assert [row["status"] for row in rows] == [
        "equal",
        "replace",
        "equal",
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


def test_difftastic_rows_render_split_arguments_as_semantic_context() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [None, 1],
                [None, 2],
                [None, 3],
                [None, 4],
            ],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 38, "end": 50}],
                        },
                    }
                ]
            ],
        },
        left_text='    return create_app(service, defaults, services={"git": git_service})\n',
        right_text=(
            "    return create_app(\n"
            "        service,\n"
            "        defaults,\n"
            '        services={"git": git_service, "difftastic": difftastic_service},\n'
            "    )\n"
        ),
    )

    assert rows[0]["status"] == "equal"
    assert rows[0]["left_text"] == "    return create_app("
    assert rows[0]["right_text"] == "    return create_app("
    assert [row["status"] for row in rows[1:]] == [
        "equal",
        "equal",
        "replace",
        "equal",
    ]
    assert rows[3]["right_tokens"][1]["status"] == "insert"


def test_difftastic_rows_clip_old_tail_for_one_sided_paired_insert() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [None, 1],
                [None, 2],
                [None, 3],
                [None, 4],
                [None, 5],
                [1, 6],
            ],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 0,
                            "changes": [{"start": 25, "end": 26}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 4, "end": 25}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 4,
                            "changes": [{"start": 19, "end": 20}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 0, "end": 1}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "from dirdiff.diff import GitDiffService, GitRepository, TextDiffService\n"
            "from dirdiff.server import create_app\n"
        ),
        right_text=(
            "from dirdiff.diff import (\n"
            "    DifftasticDiffService,\n"
            "    GitDiffService,\n"
            "    GitRepository,\n"
            "    TextDiffService,\n"
            ")\n"
            "from dirdiff.server import create_app\n"
        ),
    )

    assert rows[0]["status"] == "replace"
    assert rows[0]["left_text"] == "from dirdiff.diff import "
    assert rows[0]["right_text"] == "from dirdiff.diff import ("
    assert rows[0]["right_tokens"] == [
        {"text": "from dirdiff.diff import ", "status": "unchanged", "is_ws": False},
        {"text": "(", "status": "insert", "is_ws": False},
    ]
    assert rows[1]["status"] == "replace"
    assert rows[2]["status"] == "equal"
    assert rows[6]["left_text"] == "from dirdiff.server import create_app"


def test_difftastic_rows_pair_one_sided_rhs_token_insert_with_matching_lhs_line() -> (
    None
):
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, 2],
                [3, 3],
                [4, 4],
                [None, 5],
                [None, 6],
                [6, 7],
            ],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 2, "end": 11}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 6,
                            "changes": [{"start": 12, "end": 13}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            'import { createEffect } from "solid-js";\n'
            "import type {\n"
            "  DiffRow,\n"
            "  FileEntry,\n"
            "  InlineToken,\n"
            "  SyntaxSpan\n"
            '} from "./api";\n'
        ),
        right_text=(
            'import { createEffect } from "solid-js";\n'
            "import type {\n"
            "  DiffRow,\n"
            "  FileEntry,\n"
            "  InlineToken,\n"
            "  RowStatus,\n"
            "  SyntaxSpan,\n"
            '} from "./api";\n'
        ),
    )

    assert rows[5]["status"] == "replace"
    assert rows[5]["right_text"] == "  RowStatus,"
    assert rows[6]["status"] == "replace"
    assert rows[6]["left_no"] == 6
    assert rows[6]["right_no"] == 7
    assert rows[6]["left_text"] == "  SyntaxSpan"
    assert rows[6]["right_text"] == "  SyntaxSpan,"
    assert rows[6]["right_tokens"] == [
        {"text": "  SyntaxSpan", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]
    assert rows[7]["left_text"] == '} from "./api";'


def test_difftastic_rows_pair_rhs_token_insert_from_split_lhs_line_tail() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [None, 2],
                [None, 3],
                [None, 4],
                [None, 5],
                [None, 6],
                [None, 7],
                [2, 8],
            ],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 5,
                            "changes": [{"start": 2, "end": 11}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 6,
                            "changes": [{"start": 12, "end": 13}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            'import { createEffect } from "solid-js";\n'
            'import type { DiffRow, FileEntry, InlineToken, SyntaxSpan } from "./api";\n'
            'import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";\n'
        ),
        right_text=(
            'import { createEffect } from "solid-js";\n'
            "import type {\n"
            "  DiffRow,\n"
            "  FileEntry,\n"
            "  InlineToken,\n"
            "  RowStatus,\n"
            "  SyntaxSpan,\n"
            '} from "./api";\n'
            'import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";\n'
        ),
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_text"] == "import type {"
    assert rows[1]["right_text"] == "import type {"
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] == 2
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == "  DiffRow,"
    assert rows[2]["right_text"] == "  DiffRow,"
    assert rows[5]["status"] == "replace"
    assert rows[5]["left_no"] is None
    assert rows[5]["right_text"] == "  RowStatus,"
    assert rows[6]["status"] == "replace"
    assert rows[6]["left_no"] == 2
    assert rows[6]["right_no"] == 7
    assert rows[6]["left_text"] == "  SyntaxSpan"
    assert rows[6]["right_text"] == "  SyntaxSpan,"
    assert rows[6]["right_tokens"] == [
        {"text": "  SyntaxSpan", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]
    assert rows[7]["status"] == "equal"
    assert rows[7]["left_no"] == 2
    assert rows[7]["right_no"] == 8
    assert rows[7]["left_text"] == '} from "./api";'
    assert rows[7]["right_text"] == '} from "./api";'
    assert rows[8]["left_text"].startswith("import { addFoldRows")


def test_difftastic_rows_pair_rhs_lines_from_replaced_lhs_line_tail() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [None, 1],
                [None, 2],
                [None, 3],
                [None, 4],
                [1, 5],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [{"start": 9, "end": 20}],
                        },
                        "rhs": {
                            "line_number": 0,
                            "changes": [{"start": 9, "end": 23}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [
                                {"start": 2, "end": 8},
                                {"start": 8, "end": 9},
                                {"start": 10, "end": 21},
                                {"start": 21, "end": 22},
                                {"start": 22, "end": 23},
                                {"start": 23, "end": 24},
                            ],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 22, "end": 23}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "function syntaxParts(text: string, syntax: SyntaxSpan[]) {\n"
            "  if (!text || !syntax.length) {\n"
        ),
        right_text=(
            "function decoratedParts(\n"
            "  text: string,\n"
            "  tokens: InlineToken[],\n"
            "  syntax: SyntaxSpan[],\n"
            ") {\n"
            "  if (!text || (!tokens.length && !syntax.length)) {\n"
        ),
    )

    assert rows[0]["status"] == "replace"
    assert (
        rows[0]["left_text"]
        == "function syntaxParts(text: string, syntax: SyntaxSpan[]) {"
    )
    assert rows[0]["right_text"] == "function decoratedParts("
    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] == 1
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == "  text: string,"
    assert rows[1]["right_text"] == "  text: string,"
    assert rows[2]["status"] == "insert"
    assert rows[2]["right_text"] == "  tokens: InlineToken[],"
    assert rows[3]["status"] == "replace"
    assert rows[3]["left_no"] == 1
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == "  syntax: SyntaxSpan[]"
    assert rows[3]["right_text"] == "  syntax: SyntaxSpan[],"
    assert rows[3]["right_tokens"] == [
        {"text": "  syntax: SyntaxSpan[]", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]
    assert rows[4]["status"] == "equal"
    assert rows[4]["left_no"] == 1
    assert rows[4]["right_no"] == 5
    assert rows[4]["left_text"] == ") {"
    assert rows[4]["right_text"] == ") {"


def test_difftastic_reconstructed_rhs_insert_does_not_mark_lhs_tokens() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [{"start": 9, "end": 20}],
                        },
                        "rhs": {
                            "line_number": 0,
                            "changes": [{"start": 9, "end": 23}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 22, "end": 23}],
                        },
                    },
                ]
            ],
        },
        left_text="function syntaxParts(syntax: SyntaxSpan[]) {\n",
        right_text=("function decoratedParts(\n  syntax: SyntaxSpan[],\n"),
    )

    assert rows[1]["status"] == "replace"
    assert rows[1]["left_text"] == "  syntax: SyntaxSpan[]"
    assert rows[1]["right_text"] == "  syntax: SyntaxSpan[],"
    assert rows[1].get("left_tokens") in (None, [])
    assert rows[1]["right_tokens"] == [
        {"text": "  syntax: SyntaxSpan[]", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]


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


def test_difftastic_rows_repair_shifted_delete_equal_insert_fields() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, 2],
                [3, 3],
                [4, 4],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 2, "end": 9}],
                        },
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 2, "end": 7}],
                        },
                    },
                    {
                        "lhs": {
                            "line_number": 3,
                            "changes": [{"start": 2, "end": 7}],
                        },
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 2, "end": 8}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "export type InlineToken = {\n"
            "  text: string;\n"
            "  changed: boolean;\n"
            "  is_ws: boolean;\n"
            "};\n"
        ),
        right_text=(
            "export type InlineToken = {\n"
            "  text: string;\n"
            "  is_ws: boolean;\n"
            '  status: "unchanged" | "replace" | "insert" | "delete";\n'
            "};\n"
        ),
    )

    assert [row["status"] for row in rows] == [
        "equal",
        "equal",
        "delete",
        "equal",
        "insert",
        "equal",
    ]
    assert rows[2]["left_no"] == 3
    assert rows[2]["left_text"] == "  changed: boolean;"
    assert rows[2]["right_no"] is None
    assert rows[3]["left_no"] == 4
    assert rows[3]["right_no"] == 3
    assert rows[3]["left_text"] == "  is_ws: boolean;"
    assert rows[3]["right_text"] == "  is_ws: boolean;"
    assert rows[3].get("left_tokens") is None
    assert rows[3].get("right_tokens") is None
    assert rows[4]["left_no"] is None
    assert rows[4]["right_no"] == 4
    assert (
        rows[4]["right_text"]
        == '  status: "unchanged" | "replace" | "insert" | "delete";'
    )


def test_difftastic_rows_do_not_duplicate_reconstructed_right_line_numbers() -> None:
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

    numbered_right_rows = [row for row in rows if isinstance(row.get("right_no"), int)]
    right_numbers = [row["right_no"] for row in numbered_right_rows]

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


def test_difftastic_rows_do_not_reconstruct_assignment_rhs_as_insert_argument() -> None:
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
                                {"start": 31, "end": 58},
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
                            "changes": [{"start": 12, "end": 32}],
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
        },
        {
            "status": "replace",
            "left_no": 2,
            "right_no": 2,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = _file_kind_for_change_type(',
            "left_tokens": [
                {"text": "        payload[", "status": "unchanged", "is_ws": False},
                {"text": '"change_type"', "status": "replace", "is_ws": False},
                {"text": "] = change_type", "status": "unchanged", "is_ws": False},
            ],
            "right_tokens": [
                {"text": "        payload[", "status": "unchanged", "is_ws": False},
                {"text": '"file_kind"', "status": "replace", "is_ws": False},
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
        },
    ]


def test_difftastic_rows_keep_moved_show_wrapper_lines_as_replacements() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, None],
                [3, None],
                [4, None],
                [5, 2],
                [6, 3],
                [7, 4],
                [8, 5],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 3,
                            "changes": [
                                {"start": 16, "end": 24},
                                {"start": 24, "end": 25},
                                {"start": 25, "end": 26},
                                {"start": 26, "end": 27},
                                {"start": 27, "end": 42},
                                {"start": 43, "end": 47},
                                {"start": 47, "end": 48},
                                {"start": 48, "end": 49},
                                {"start": 49, "end": 54},
                                {"start": 54, "end": 55},
                                {"start": 55, "end": 59},
                                {"start": 59, "end": 60},
                                {"start": 61, "end": 63},
                                {"start": 63, "end": 64},
                            ],
                        },
                    },
                ]
            ],
        },
        left_text=(
            '        <Show when={props.file.render_kind !== "notebook"}>\n'
            "              <Show\n"
            "                when={canRenderRows()}\n"
            "                fallback={<FilePlaceholder file={props.file} />}\n"
            "              >\n"
            "                <Show\n"
            "                  when={shouldRenderRichBody()}\n"
            "                  fallback={<PlainSplitFileDiff file={props.file} />}\n"
            "                >\n"
        ),
        right_text=(
            '        <Show when={props.file.render_kind !== "notebook"}>\n'
            "              <Show\n"
            "                <Show\n"
            "                  when={shouldRenderRichBody()}\n"
            "                  fallback={<PlainSplitFileDiff file={props.file} />}\n"
            "                >\n"
        ),
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": '        <Show when={props.file.render_kind !== "notebook"}>',
            "right_text": '        <Show when={props.file.render_kind !== "notebook"}>',
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": "              <Show",
            "right_text": "              <Show",
        },
        {
            "status": "replace",
            "left_no": 3,
            "right_no": None,
            "left_text": "                when={canRenderRows()}",
            "right_text": "",
        },
        {
            "status": "delete",
            "left_no": 4,
            "right_no": None,
            "left_text": "                fallback={<FilePlaceholder file={props.file} />}",
            "right_text": "",
            "left_tokens": [
                {"text": "                ", "status": "unchanged", "is_ws": True},
                {"text": "fallback", "status": "delete", "is_ws": False},
                {"text": "=", "status": "delete", "is_ws": False},
                {"text": "{", "status": "delete", "is_ws": False},
                {"text": "<", "status": "delete", "is_ws": False},
                {"text": "FilePlaceholder", "status": "delete", "is_ws": False},
                {"text": " ", "status": "unchanged", "is_ws": True},
                {"text": "file", "status": "delete", "is_ws": False},
                {"text": "=", "status": "delete", "is_ws": False},
                {"text": "{", "status": "delete", "is_ws": False},
                {"text": "props", "status": "delete", "is_ws": False},
                {"text": ".", "status": "delete", "is_ws": False},
                {"text": "file", "status": "delete", "is_ws": False},
                {"text": "}", "status": "delete", "is_ws": False},
                {"text": " ", "status": "unchanged", "is_ws": True},
                {"text": "/", "status": "delete", "is_ws": False},
                {"text": ">", "status": "delete", "is_ws": False},
                {"text": "}", "status": "delete", "is_ws": False},
            ],
        },
        {
            "status": "replace",
            "left_no": 5,
            "right_no": None,
            "left_text": "              >",
            "right_text": "",
        },
        {
            "status": "equal",
            "left_no": 6,
            "right_no": 3,
            "left_text": "                <Show",
            "right_text": "                <Show",
        },
        {
            "status": "equal",
            "left_no": 7,
            "right_no": 4,
            "left_text": "                  when={shouldRenderRichBody()}",
            "right_text": "                  when={shouldRenderRichBody()}",
        },
        {
            "status": "equal",
            "left_no": 8,
            "right_no": 5,
            "left_text": "                  fallback={<PlainSplitFileDiff file={props.file} />}",
            "right_text": "                  fallback={<PlainSplitFileDiff file={props.file} />}",
        },
        {
            "status": "equal",
            "left_no": 9,
            "right_no": 6,
            "left_text": "                >",
            "right_text": "                >",
        },
    ]


def test_difftastic_rows_status_is_equal_for_right_only_line_without_changed_tokens() -> (
    None
):
    rows = _difftastic_rows_from_json(
        {"aligned_lines": [[0, 0], [None, 1]], "chunks": [[]]},
        left_text="value = arg\n",
        right_text="value = arg\nvalue = arg\n",
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "right_text": "value = arg",
        },
        {
            "status": "equal",
            "left_no": None,
            "right_no": 2,
            "left_text": "",
            "right_text": "value = arg",
            "right_tokens": [],
        },
    ]


def test_difftastic_rows_status_is_replace_for_mixed_unchanged_and_insert_tokens() -> (
    None
):
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 11, "end": 12}],
                        },
                    },
                ]
            ],
        },
        left_text="value = arg\n",
        right_text="value = arg\nvalue = arg,\n",
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "right_text": "value = arg",
        },
        {
            "status": "replace",
            "left_no": None,
            "right_no": 2,
            "left_text": "",
            "right_text": "value = arg,",
            "right_tokens": [
                {"text": "value = arg", "status": "unchanged", "is_ws": False},
                {"text": ",", "status": "insert", "is_ws": False},
            ],
        },
    ]


def test_difftastic_rows_status_is_insert_when_every_changed_token_is_insert() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1]],
            "chunks": [
                [
                    {
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 0, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text="value = arg\n",
        right_text="value = arg\nnew_value\n",
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "right_text": "value = arg",
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
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [{"start": 8, "end": 11}],
                        },
                        "rhs": {
                            "line_number": 0,
                            "changes": [{"start": 8, "end": 11}],
                        },
                    },
                ]
            ],
        },
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


def test_difftastic_rows_status_is_delete_when_every_changed_token_is_delete() -> None:
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [1, None]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 1,
                            "changes": [{"start": 0, "end": 9}],
                        },
                    },
                ]
            ],
        },
        left_text="value = arg\nold_value\n",
        right_text="value = arg\n",
    )

    assert rows == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = arg",
            "right_text": "value = arg",
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
        row for row in rows if row.get("right_text") == "    if lazy is not None:"
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
                            "changes": [{"start": 12, "end": 32}],
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
                                {"start": 31, "end": 58},
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
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": "        )",
            "right_text": "        )",
        },
        {
            "status": "replace",
            "left_no": 3,
            "right_no": 3,
            "left_text": '        payload["change_type"] = change_type',
            "right_text": '        payload["file_kind"] = _file_kind_for_change_type(',
            "left_tokens": [
                {"text": "        payload[", "status": "unchanged", "is_ws": False},
                {"text": '"change_type"', "status": "replace", "is_ws": False},
                {"text": "] = change_type", "status": "unchanged", "is_ws": False},
            ],
            "right_tokens": [
                {"text": "        payload[", "status": "unchanged", "is_ws": False},
                {"text": '"file_kind"', "status": "replace", "is_ws": False},
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
        },
    ]


def test_difftastic_rows_pair_one_sided_lhs_token_delete_with_matching_rhs_line() -> (
    None
):
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [
                [0, 0],
                [1, 1],
                [2, 2],
                [3, 3],
                [4, 4],
                [5, None],
                [6, 6],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 5,
                            "changes": [{"start": 12, "end": 13}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            'import { createEffect } from "solid-js";\n'
            "import type {\n"
            "  DiffRow,\n"
            "  FileEntry,\n"
            "  InlineToken,\n"
            "  SyntaxSpan,\n"
            '} from "./api";\n'
        ),
        right_text=(
            'import { createEffect } from "solid-js";\n'
            "import type {\n"
            "  DiffRow,\n"
            "  FileEntry,\n"
            "  InlineToken,\n"
            "  SyntaxSpan\n"
            '} from "./api";\n'
        ),
    )

    assert rows[5]["status"] == "replace"
    assert rows[5]["left_no"] == 6
    assert rows[5]["right_no"] == 6
    assert rows[5]["left_text"] == "  SyntaxSpan,"
    assert rows[5]["right_text"] == "  SyntaxSpan"
    assert rows[5]["left_tokens"] == [
        {"text": "  SyntaxSpan", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "delete", "is_ws": False},
    ]
    assert rows[6]["left_text"] == '} from "./api";'


def test_tree_sitter_highlights_multiline_python_strings() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='value = """hello\nworld"""\n',
        right_text='value = """hello\nworld"""\n',
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    first_line_classes = {
        css_class
        for span in diff["rows"][0]["left_syntax"]
        for css_class in span["classes"]
    }
    second_line_classes = {
        css_class
        for span in diff["rows"][1]["left_syntax"]
        for css_class in span["classes"]
    }

    assert "ts-string" in first_line_classes
    assert "ts-string" in second_line_classes


def test_fold_hints_include_unchanged_top_level_function_body() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="def helper():\n    value = 1\n    return value\n\nx = 1\n",
        right_text="def helper():\n    value = 1\n    return value\n\nx = 2\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 3,
            "label": "def helper():",
        }
    ]


def test_changed_top_level_function_does_not_fold_descendants() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            'def helper():\n    config = {\n        "a": 1,\n    }\n    return None\n'
        ),
        right_text=(
            "def helper():\n"
            "    config = {\n"
            '        "a": 1,\n'
            "    }\n"
            '    return config.get("a")\n'
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff.get("fold_hints", []) == []


def test_fold_hints_include_unchanged_top_level_dict_body() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=('CONFIG = {\n    "a": 1,\n    "b": 2,\n}\n\nvalue = 1\n'),
        right_text=('CONFIG = {\n    "a": 1,\n    "b": 2,\n}\n\nvalue = 2\n'),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "CONFIG = {",
        }
    ]


def test_unchanged_top_level_class_folds_methods_but_not_whole_class() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n\n"
            "value = 1\n"
        ),
        right_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n\n"
            "value = 2\n"
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 3,
            "label": "def a(self):",
        },
        {
            "start_row": 5,
            "end_row": 6,
            "label": "def b(self):",
        },
    ]


def test_changed_class_still_folds_only_unchanged_methods() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 2\n"
        ),
        right_text=(
            "class Example:\n"
            "    def a(self):\n"
            "        return 1\n\n"
            "    def b(self):\n"
            "        return 3\n"
        ),
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 3,
            "label": "def a(self):",
        }
    ]


def test_whitespace_only_changes_block_folding() -> None:
    diff = build_loaded_diff(
        display_name="demo.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="def helper():\n    value = 1\n    return value\n",
        right_text="def helper():\n\tvalue = 1\n\treturn value\n",
        left_path_hint="demo.py",
        right_path_hint="demo.py",
    )

    assert diff.get("fold_hints", []) == []


def test_javascript_classes_fold_unchanged_methods_only() -> None:
    diff = build_loaded_diff(
        display_name="demo.js",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "class Example {\n  a() {\n    return 1;\n  }\n}\n\nconst value = 1;\n"
        ),
        right_text=(
            "class Example {\n  a() {\n    return 1;\n  }\n}\n\nconst value = 2;\n"
        ),
        left_path_hint="demo.js",
        right_path_hint="demo.js",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": "a() {",
        }
    ]


def test_css_unchanged_top_level_rule_folds_declarations() -> None:
    diff = build_loaded_diff(
        display_name="demo.css",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            ".card {\n"
            "  color: red;\n"
            "  background: blue;\n"
            "}\n\n"
            ":root {\n"
            "  color: black;\n"
            "}\n"
        ),
        right_text=(
            ".card {\n"
            "  color: red;\n"
            "  background: blue;\n"
            "}\n\n"
            ":root {\n"
            "  color: white;\n"
            "}\n"
        ),
        left_path_hint="demo.css",
        right_path_hint="demo.css",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": ".card {",
        }
    ]


def test_changed_css_media_rule_still_folds_unchanged_nested_rule() -> None:
    diff = build_loaded_diff(
        display_name="demo.css",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "@media screen {\n"
            "  .card {\n"
            "    color: red;\n"
            "    background: blue;\n"
            "  }\n"
            "}\n"
        ),
        right_text=(
            "@media screen {\n"
            "  .card {\n"
            "    color: red;\n"
            "    background: blue;\n"
            "  }\n"
            "  .badge {\n"
            "    color: white;\n"
            "  }\n"
            "}\n"
        ),
        left_path_hint="demo.css",
        right_path_hint="demo.css",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 5,
            "label": ".card {",
        }
    ]


def test_rust_impl_blocks_fold_unchanged_methods_only() -> None:
    diff = build_loaded_diff(
        display_name="demo.rs",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=(
            "impl Thing {\n"
            "    fn a(&self) {\n"
            "        1;\n"
            "    }\n"
            "}\n\n"
            "const VALUE: i32 = 1;\n"
        ),
        right_text=(
            "impl Thing {\n"
            "    fn a(&self) {\n"
            "        1;\n"
            "    }\n"
            "}\n\n"
            "const VALUE: i32 = 2;\n"
        ),
        left_path_hint="demo.rs",
        right_path_hint="demo.rs",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": "fn a(&self) {",
        }
    ]


def test_json_unchanged_nested_top_level_container_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.json",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='{\n  "config": {\n    "a": 1\n  },\n  "value": 1\n}\n',
        right_text='{\n  "config": {\n    "a": 1\n  },\n  "value": 2\n}\n',
        left_path_hint="demo.json",
        right_path_hint="demo.json",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 2,
            "end_row": 4,
            "label": '"config": {',
        }
    ]


def test_yaml_unchanged_nested_top_level_container_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.yaml",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="config:\n  a: 1\n  b: 2\nvalue: 1\n",
        right_text="config:\n  a: 1\n  b: 2\nvalue: 2\n",
        left_path_hint="demo.yaml",
        right_path_hint="demo.yaml",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 3,
            "label": "config:",
        }
    ]


def test_toml_unchanged_top_level_table_folds() -> None:
    diff = build_loaded_diff(
        display_name="demo.toml",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="[tool.x]\na = 1\nb = 2\n\n[other]\nvalue = 1\n",
        right_text="[tool.x]\na = 1\nb = 2\n\n[other]\nvalue = 2\n",
        left_path_hint="demo.toml",
        right_path_hint="demo.toml",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "[tool.x]",
        }
    ]


def test_markdown_unchanged_heading_section_folds_under_heading() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Intro\nalpha\nbeta\n\n# Tail\none\n",
        right_text="# Intro\nalpha\nbeta\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# Intro",
        }
    ]


def test_markdown_changed_parent_section_allows_unchanged_child_heading_fold() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Parent\nalpha\n## Child\nbeta\n\n# Tail\none\n",
        right_text="# Parent\nchanged\n## Child\nbeta\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 4,
            "end_row": 6,
            "label": "## Child",
        }
    ]


def test_markdown_added_later_sibling_section_keeps_prior_section_folded() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# Intro\nalpha\nbeta\n\n# Tail\none\n",
        right_text="# Intro\nalpha\nbeta\n\n# Added\nnew\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# Intro",
        }
    ]


def test_markdown_added_sibling_section_keeps_all_prior_unchanged_sections_folded() -> (
    None
):
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="# One\na1\na2\n\n# Two\nb1\nb2\n\n# Tail\none\n",
        right_text="# One\na1\na2\n\n# Added\nnew\n\n# Two\nb1\nb2\n\n# Tail\ntwo\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff["fold_hints"] == [
        {
            "start_row": 1,
            "end_row": 4,
            "label": "# One",
        },
        {
            "start_row": 8,
            "end_row": 11,
            "label": "# Two",
        },
    ]


def test_markdown_non_heading_content_does_not_fold() -> None:
    diff = build_loaded_diff(
        display_name="demo.md",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text="```\nalpha\nbeta\n```\nvalue = 1\n",
        right_text="```\nalpha\nbeta\n```\nvalue = 2\n",
        left_path_hint="demo.md",
        right_path_hint="demo.md",
    )

    assert diff.get("fold_hints", []) == []


def test_large_tree_sitter_diff_keeps_rich_render_mode() -> None:
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 12050
    right_text = left_text.replace("1234567890", "1234567891", 1)

    diff = build_loaded_diff(
        display_name="large.py",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="large.py",
        right_path_hint="large.py",
    )

    assert "render_mode" not in diff
    assert "truncated_rows" not in diff
    assert any(row.get("left_syntax") for row in diff["rows"])
    assert any(row.get("right_syntax") for row in diff["rows"])
    assert all(row["status"] != "fold" for row in diff["rows"])
    changed_rows = [
        row for row in diff["rows"] if row.get("left_text") != row.get("right_text")
    ]
    assert changed_rows
    assert "right_syntax" in changed_rows[0] or "left_syntax" in changed_rows[0]


def test_large_plaintext_diff_still_falls_back_to_plain_render_mode() -> None:
    repeated_line = "value = 1234567890\n"
    left_text = repeated_line * 12050
    right_text = left_text.replace("1234567890", "1234567891", 1)

    diff = build_loaded_diff(
        display_name="large.txt",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="large.txt",
        right_path_hint="large.txt",
    )

    assert diff["render_mode"] == "plain"
    assert any(row["status"] == "fold" for row in diff["rows"])
    assert not any(row.get("left_syntax") for row in diff["rows"])


def test_build_loaded_diff_renders_notebook_cells_when_ipynb_is_valid() -> None:
    left_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {"collapsed": True},
                    "source": ["value = 2\n", "print(value)\n"],
                    "outputs": [
                        {"output_type": "stream", "name": "stdout", "text": "2\n"}
                    ],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["changed_cells"] == 1
    assert diff["cells"][0]["metadata_changed"] is True
    assert diff["cells"][0]["outputs_changed"] is True
    assert any(
        row["left_text"] == "value = 1" and row["right_text"] == "value = 2"
        for row in diff["cells"][0]["source_rows"]
    )


def test_build_loaded_diff_pairs_notebook_cells_by_partial_unique_ids() -> None:
    left_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["## Setup\n\n", "Old body\n"],
                },
                {
                    "cell_type": "code",
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "markdown",
                    "id": "intro",
                    "metadata": {},
                    "source": ["## Setup\n\n", "Updated body\n"],
                },
                {
                    "cell_type": "code",
                    "metadata": {},
                    "source": ["value = 1\n"],
                    "outputs": [],
                },
            ],
            "metadata": {},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["changed_cells"] == 1
    assert diff["summary"]["modified_cells"] == 1
    assert diff["summary"]["added_cells"] == 0
    assert diff["summary"]["removed_cells"] == 0
    assert diff["cells"][0]["kind"] == "modified"
    assert diff["cells"][0]["cell_id"] == "intro"
    assert diff["cells"][0]["left_index"] == 0
    assert diff["cells"][0]["right_index"] == 0


def test_build_loaded_diff_keeps_notebook_metadata_and_outputs_lazy() -> None:
    left_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [],
                }
            ],
            "metadata": {"kernelspec": {"name": "python3"}},
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )
    right_text = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "id": "code-1",
                    "metadata": {"collapsed": True},
                    "source": ["value = 1\n", "print(value)\n"],
                    "outputs": [
                        {
                            "output_type": "stream",
                            "name": "stdout",
                            "text": "1\n",
                        }
                    ],
                }
            ],
            "metadata": {
                "kernelspec": {"name": "python3"},
                "language_info": {"name": "python"},
            },
            "nbformat": 4,
            "nbformat_minor": 5,
        }
    )

    diff = build_loaded_diff(
        display_name="demo.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text=left_text,
        right_text=right_text,
        left_path_hint="demo.ipynb",
        right_path_hint="demo.ipynb",
    )

    assert diff["render_kind"] == "notebook"
    assert diff["summary"]["notebook_metadata_changed"] is True
    assert diff["notebook_metadata_rows"] == []
    assert diff["notebook_metadata_lazy"] is True
    assert diff["notebook_metadata_hunk_count"] >= 1

    cell = diff["cells"][0]
    assert cell["metadata_changed"] is True
    assert cell["outputs_changed"] is True
    assert cell["metadata_rows"] == []
    assert cell["outputs_rows"] == []
    assert cell["metadata_lazy"] is True
    assert cell["outputs_lazy"] is True
    assert cell["metadata_hunk_count"] >= 1
    assert cell["outputs_hunk_count"] >= 1


def test_build_loaded_diff_falls_back_to_text_for_invalid_notebook_json() -> None:
    diff = build_loaded_diff(
        display_name="broken.ipynb",
        mode="files",
        left_label="left",
        right_label="right",
        left_exists=True,
        right_exists=True,
        left_text='{"cells": [}\n',
        right_text='{"cells": []}\n',
        left_path_hint="broken.ipynb",
        right_path_hint="broken.ipynb",
    )

    assert diff.get("render_kind") != "notebook"
    assert "rows" in diff
