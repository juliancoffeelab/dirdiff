from dirdiff.diff import _difftastic_engine_warning, _difftastic_rows_from_json


def test_difftastic_engine_warning_reports_graph_limit_fallback() -> None:
    assert _difftastic_engine_warning(
        {"language": "Text (exceeded DFT_GRAPH_LIMIT)"}
    ) == {
        "type": "difftastic_graph_limit",
        "message": "Difftastic exceeded DFT_GRAPH_LIMIT and fell back to text diff.",
    }
    assert _difftastic_engine_warning({"language": "TypeScript"}) is None


def test_difftastic_json_rows_use_semantic_alignment_and_changed_ranges() -> (
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
    assert rows[1]["left_no"] == 1
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == "        service,"
    assert rows[1]["right_text"] == "        service,"
    assert rows[2]["left_no"] == 1
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == "        defaults,"
    assert rows[2]["right_text"] == "        defaults,"
    assert rows[3]["left_no"] == 1
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == '        services={"git": git_service}'
    assert (
        rows[3]["right_text"]
        == '        services={"git": git_service, "difftastic": difftastic_service},'
    )
    assert any(
        token["status"] == "insert" and token["text"] == '"difftastic"'
        for token in rows[3]["right_tokens"]
    )
    assert rows[4]["left_no"] == 1
    assert rows[4]["right_no"] == 5
    assert rows[4]["left_text"] == "    )"
    assert rows[4]["right_text"] == "    )"


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
            "from dirdiff.diff import GitDiffService, GitBackend, TextDiffService\n"
            "from dirdiff.server import create_app\n"
        ),
        right_text=(
            "from dirdiff.diff import (\n"
            "    DifftasticDiffService,\n"
            "    GitDiffService,\n"
            "    GitBackend,\n"
            "    TextDiffService,\n"
            ")\n"
            "from dirdiff.server import create_app\n"
        ),
    )

    assert rows[0]["status"] == "replace"
    assert rows[0]["left_text"] == "from dirdiff.diff import "
    assert rows[0]["right_text"] == "from dirdiff.diff import ("
    assert rows[0]["right_tokens"] == [
        {
            "text": "from dirdiff.diff import ",
            "status": "unchanged",
            "is_ws": False,
        },
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


def test_difftastic_rows_pair_rhs_token_insert_from_split_lhs_line_tail() -> (
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
                [None, 0],
                [0, 1],
                [1, 2],
                [2, 3],
                [None, 4],
                [None, 5],
                [None, 6],
                [None, 7],
                [3, 8],
            ],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 2,
                            "changes": [{"start": 16, "end": 27}],
                        },
                        "rhs": {
                            "line_number": 3,
                            "changes": [{"start": 16, "end": 30}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 5,
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
                            "line_number": 6,
                            "changes": [{"start": 22, "end": 23}],
                        },
                    },
                ]
            ],
        },
        left_text=(
            "type SyntaxSpan = { start: number; end: number };\n"
            "\n"
            "export function syntaxParts(text: string, syntax: SyntaxSpan[]): string[] {\n"
            "  if (!text || !syntax.length) {\n"
        ),
        right_text=(
            "type InlineToken = { text: string };\n"
            "type SyntaxSpan = { start: number; end: number };\n"
            "\n"
            "export function decoratedParts(\n"
            "  text: string,\n"
            "  tokens: InlineToken[],\n"
            "  syntax: SyntaxSpan[],\n"
            ") {\n"
            "  if (!text || (!tokens.length && !syntax.length)) {\n"
        ),
    )

    assert rows[3]["status"] == "replace"
    assert (
        rows[3]["left_text"]
        == "export function syntaxParts(text: string, syntax: SyntaxSpan[]): string[] {"
    )
    assert rows[3]["right_text"] == "export function decoratedParts("
    assert rows[4]["status"] == "equal"
    assert rows[4]["left_no"] == 3
    assert rows[4]["right_no"] == 5
    assert rows[4]["left_text"] == "  text: string,"
    assert rows[4]["right_text"] == "  text: string,"
    assert rows[5]["status"] == "insert"
    assert rows[5]["right_text"] == "  tokens: InlineToken[],"
    assert rows[6]["status"] == "replace"
    assert rows[6]["left_no"] == 3
    assert rows[6]["right_no"] == 7
    assert rows[6]["left_text"] == "  syntax: SyntaxSpan[]"
    assert rows[6]["right_text"] == "  syntax: SyntaxSpan[],"
    assert rows[6]["right_tokens"] == [
        {
            "text": "  syntax: SyntaxSpan[]",
            "status": "unchanged",
            "is_ws": False,
        },
        {"text": ",", "status": "insert", "is_ws": False},
    ]


