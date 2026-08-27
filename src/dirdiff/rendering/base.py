"""Display enrichment for already-rendered diff rows.

This module does not choose an engine and does not own native text comparison.
It receives already-computed neutral rows, then attaches display-only details:
backend-woven syntax/diff parts, fold hints, backend-owned hunk identities, and
default expansion.
"""

from __future__ import annotations

import importlib
import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from operator import itemgetter
from typing import Literal, NotRequired, TypedDict, TypeIs, get_args

from tree_sitter import Language, Parser, Query, QueryCursor

from dirdiff.engines import (
    DiffEngineRow,
    InlineToken,
    InlineTokenStatus,
)
from dirdiff.rendering.fold import (
    FoldHint,
    engine_row_has_change,
    fold_hints_for_path,
)

__all__ = [
    "DecoratedPart",
    "DiffRow",
    "EnrichedRows",
    "SyntaxClass",
    "SyntaxSpan",
    "enrich_rows_for_display",
    "highlight_lines_for_path",
    "weave_decorated_parts",
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


class DecoratedPart(TypedDict):
    """One contiguous text slice carrying diff and syntax decoration.

    Parts preserve source order and partition one complete rendered line.
    Adjacent parts differ in at least one decoration field.
    """

    text: str
    """
    Exact source text represented by this part.
    """

    syntax_classes: list[SyntaxClass]
    """
    Syntax classes active across the complete text slice.
    """

    diff_status: InlineTokenStatus
    """
    Token-level diff status active across the complete text slice.
    """

    is_whitespace: bool
    """
    Whether the source inline token consists only of whitespace.
    """

    is_leading_whitespace: bool
    """
    Whether this slice belongs to the first whitespace inline token.
    """


def weave_decorated_parts(
    text: str,
    tokens: list[InlineToken],
    syntax: list[SyntaxSpan],
) -> list[DecoratedPart]:
    """Combine diff tokens and syntax spans into one lossless text partition.

    An empty token list means the engine supplied no token-level change for the
    complete text. Non-empty token lists must reconstruct `text` exactly.
    Syntax spans must be ordered, non-overlapping, non-empty character ranges
    within the same text. The result preserves every character and carries both
    decorations without retaining offsets for the frontend to intersect again.
    """
    token_intervals: list[tuple[int, int, InlineToken]] = []
    token_cursor = 0
    for inline_token in tokens:
        token_text = inline_token["text"]
        assert token_text != "", "Inline diff tokens must contain text."
        assert inline_token["is_ws"] == token_text.isspace(), (
            "Inline diff token whitespace metadata must match its text."
        )
        # Offset startswith checks each token in place, so the full
        # reconstruction invariant holds without rebuilding the row string.
        assert text.startswith(token_text, token_cursor), (
            "Inline diff tokens must reconstruct their complete row text."
        )
        token_end = token_cursor + len(token_text)
        token_intervals.append((token_cursor, token_end, inline_token))
        token_cursor = token_end
    if tokens != []:
        assert token_cursor == len(text), (
            "Inline diff tokens must reconstruct their complete row text."
        )

    previous_syntax_end = 0
    for span in syntax:
        assert 0 <= span["start"] < span["end"] <= len(text), (
            "Syntax spans must be non-empty ranges within their row text."
        )
        assert span["start"] >= previous_syntax_end, (
            "Syntax spans must be ordered and non-overlapping."
        )
        assert span["classes"] != [], (
            "Syntax spans must contain at least one syntax class."
        )
        previous_syntax_end = span["end"]

    if text == "":
        assert tokens == [] and syntax == [], (
            "Empty row text cannot carry diff or syntax decoration."
        )
        return []

    # Both interval sequences are already ordered and non-overlapping (the
    # token cursor and the span assertions above prove it), so one linear
    # merge yields the sorted boundary list without set hashing or sorting.
    token_boundaries = [0]
    for start, end, _token in token_intervals:
        token_boundaries.append(start)
        token_boundaries.append(end)
    syntax_boundaries: list[int] = []
    for span in syntax:
        syntax_boundaries.append(span["start"])
        syntax_boundaries.append(span["end"])
    syntax_boundaries.append(len(text))
    sorted_boundaries: list[int] = []
    token_position = 0
    syntax_position = 0
    while token_position < len(token_boundaries) or syntax_position < len(
        syntax_boundaries
    ):
        if syntax_position >= len(syntax_boundaries) or (
            token_position < len(token_boundaries)
            and token_boundaries[token_position]
            <= syntax_boundaries[syntax_position]
        ):
            value = token_boundaries[token_position]
            token_position += 1
        else:
            value = syntax_boundaries[syntax_position]
            syntax_position += 1
        if sorted_boundaries == [] or sorted_boundaries[-1] != value:
            sorted_boundaries.append(value)

    parts: list[DecoratedPart] = []
    token_index = 0
    syntax_index = 0
    for boundary_index in range(len(sorted_boundaries) - 1):
        start = sorted_boundaries[boundary_index]
        end = sorted_boundaries[boundary_index + 1]

        while (
            token_index < len(token_intervals)
            and token_intervals[token_index][1] <= start
        ):
            token_index += 1
        active_token = (
            token_intervals[token_index][2]
            if token_index < len(token_intervals)
            and token_intervals[token_index][0] <= start
            and end <= token_intervals[token_index][1]
            else None
        )

        while (
            syntax_index < len(syntax) and syntax[syntax_index]["end"] <= start
        ):
            syntax_index += 1
        active_span = (
            syntax[syntax_index]
            if syntax_index < len(syntax)
            and syntax[syntax_index]["start"] <= start
            and end <= syntax[syntax_index]["end"]
            else None
        )

        # Decide the merge before building anything: a mergeable segment
        # only extends the previous part's text, so its dict, class copy,
        # and slices are never constructed.
        part_text = text[start:end]
        diff_status = (
            "unchanged" if active_token is None else active_token["status"]
        )
        is_whitespace = (
            part_text.isspace()
            if active_token is None
            else active_token["is_ws"]
        )
        is_leading_whitespace = (
            active_token is not None
            and token_index == 0
            and active_token["is_ws"]
        )
        previous = parts[-1] if parts != [] else None
        if (
            previous is not None
            and previous["diff_status"] == diff_status
            and previous["is_whitespace"] == is_whitespace
            and previous["is_leading_whitespace"] == is_leading_whitespace
            and previous["syntax_classes"]
            == ([] if active_span is None else active_span["classes"])
        ):
            previous["text"] += part_text
        else:
            parts.append(
                {
                    "text": part_text,
                    "syntax_classes": (
                        []
                        if active_span is None
                        else list(active_span["classes"])
                    ),
                    "diff_status": diff_status,
                    "is_whitespace": is_whitespace,
                    "is_leading_whitespace": is_leading_whitespace,
                }
            )
    return parts


class DiffRow(TypedDict):
    """One row in the rendered text diff grid.

    This is the display/API row shape after engine rows have been enriched with
    decorated parts and backend-owned hunk identity. Frontend fold rows are
    derived separately from `FoldHint` ranges and never enter this shape.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """
    Display status of the real aligned engine row.
    """

    left_no: int | None
    """
    One-based old/left line number, when this row has a left side.
    """

    right_no: int | None
    """
    One-based new/right line number, when this row has a right side.
    """

    left_text: str | None
    """
    Rendered old/left line text.
    """

    right_text: str | None
    """
    Rendered new/right line text.
    """

    left_parts: list[DecoratedPart]
    """
    Complete decorated text partition for the old/left side.
    """

    right_parts: list[DecoratedPart]
    """
    Complete decorated text partition for the new/right side.
    """

    hunk_index: int | None
    """
    Zero-based bay-local identity on the first row of a changed hunk.

    Every other row carries `None`. Display enrichment assigns this field
    before the row enters an API payload, numbering each bay's own rows from
    zero; the frontend walks bays in document order to build the File's
    navigable sequence.
    """


class EnrichedRows(TypedDict):
    """Display-ready result for one text bay's neutral engine rows.

    `rows` contains the complete decorated row sequence and `hunk_count` counts
    its bay-local changed runs. `fold_hints` is omitted when structural parsing
    found no foldable ranges. This contract does not carry engine summary or
    warning data, which remain alongside it in the format layer.
    """

    hunk_count: int
    """Number of bay-local changed runs represented by `rows`."""

    rows: list[DiffRow]
    """Display rows in the engine's original order."""

    fold_hints: NotRequired[list[FoldHint]]
    """Optional structural source ranges the frontend may fold."""


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


_SpanPriority = tuple[int, int, int]
"""Precomputed collapse ordering for one syntax interval.

The triple (span length, negated class count, negated capture order) selects
the shortest, most specific, latest capture; it is fixed when the interval is
created so overlap resolution compares plain tuples at C speed.
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

    def _syntax_spec_for_path(path: str) -> _SyntaxLanguageSpec | None:
        """Return the configured syntax language matching one path."""
        normalized = path.casefold()
        basename = normalized.rsplit("/", 1)[-1]
        for spec in _LANGUAGE_SPECS:
            if basename in spec.filenames:
                return spec
            if any(normalized.endswith(suffix) for suffix in spec.suffixes):
                return spec
        return None

    if path is None:
        return None
    spec = _syntax_spec_for_path(path)
    if spec is None:
        return None

    return _highlight_lines_with_spec(spec, text)


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
    def _inherited_query_names(query_text: str) -> list[str]:
        """Parse inherited tree-sitter query names from query comments."""
        inherited_names: list[str] = []
        for line in query_text.splitlines():
            match = re.match(r"\s*;\s*inherits:\s*(.+)$", line)
            if match is None:
                continue
            inherited_names.extend(
                name.strip()
                for name in match.group(1).split(",")
                if name.strip()
            )
        return inherited_names

    def _sibling_query_path(query_path: str, query_name: str) -> str:
        """Address an inherited query beside the current query file."""
        parent = query_path.rsplit("/", 1)[0]
        return f"{parent}/{query_name}.scm"

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


def _highlight_lines_with_spec(
    spec: _SyntaxLanguageSpec,
    text: str,
) -> list[list[SyntaxSpan]] | None:
    def _classes_for_capture(
        capture_name: str,
    ) -> tuple[SyntaxClass, ...]:
        """Expand one tree-sitter capture into validated prefix classes."""
        parts = [part for part in capture_name.split(".") if part]
        classes: list[SyntaxClass] = []
        for index in range(1, len(parts) + 1):
            syntax_class = f"ts-{'-'.join(parts[:index])}"
            assert _is_syntax_class(syntax_class), (
                f"Tree-sitter emitted undeclared syntax class {syntax_class!r}."
            )
            classes.append(syntax_class)
        return tuple(classes)

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

    line_texts = text.splitlines()
    line_count = len(line_texts)

    # Class expansion is memoized per capture name and byte columns come from
    # each Node's row/column points, so the whole-text character/byte boundary
    # arrays and the two bisects per capture are gone. Only lines containing
    # multibyte characters build a byte-to-character map, once; one whole-text
    # ASCII check short-circuits the per-line decision for the common case.
    classes_by_capture: dict[str, tuple[SyntaxClass, ...]] = {}
    line_byte_maps: dict[int, list[int]] = {}
    text_is_ascii = text.isascii()

    def _character_column(line_index: int, byte_column: int) -> int:
        """Convert one line-local byte column to its character column."""
        line_text = line_texts[line_index]
        if text_is_ascii or line_text.isascii():
            return min(byte_column, len(line_text))
        boundaries = line_byte_maps.get(line_index)
        if boundaries is None:
            boundaries = [0]
            for character in line_text:
                boundaries.append(
                    boundaries[-1] + len(character.encode("utf-8"))
                )
            line_byte_maps[line_index] = boundaries
        return bisect_right(boundaries, byte_column) - 1

    line_intervals: list[
        list[tuple[int, int, tuple[SyntaxClass, ...], _SpanPriority]]
    ] = [[] for _ in line_texts]
    order = 0
    captured_any = False
    for capture_name, nodes in capture_map.items():
        classes: tuple[SyntaxClass, ...] | None = None
        for node in nodes:
            if node.start_byte >= node.end_byte:
                continue
            captured_any = True
            if classes is None:
                classes = classes_by_capture.get(capture_name)
                if classes is None:
                    classes = _classes_for_capture(capture_name)
                    classes_by_capture[capture_name] = classes
            start_row, start_column = node.start_point
            end_row, end_column = node.end_point
            for line_index in range(
                start_row, min(end_row, line_count - 1) + 1
            ):
                local_start = (
                    _character_column(line_index, start_column)
                    if line_index == start_row
                    else 0
                )
                local_end = (
                    _character_column(line_index, end_column)
                    if line_index == end_row
                    else len(line_texts[line_index])
                )
                if local_start >= local_end:
                    continue
                # The collapse priority (shortest span, most classes, latest
                # capture) is a pure function of the interval: fixing it here
                # lets every later comparison run through one C-level key.
                line_intervals[line_index].append(
                    (
                        local_start,
                        local_end,
                        classes,
                        (local_end - local_start, -len(classes), -order),
                    )
                )
            order += 1

    if not captured_any:
        return [[] for _ in line_texts]

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


def _collapse_line_intervals(
    line_text: str,
    intervals: list[tuple[int, int, tuple[SyntaxClass, ...], _SpanPriority]],
) -> list[_SyntaxSpan]:
    if intervals == []:
        return []

    events: list[tuple[int, int, int]] = []
    for index, (start, end, _classes, _priority) in enumerate(intervals):
        events.append((start, 1, index))
        events.append((end, 0, index))
    # The trailing sentinel emits the final active segment inside the loop,
    # so the closing selection logic exists exactly once.
    events.append((len(line_text), 2, -1))
    events.sort(key=itemgetter(0, 1))

    active: dict[
        int,
        tuple[int, int, tuple[SyntaxClass, ...], _SpanPriority],
    ] = {}
    position = 0
    spans: list[_SyntaxSpan] = []
    priority_of = itemgetter(3)

    for event_position, event_kind, interval_index in events:
        if event_position > position and active != {}:
            chosen = min(active.values(), key=priority_of)
            _append_syntax_span(spans, position, event_position, chosen[2])

        if event_kind == 0:
            active.pop(interval_index, None)
        elif event_kind == 1:
            active[interval_index] = intervals[interval_index]
        position = event_position

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


def enrich_rows_for_display(
    *,
    rows: list[DiffEngineRow],
    left_text: str,
    right_text: str,
    left_path_hint: str | None = None,
    right_path_hint: str | None = None,
) -> EnrichedRows:
    """Attach display-only row metadata without calculating diff summary.

    This helper preserves every engine row while assigning hunk identities,
    weaving inline diff tokens with syntax spans, and attaching optional
    syntax-aware fold hints. It does not decide changed/added/removed/moved line
    counts; engines calculate summaries before calling it.
    """
    left_syntax_lines = highlight_lines_for_path(left_path_hint, left_text)
    right_syntax_lines = highlight_lines_for_path(
        right_path_hint,
        right_text,
    )
    fold_hints = fold_hints_for_path(right_path_hint, right_text, rows)
    enriched_rows: list[DiffRow] = []
    hunk_count = 0
    previous_changed = False
    for row in rows:
        changed = engine_row_has_change(row)
        hunk_index = hunk_count if changed and not previous_changed else None
        if hunk_index is not None:
            hunk_count += 1
        previous_changed = changed

        left_no = row["left_no"]
        left_syntax: list[SyntaxSpan] = []
        if (
            left_no is not None
            and left_syntax_lines is not None
            and left_no - 1 < len(left_syntax_lines)
        ):
            left_syntax = left_syntax_lines[left_no - 1]

        right_no = row["right_no"]
        right_syntax: list[SyntaxSpan] = []
        if (
            right_no is not None
            and right_syntax_lines is not None
            and right_no - 1 < len(right_syntax_lines)
        ):
            right_syntax = right_syntax_lines[right_no - 1]

        left_text_value = row["left_text"]
        right_text_value = row["right_text"]
        enriched_rows.append(
            {
                "status": row["status"],
                "left_no": left_no,
                "right_no": right_no,
                "left_text": left_text_value,
                "right_text": right_text_value,
                "left_parts": weave_decorated_parts(
                    left_text_value,
                    row.get("left_tokens", []),
                    left_syntax,
                ),
                "right_parts": weave_decorated_parts(
                    right_text_value,
                    row.get("right_tokens", []),
                    right_syntax,
                ),
                "hunk_index": hunk_index,
            }
        )

    payload: EnrichedRows = {
        "hunk_count": hunk_count,
        "rows": enriched_rows,
    }
    if fold_hints != []:
        payload["fold_hints"] = fold_hints
    return payload
