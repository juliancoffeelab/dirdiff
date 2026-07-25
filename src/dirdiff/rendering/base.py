"""Display enrichment for already-rendered diff rows.

This module does not choose an engine and does not own native text comparison.
It receives already-computed neutral rows, then attaches display-only details:
syntax spans, fold hints, backend-owned hunk identities, and default expansion.
"""

from __future__ import annotations

import importlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Literal, NotRequired, TypedDict, TypeIs, get_args

from tree_sitter import Language, Parser, Query, QueryCursor

from dirdiff.engines import (
    InlineToken,
    engine_row_has_change,
)
from dirdiff.rendering.fold import fold_hints_for_path

__all__ = [
    "DiffRow",
    "SyntaxClass",
    "SyntaxSpan",
    "canonical_json",
    "default_expanded_for_payload",
    "enrich_rows_for_display",
    "highlight_lines_for_path",
]

type SyntaxClass = Literal[
    "ts-attribute",
    "ts-boolean",
    "ts-charset",
    "ts-comment",
    "ts-comment-documentation",
    "ts-constant",
    "ts-constant-builtin",
    "ts-constant-builtin-boolean",
    "ts-constant-character",
    "ts-constant-character-escape",
    "ts-constant-numeric",
    "ts-constant-numeric-float",
    "ts-constant-numeric-integer",
    "ts-constructor",
    "ts-embedded",
    "ts-escape",
    "ts-function",
    "ts-function-builtin",
    "ts-function-macro",
    "ts-function-method",
    "ts-function-method-private",
    "ts-import",
    "ts-keyframes",
    "ts-keyword",
    "ts-keyword-control",
    "ts-keyword-control-conditional",
    "ts-keyword-control-exception",
    "ts-keyword-control-import",
    "ts-keyword-control-repeat",
    "ts-keyword-control-return",
    "ts-keyword-directive",
    "ts-keyword-function",
    "ts-keyword-operator",
    "ts-keyword-storage",
    "ts-keyword-storage-modifier",
    "ts-keyword-storage-type",
    "ts-label",
    "ts-media",
    "ts-module",
    "ts-namespace",
    "ts-none",
    "ts-number",
    "ts-operator",
    "ts-property",
    "ts-punctuation",
    "ts-punctuation-bracket",
    "ts-punctuation-delimiter",
    "ts-punctuation-special",
    "ts-string",
    "ts-string-escape",
    "ts-string-regexp",
    "ts-string-special",
    "ts-string-special-key",
    "ts-supports",
    "ts-tag",
    "ts-tag-error",
    "ts-text",
    "ts-text-literal",
    "ts-text-reference",
    "ts-text-title",
    "ts-text-uri",
    "ts-type",
    "ts-type-builtin",
    "ts-type-parameter",
    "ts-variable",
    "ts-variable-builtin",
    "ts-variable-other",
    "ts-variable-other-member",
    "ts-variable-other-member-private",
    "ts-variable-parameter",
]
"""
Syntax-highlighting class emitted by the bundled Tree-sitter queries.

When adding a syntax class, ensure it is handled in CSS if necessary.
"""


def _is_syntax_class(value: str) -> TypeIs[SyntaxClass]:
    """Check that a Tree-sitter capture class belongs to `SyntaxClass`."""
    return value in get_args(SyntaxClass.__value__)


class SyntaxSpan(TypedDict):
    """Highlighted token span for one rendered line."""

    start: int
    """
    Start offset within the rendered line text.
    """

    end: int
    """
    End offset within the rendered line text.
    """

    classes: list[SyntaxClass]
    """
    Syntax classes consumed by the frontend renderer.
    """


class DiffRow(TypedDict):
    """One row in the rendered text diff grid.

    This is the display/API row shape after engine rows have been enriched for
    syntax highlighting and backend-owned hunk identity. Frontend fold rows
    are derived separately from `FoldHint` ranges and never enter this shape.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """
    Display status of the real aligned engine row.
    """

    left_no: NotRequired[int | None]
    """
    One-based old/left line number, when this row has a left side.
    """

    right_no: NotRequired[int | None]
    """
    One-based new/right line number, when this row has a right side.
    """

    left_text: NotRequired[str | None]
    """
    Rendered old/left line text.
    """

    right_text: NotRequired[str | None]
    """
    Rendered new/right line text.
    """

    left_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the old/left side.

    TODO: token spans and syntax spans are parallel decorations today. We
    should probably unify them into one server-side decorated text model
    before the frontend has to merge overlapping ranges itself.
    """

    right_tokens: NotRequired[list[InlineToken]]
    """
    Inline diff tokens for the new/right side.
    """

    left_syntax: NotRequired[list[SyntaxSpan]]
    """
    Syntax-highlight spans for the old/left line.

    See `left_tokens` for the TODO about unifying token and syntax
    decorations before frontend rendering.
    """

    right_syntax: NotRequired[list[SyntaxSpan]]
    """
    Syntax-highlight spans for the new/right line.
    """

    hunk_index: int | None
    """
    Zero-based file-local identity on the first row of a changed hunk.

    Every other row carries `None`. Display enrichment assigns this field
    before the row enters an API payload.
    """