def test_difftastic_rows_preserve_left_context_for_clojure_wrapper_insert() -> (
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
                                {"start": 13, "end": 14},
                                {"start": 14, "end": 18},
                                {"start": 31, "end": 32},
                            ],
                        }
                    }
                ]
            ],
        },
        left_text="(render {:on-click handle-click! :on-submit submit-form!})\n",
        right_text=(
            "(render {\n"
            "  :on-click handle-click!\n"
            "  :on-submit (wrap submit-form!)\n"
            "})\n"
        ),
    )

    assert rows[0]["status"] == "equal"
    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] == 1
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == "  :on-click handle-click!"
    assert rows[1]["right_text"] == "  :on-click handle-click!"
    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] == 1
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == "  :on-submit submit-form!"
    assert rows[2]["right_text"] == "  :on-submit (wrap submit-form!)"
    assert rows[2]["right_tokens"] == [
        {"text": "  :on-submit ", "status": "unchanged", "is_ws": False},
        {"text": "(", "status": "insert", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " submit-form!", "status": "unchanged", "is_ws": False},
        {"text": ")", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] == 1
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == "})"
    assert rows[3]["right_text"] == "})"


def test_difftastic_rows_preserve_left_context_for_clojure_comp_insert() -> (
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
                                {"start": 13, "end": 14},
                                {"start": 14, "end": 18},
                                {"start": 19, "end": 24},
                                {"start": 42, "end": 43},
                            ],
                        }
                    }
                ]
            ],
        },
        left_text="(render {:on-click handle-click! :on-submit user/submit-form!})\n",
        right_text=(
            "(render {\n"
            "  :on-click handle-click!\n"
            "  :on-submit (comp audit user/submit-form!)\n"
            "})\n"
        ),
    )

    assert rows[1]["status"] == "equal"
    assert rows[1]["left_no"] == 1
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == "  :on-click handle-click!"
    assert rows[1]["right_text"] == "  :on-click handle-click!"
    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] == 1
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == "  :on-submit user/submit-form!"
    assert (
        rows[2]["right_text"] == "  :on-submit (comp audit user/submit-form!)"
    )
    assert rows[2]["right_tokens"] == [
        {"text": "  :on-submit ", "status": "unchanged", "is_ws": False},
        {"text": "(", "status": "insert", "is_ws": False},
        {"text": "comp", "status": "insert", "is_ws": False},
        {"text": " ", "status": "unchanged", "is_ws": True},
        {"text": "audit", "status": "insert", "is_ws": False},
        {"text": " user/submit-form!", "status": "unchanged", "is_ws": False},
        {"text": ")", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] == 1
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == "})"
    assert rows[3]["right_text"] == "})"


def test_difftastic_rows_preserve_clojure_map_tail_after_middle_wrap_change() -> (
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
                                {"start": 21, "end": 22},
                            ],
                        }
                    }
                ]
            ],
        },
        left_text="(render {:left foo-bar :right baz})\n",
        right_text="(render {\n  :left (wrap foo-bar)\n  :right baz\n})\n",
    )

    assert rows[0]["status"] == "equal"
    assert rows[0]["left_text"] == "(render {"
    assert rows[1]["status"] == "replace"
    assert rows[1]["left_no"] == 1
    assert rows[1]["right_no"] == 2
    assert rows[1]["left_text"] == "  :left foo-bar"
    assert rows[1]["right_text"] == "  :left (wrap foo-bar)"
    assert rows[1]["right_tokens"] == [
        {"text": "  :left ", "status": "unchanged", "is_ws": False},
        {"text": "(", "status": "insert", "is_ws": False},
        {"text": "wrap", "status": "insert", "is_ws": False},
        {"text": " foo-bar", "status": "unchanged", "is_ws": False},
        {"text": ")", "status": "insert", "is_ws": False},
    ]
    assert rows[2]["status"] == "equal"
    assert rows[2]["left_no"] == 1
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == "  :right baz"
    assert rows[2]["right_text"] == "  :right baz"
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] == 1
    assert rows[3]["right_no"] == 4
    assert rows[3]["left_text"] == "})"
    assert rows[3]["right_text"] == "})"


