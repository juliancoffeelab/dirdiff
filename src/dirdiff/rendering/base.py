"""Display enrichment for already-rendered diff rows.

This module does not choose an engine and does not own native text comparison.
It receives already-computed neutral rows, then attaches display-only details:
backend-woven syntax/diff parts, fold hints, backend-owned hunk identities, and
default expansion.
"""

from __future__ import annotations

import importlib
import json
import re
from bisect import bisect_right
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Literal, TypedDict, TypeIs, get_args

from tree_sitter import Language, Parser, Query, QueryCursor

from dirdiff.engines import (
    InlineToken,
    InlineTokenStatus,
    engine_row_has_change,
)
from dirdiff.rendering.fold import fold_hints_for_path

__all__ = [
    "DecoratedPart",
    "DiffRow",
    "SyntaxClass",
    "SyntaxSpan",
    "canonical_json",
    "default_expanded_for_payload",
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
        token_end = token_cursor + len(token_text)
        token_intervals.append((token_cursor, token_end, inline_token))
        token_cursor = token_end
    if tokens != []:
        assert "".join(token["text"] for token in tokens) == text, (
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

    boundaries = {0, len(text)}
    for start, end, _token in token_intervals:
        boundaries.add(start)
        boundaries.add(end)
    for span in syntax:
        boundaries.add(span["start"])
        boundaries.add(span["end"])

    sorted_boundaries = sorted(boundaries)
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
        syntax_classes = (
            list(syntax[syntax_index]["classes"])
            if syntax_index < len(syntax)
            and syntax[syntax_index]["start"] <= start
            and end <= syntax[syntax_index]["end"]
            else []
        )

        part: DecoratedPart = {
            "text": text[start:end],
            "syntax_classes": syntax_classes,
            "diff_status": (
                "unchanged" if active_token is None else active_token["status"]
            ),
            "is_whitespace": (
                text[start:end].isspace()
                if active_token is None
                else active_token["is_ws"]
            ),
            "is_leading_whitespace": (
                active_token is not None
                and token_index == 0
                and active_token["is_ws"]
            ),
        }
        if (
            parts != []
            and parts[-1]["syntax_classes"] == part["syntax_classes"]
            and parts[-1]["diff_status"] == part["diff_status"]
            and parts[-1]["is_whitespace"] == part["is_whitespace"]
            and parts[-1]["is_leading_whitespace"]
            == part["is_leading_whitespace"]
        ):
            parts[-1]["text"] += part["text"]
        else:
            parts.append(part)
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
    weaving inline diff tokens with syntax spans, and attaching optional
    syntax-aware fold hints. It does not decide changed/added/removed/moved line
    counts; engines calculate summaries before calling it.
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
        left_syntax: list[SyntaxSpan] = []
        if (
            isinstance(left_no, int)
            and left_syntax_lines is not None
            and left_no - 1 < len(left_syntax_lines)
        ):
            left_syntax = left_syntax_lines[left_no - 1]

        right_no = row.get("right_no")
        right_syntax: list[SyntaxSpan] = []
        if (
            isinstance(right_no, int)
            and right_syntax_lines is not None
            and right_no - 1 < len(right_syntax_lines)
        ):
            right_syntax = right_syntax_lines[right_no - 1]

        assert (
            "left_text" in row
            and "right_text" in row
            and "left_tokens" in row
            and "right_tokens" in row
        ), (
            "Every engine row requires both text sides and both inline-token "
            "arrays."
        )
        left_text_value = row["left_text"]
        right_text_value = row["right_text"]
        left_tokens = row.pop("left_tokens")
        right_tokens = row.pop("right_tokens")
        row["left_parts"] = weave_decorated_parts(
            "" if left_text_value is None else left_text_value,
            left_tokens,
            left_syntax,
        )
        row["right_parts"] = weave_decorated_parts(
            "" if right_text_value is None else right_text_value,
            right_tokens,
            right_syntax,
        )

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