@dataclass(frozen=True)
class _SyntaxLanguageSpec:
    """Tree-sitter language/query metadata used by display highlighting."""

    module_name: str
    """
    Python module that exposes the tree-sitter language factory.
    """

    query_path: str
    """
    Package-resource path to the highlight query.
    """

    suffixes: tuple[str, ...]
    """
    File suffixes that select this syntax highlighter.
    """

    filenames: tuple[str, ...] = ()
    """
    Exact lower-case basenames that select this syntax highlighter.
    """

    language_attr: str = "language"
    """
    Attribute name for the language factory inside `module_name`.
    """

    query_package: str | None = None
    """
    Optional package to read `query_path` from instead of `module_name`.
    """


@dataclass(frozen=True)
class _SyntaxSpan:
    """Collapsed syntax span before conversion to the API dictionary shape."""

    start: int
    """
    Start offset within the rendered line text.
    """

    end: int
    """
    End offset within the rendered line text.
    """

    classes: tuple[SyntaxClass, ...]
    """
    Syntax classes for this span.
    """


_LANGUAGE_SPECS: tuple[_SyntaxLanguageSpec, ...] = (
    _SyntaxLanguageSpec(
        module_name="tree_sitter_python",
        query_path="queries/highlights.scm",
        suffixes=(".py", ".pyi", ".pyw"),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_javascript",
        query_path="queries/highlights/javascript.scm",
        suffixes=(".js", ".mjs", ".cjs"),
        query_package="dirdiff",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_javascript",
        query_path="queries/highlights/jsx.scm",
        suffixes=(".jsx",),
        query_package="dirdiff",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/highlights/typescript.scm",
        suffixes=(".ts", ".mts", ".cts"),
        language_attr="language_typescript",
        query_package="dirdiff",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/highlights/tsx.scm",
        suffixes=(".tsx",),
        language_attr="language_tsx",
        query_package="dirdiff",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_rust",
        query_path="queries/highlights.scm",
        suffixes=(".rs",),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_bash",
        query_path="queries/highlights.scm",
        suffixes=(".sh", ".bash", ".zsh"),
        filenames=(".bashrc", ".zshrc"),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_css",
        query_path="queries/highlights.scm",
        suffixes=(".css",),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_html",
        query_path="queries/highlights.scm",
        suffixes=(".html", ".htm", ".xhtml"),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_json",
        query_path="queries/highlights.scm",
        suffixes=(".json",),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_markdown",
        query_path="queries/markdown/highlights.scm",
        suffixes=(".md", ".markdown"),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_clojure",
        query_path="queries/highlights.scm",
        suffixes=(".clj", ".cljs", ".cljc", ".edn"),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_ocaml",
        query_path="queries/highlights.scm",
        suffixes=(".ml",),
        language_attr="language_ocaml",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_ocaml",
        query_path="queries/highlights.scm",
        suffixes=(".mli",),
        language_attr="language_ocaml_interface",
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_toml",
        query_path="queries/highlights.scm",
        suffixes=(".toml",),
        filenames=("pyproject.toml",),
    ),
    _SyntaxLanguageSpec(
        module_name="tree_sitter_yaml",
        query_path="queries/highlights.scm",
        suffixes=(".yaml", ".yml"),
    ),
)


def highlight_lines_for_path(
    path: str | None,
    text: str,
) -> list[list[SyntaxSpan]] | None:
    """Return syntax spans for display rendering, if a parser is available.

    Highlighting is part of the rendered row payload, not part of diff-engine
    comparison.  The renderer uses the path hint only to choose a tree-sitter
    language and query; unsupported languages, missing parsers, and missing
    query files all produce `None`; callers then leave that side undecorated.
    """
    if path is None:
        return None
    spec = _syntax_spec_for_path(path)
    if spec is None:
        return None

    return _highlight_lines_with_spec(spec, text)