def test_difftastic_rows_do_not_reconstruct_unchanged_tail_after_split_call_change() -> (
    None
):
    rows = _difftastic_rows_from_json(
        {
            "aligned_lines": [[0, 0], [None, 1], [1, 2], [None, 3], [2, 4]],
            "chunks": [
                [
                    {
                        "lhs": {
                            "line_number": 0,
                            "changes": [{"start": 19, "end": 22}],
                        },
                        "rhs": {
                            "line_number": 1,
                            "changes": [{"start": 6, "end": 16}],
                        },
                    },
                    {
                        "rhs": {
                            "line_number": 2,
                            "changes": [{"start": 5, "end": 6}],
                        }
                    },
                ]
            ],
        },
        left_text="return compute(foo.bar, baz);\n",
        right_text="return compute(\n  foo.barWrapped,\n  baz,\n);\n",
    )

    assert rows[0]["status"] == "replace"
    assert rows[0]["left_text"] == "return compute(foo.bar, baz);"
    assert rows[0]["right_text"] == "return compute("
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
    assert rows[2]["status"] == "replace"
    assert rows[2]["left_no"] is None
    assert rows[2]["right_no"] == 3
    assert rows[2]["left_text"] == ""
    assert rows[2]["right_text"] == "  baz,"
    assert rows[2]["right_tokens"] == [
        {"text": "  baz", "status": "unchanged", "is_ws": False},
        {"text": ",", "status": "insert", "is_ws": False},
    ]
    assert rows[3]["status"] == "equal"
    assert rows[3]["left_no"] is None
    assert rows[3]["right_no"] == 4
    assert rows[3]["right_text"] == ");"


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
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [1, 3]],
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
            "aligned_lines": [[0, 0], [None, 1], [None, 2], [1, 3]],
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
        {
            "text": "  syntax: SyntaxSpan[]",
            "status": "unchanged",
            "is_ws": False,
        },
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

    numbered_right_rows = [
        row for row in rows if isinstance(row.get("right_no"), int)
    ]
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
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"change_type"', "status": "replace", "is_ws": False},
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


def test_difftastic_rows_keep_moved_show_wrapper_lines_as_replacements() -> (
    None
):
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
            "              <Show when={canRenderRows()}>\n"
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
            "status": "equal",
            "left_no": 3,
            "right_no": 2,
            "left_text": "                when={canRenderRows()}",
            "right_text": "                when={canRenderRows()}",
        },
        {
            "status": "delete",
            "left_no": 4,
            "right_no": None,
            "left_text": "                fallback={<FilePlaceholder file={props.file} />}",
            "right_text": "",
            "left_tokens": [
                {
                    "text": "                ",
                    "status": "unchanged",
                    "is_ws": True,
                },
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
            "status": "equal",
            "left_no": 5,
            "right_no": 2,
            "left_text": "              >",
            "right_text": "              >",
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


def test_difftastic_rows_status_is_insert_when_every_changed_token_is_insert() -> (
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


def test_difftastic_rows_status_is_delete_when_every_changed_token_is_delete() -> (
    None
):
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
                {
                    "text": "        payload[",
                    "status": "unchanged",
                    "is_ws": False,
                },
                {"text": '"change_type"', "status": "replace", "is_ws": False},
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