def _syntax_spec_for_path(path: str) -> _SyntaxLanguageSpec | None:
    normalized = path.casefold()
    basename = normalized.rsplit("/", 1)[-1]
    for spec in _LANGUAGE_SPECS:
        if basename in spec.filenames:
            return spec
        if any(normalized.endswith(suffix) for suffix in spec.suffixes):
            return spec
    return None


@cache
def _load_syntax_language_query(
    module_name: str,
    language_attr: str,
    query_path: str,
    query_package: str | None,
) -> tuple[Language, Query]:
    module = importlib.import_module(module_name)
    language_factory = getattr(module, language_attr)
    language = Language(language_factory())
    query_text = _load_syntax_query_text(
        query_package or module_name, query_path
    )
    query = Query(language, query_text)
    return language, query


@cache
def _load_syntax_query_text(package_name: str, query_path: str) -> str:
    query_file = files(package_name).joinpath(query_path)
    query_text = query_file.read_text(encoding="utf-8")
    inherited_query_texts = [
        _load_syntax_query_text(
            package_name,
            _sibling_query_path(query_path, inherited_name),
        )
        for inherited_name in _inherited_query_names(query_text)
    ]
    return "\n".join([*inherited_query_texts, query_text])


def _inherited_query_names(query_text: str) -> list[str]:
    inherited_names: list[str] = []
    for line in query_text.splitlines():
        match = re.match(r"\s*;\s*inherits:\s*(.+)$", line)
        if match is None:
            continue
        inherited_names.extend(
            name.strip() for name in match.group(1).split(",") if name.strip()
        )
    return inherited_names


def _sibling_query_path(query_path: str, query_name: str) -> str:
    parent = query_path.rsplit("/", 1)[0]
    return f"{parent}/{query_name}.scm"


def _highlight_lines_with_spec(
    spec: _SyntaxLanguageSpec,
    text: str,
) -> list[list[SyntaxSpan]] | None:
    try:
        language, query = _load_syntax_language_query(
            spec.module_name,
            spec.language_attr,
            spec.query_path,
            spec.query_package,
        )
    except ImportError, AttributeError, FileNotFoundError, OSError, ValueError:
        return None

    parser = Parser(language)
    source_bytes = text.encode("utf-8")
    tree = parser.parse(source_bytes)
    cursor = QueryCursor(query)
    capture_map = cursor.captures(tree.root_node)
    captures: list[tuple[str, int, int]] = []
    for capture_name, nodes in capture_map.items():
        for node in nodes:
            if node.start_byte < node.end_byte:
                captures.append((capture_name, node.start_byte, node.end_byte))

    if captures == []:
        return [[] for _ in text.splitlines()]

    byte_boundaries = [0]
    for character in text:
        byte_boundaries.append(
            byte_boundaries[-1] + len(character.encode("utf-8"))
        )

    line_texts = text.splitlines()
    line_starts = [0]
    for index, character in enumerate(text):
        if character == "\n":
            line_starts.append(index + 1)

    line_intervals: list[
        list[tuple[int, int, tuple[SyntaxClass, ...], int]]
    ] = [[] for _ in line_texts]
    for order, (capture_name, start_byte, end_byte) in enumerate(captures):
        start_char = bisect_right(byte_boundaries, start_byte) - 1
        end_char = bisect_right(byte_boundaries, end_byte) - 1
        if start_char >= end_char:
            continue

        classes = _classes_for_capture(capture_name)
        first_line = bisect_right(line_starts, start_char) - 1
        last_line = bisect_right(line_starts, max(start_char, end_char - 1)) - 1

        for line_index in range(
            first_line,
            min(last_line + 1, len(line_texts)),
        ):
            line_start = line_starts[line_index]
            line_end = line_start + len(line_texts[line_index])
            local_start = max(start_char, line_start) - line_start
            local_end = min(end_char, line_end) - line_start
            if local_start >= local_end:
                continue
            line_intervals[line_index].append(
                (local_start, local_end, classes, order)
            )

    return [
        [
            SyntaxSpan(
                start=span.start,
                end=span.end,
                classes=list(span.classes),
            )
            for span in _collapse_line_intervals(line, intervals)
        ]
        for line, intervals in zip(line_texts, line_intervals, strict=True)
    ]


def _classes_for_capture(capture_name: str) -> tuple[SyntaxClass, ...]:
    parts = [part for part in capture_name.split(".") if part]
    classes: list[SyntaxClass] = []
    for index in range(1, len(parts) + 1):
        syntax_class = f"ts-{'-'.join(parts[:index])}"
        assert _is_syntax_class(syntax_class), (
            f"Tree-sitter emitted undeclared syntax class {syntax_class!r}."
        )
        classes.append(syntax_class)
    return tuple(classes)


def _collapse_line_intervals(
    line_text: str,
    intervals: list[tuple[int, int, tuple[SyntaxClass, ...], int]],
) -> list[_SyntaxSpan]:
    if intervals == []:
        return []

    events: list[tuple[int, int, int]] = []
    for index, (start, end, _classes, _order) in enumerate(intervals):
        events.append((start, 1, index))
        events.append((end, 0, index))
    events.sort(key=lambda item: (item[0], item[1]))

    active: dict[
        int,
        tuple[int, int, tuple[SyntaxClass, ...], int],
    ] = {}
    position = 0
    spans: list[_SyntaxSpan] = []

    for event_position, event_kind, interval_index in events:
        if event_position > position and active != {}:
            chosen = min(
                active.values(),
                key=lambda item: (
                    item[1] - item[0],
                    -len(item[2]),
                    -item[3],
                ),
            )
            _append_syntax_span(spans, position, event_position, chosen[2])

        interval = intervals[interval_index]
        if event_kind == 0:
            active.pop(interval_index, None)
        else:
            active[interval_index] = interval
        position = event_position

    if position < len(line_text) and active != {}:
        chosen = min(
            active.values(),
            key=lambda item: (
                item[1] - item[0],
                -len(item[2]),
                -item[3],
            ),
        )
        _append_syntax_span(spans, position, len(line_text), chosen[2])

    return spans


def _append_syntax_span(
    spans: list[_SyntaxSpan],
    start: int,
    end: int,
    classes: tuple[SyntaxClass, ...],
) -> None:
    if start >= end:
        return
    if spans != [] and spans[-1].end == start and spans[-1].classes == classes:
        previous = spans[-1]
        spans[-1] = _SyntaxSpan(previous.start, end, previous.classes)
        return
    spans.append(_SyntaxSpan(start, end, classes))


def canonical_json(value: Any) -> str:
    """Serialize structured sections in the stable form dirdiff compares.

    Notebook metadata and output sections are rendered as text diffs.  Sorting
    keys and using deterministic indentation keeps those section diffs stable
    across Python dictionary ordering and makes golden snapshots readable.
    """
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def default_expanded_for_payload(payload: dict[str, Any]) -> bool:
    """Return whether a file payload should start expanded in the UI.

    The current rule is intentionally shared: lazy payloads start collapsed,
    fully-rendered payloads start expanded.  Engines should not invent their
    own expansion policy unless the API model grows a first-class setting for
    it.
    """
    return not payload.get("lazy")


def enrich_rows_for_display(
    *,
    rows: list[dict[str, Any]],
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> dict[str, Any]:
    """Attach display-only row metadata without calculating diff summary.

    This helper preserves every engine row while assigning hunk identities,
    syntax spans, and optional syntax-aware fold hints. It does not decide
    changed/added/removed/moved line counts; engines calculate summaries before
    calling it.
    """
    hunk_count = _assign_hunk_indices(rows)
    left_syntax_lines = highlight_lines_for_path(left_path_hint, left_text)
    right_syntax_lines = highlight_lines_for_path(
        right_path_hint,
        right_text,
    )
    fold_hints = fold_hints_for_path(right_path_hint, right_text, rows)
    for row in rows:
        left_no = row.get("left_no")
        if (
            isinstance(left_no, int)
            and left_syntax_lines is not None
            and left_no - 1 < len(left_syntax_lines)
            and left_syntax_lines[left_no - 1] != []
        ):
            row["left_syntax"] = left_syntax_lines[left_no - 1]

        right_no = row.get("right_no")
        if (
            isinstance(right_no, int)
            and right_syntax_lines is not None
            and right_no - 1 < len(right_syntax_lines)
            and right_syntax_lines[right_no - 1] != []
        ):
            row["right_syntax"] = right_syntax_lines[right_no - 1]

    payload: dict[str, Any] = {
        "hunk_count": hunk_count,
        "rows": rows,
    }
    if fold_hints != []:
        payload["fold_hints"] = fold_hints
    return payload


def _assign_hunk_indices(rows: list[dict[str, Any]]) -> int:
    """Mark each changed-run start with its zero-based file-local hunk index.

    `/api/file-diff` owns hunk identity independently of the frontend's
    fold/virtualization representation. Equal rows carry `None`; only the first
    row of each contiguous changed run carries an index.
    """
    hunk_count = 0
    previous_changed = False
    for row in rows:
        changed = engine_row_has_change(row)
        row["hunk_index"] = (
            hunk_count if changed and not previous_changed else None
        )
        if changed and not previous_changed:
            hunk_count += 1
        previous_changed = changed
    return hunk_count
