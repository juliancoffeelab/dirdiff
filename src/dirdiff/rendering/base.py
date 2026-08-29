"""Display enrichment for already-rendered diff rows.

## Public interface

`weave_decorated_parts` combines inline diff and syntax decoration.
`highlight_lines_for_path` returns syntax spans for supported source names.
`enrich_rows_for_display` applies those spans, fold hints, and bay-local hunk
indexes to neutral engine rows.

## Purpose and boundaries

Engines decide alignment, token status, and summary counts. This module keeps
those decisions intact while producing the display fields the HUD consumes. It
does not select an engine or recalculate the logical diff.
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
    "DiffRow",
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

Tree-sitter captures are validated into this type, stored on `SyntaxSpan`, and
sent to the HUD on decorated parts. The names intentionally match the CSS class
vocabulary.

This value carries syntax only. It does not describe diff status, source
language, or a Tree-sitter node. A new value requires corresponding HUD styling
when it should be visible.
"""


def _is_syntax_class(value: str) -> TypeIs[SyntaxClass]:
    """Narrow a generated capture prefix to the declared CSS vocabulary.

    Capture expansion calls this before constructing `SyntaxSpan` values. A
    false result leaves the generated string untrusted; the caller asserts it
    as a query/configuration defect rather than emitting an unstyled class.
    """
    return value in get_args(SyntaxClass.__value__)


class SyntaxSpan(TypedDict):
    """Apply syntax classes to one half-open character span of a rendered line.

    `highlight_lines_for_path` returns these spans by line; decoration weaving
    intersects them with engine tokens to produce `DecoratedPart` values.

    Offsets are local to one line. Spans must be ordered, non-overlapping, and
    non-empty; they never become review coordinates.
    """

    start: int
    """Inclusive zero-based character offset in the enclosing rendered line.

    It must be nonnegative and strictly less than `end`. Spans returned for one
    line are ordered by this value and never overlap a preceding span.
    """

    end: int
    """Exclusive zero-based character offset in the same rendered line.

    It must not exceed the line length and must be greater than `start`.
    Decoration weaving consumes the half-open pair directly because highlighting
    has already converted Tree-sitter's byte columns to character offsets.
    """

    classes: list[SyntaxClass]
    """Nonempty ordered CSS class hierarchy active over this whole span.

    Class prefixes run from general to specific, such as `ts-variable` before
    its member form. Weaving copies the list to every resulting decorated slice.
    """


class DecoratedPart(TypedDict):
    """One contiguous text slice carrying diff and syntax decoration.

    `weave_decorated_parts` partitions one complete row side into these values;
    the HUD renders them in order without intersecting ranges itself.

    Parts preserve every source character. Adjacent parts differ in syntax,
    diff status, or whitespace role. They do not carry offsets or line identity.
    """

    text: str
    """Nonempty contiguous source slice represented by this decorated part.

    Concatenating all parts for a present row side reproduces its complete text
    exactly. Adjacent parts with identical metadata are merged before return.
    """

    syntax_classes: list[SyntaxClass]
    """Ordered syntax classes active across every character of `text`.

    An empty list means no configured capture covers the slice. Consumers apply
    these classes directly and must not infer additional syntax from neighbors.
    """

    diff_status: InlineTokenStatus
    """Engine token status active across every character of `text`.

    Text outside explicit engine tokens receives `unchanged`. The HUD combines
    this value with syntax classes rather than recalculating token intersections.
    """

    is_whitespace: bool
    """Whether the originating inline token was entirely whitespace.

    For slices outside engine tokens it is derived from `text`. The value may
    remain true on one fragment after syntax boundaries split a whitespace token.
    """

    is_leading_whitespace: bool
    """Whether this slice belongs to a whitespace token at text offset zero.

    The originating engine token must be both first and entirely whitespace.
    Every syntax-split fragment of that token retains true; whitespace after any
    earlier token is false, allowing the HUD to distinguish indentation.
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

    # Parameters

    - `text`: Complete row-side text the result must reproduce.
    - `tokens`: Ordered inline diff partition, or empty for no diff decoration.
    - `syntax`: Ordered non-overlapping syntax spans over the same text.

    # Usage

    `enrich_rows_for_display` calls this once per present row side after syntax
    lookup. Direct callers must pass tokens and spans for the same exact `text`.

    # Failures

    Raises `AssertionError` when tokens do not reproduce `text`, whitespace
    metadata disagrees with token content, or syntax spans are empty,
    overlapping, unordered, or outside the text.
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

    Display enrichment converts each neutral engine row to this shape; format
    payloads send it to the HUD in source order.

    It contains decorated text and bay-local hunk identity. Frontend fold rows
    are derived from `FoldHint` and never enter this shape.
    """

    status: Literal["equal", "replace", "insert", "delete", "move"]
    """Engine-supplied relationship of the aligned source sides.

    Enrichment preserves it unchanged. `insert` lacks a left line, `delete`
    lacks a right line, and equal, replace, and move rows carry both coordinates.
    """

    left_no: int | None
    """One-based old-side source coordinate preserved from the engine row.

    It is `None` for right-only insertion and present for every other status.
    Syntax lookup subtracts one from a present value; rendered row position is
    not a substitute for this coordinate.
    """

    right_no: int | None
    """One-based new-side source coordinate preserved from the engine row.

    It is `None` for left-only deletion and present for every other status.
    Syntax and fold discovery use this source identity independently of row order.
    """

    left_text: str | None
    """Old-side line text preserved from the neutral engine row.

    Enrichment-produced insert rows carry `""` with `left_no=None`; a present
    empty source line uses the same string but has a line number. `None` remains
    a permitted serialized absence, so consumers must use `left_no` to decide
    whether the side exists rather than testing text truthiness.
    """

    right_text: str | None
    """New-side line text preserved from the neutral engine row.

    Enrichment-produced delete rows carry `""` with `right_no=None`; a present
    empty source line uses the same string but has a line number. `None` remains
    a permitted serialized absence, so consumers must use `right_no` for side
    presence instead of interpreting empty text.
    """

    left_parts: list[DecoratedPart]
    """Lossless ordered decoration partition of the old-side row text.

    Concatenated part text equals `left_text` for a represented source line.
    The list is empty when that side has no text or the represented line is empty.
    """

    right_parts: list[DecoratedPart]
    """Lossless ordered decoration partition of the new-side row text.

    Concatenated part text equals `right_text` for a represented source line.
    The list is empty when that side has no text or the represented line is empty.
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

    `enrich_rows_for_display` returns this value to text-bay composition.

    Engine summary and warnings remain alongside this value in the format
    layer. This type does not describe a complete bay.
    """

    hunk_count: int
    """Number of changed runs whose first rows carry a hunk index.

    The value is derived from this bay's enriched rows, not from File-wide
    numbering. Composition forwards it as the text bay's navigation total.
    """

    rows: list[DiffRow]
    """Decorated display rows in the engine's original alignment order.

    Syntax and hunk identity are already woven into these rows. Callers may
    serialize them but must not reorder them or renumber their bay-local hunks.
    """

    fold_hints: NotRequired[list[FoldHint]]
    """Validated foldable regions over `rows`, omitted when none are known.

    Hints are presentation metadata discovered from the right-side syntax.
    They never remove rows or change the engine's alignment and hunk count.
    """


@dataclass(frozen=True)
class _SyntaxLanguageSpec:
    """Configure syntax highlighting for one source-file family.

    Module initialization defines these private values. Path selection chooses
    one by exact filename or suffix, then highlighting loads its language and
    query resources.

    It is configuration, not a loaded parser or cache, and never crosses the
    rendering module boundary.
    """

    module_name: str
    """Importable package containing the configured Tree-sitter language factory.

    It also supplies `query_path` unless `query_package` overrides that resource
    lookup. Import failure makes highlighting unavailable for the selected File.
    """

    query_path: str
    """Package-relative highlight query loaded for this grammar.

    The path is resolved in `query_package` when present, otherwise in
    `module_name`; inherited sibling query names resolve beside this resource.
    """

    suffixes: tuple[str, ...]
    """Lowercase path endings that select this spec after filename matching.

    Selection scans specs in declaration order, so entries must avoid ambiguous
    suffixes whose first match would load the wrong grammar or query.
    """

    filenames: tuple[str, ...] = ()
    """Exact case-folded basenames that select this spec before suffixes.

    The empty tuple declares no basename exception. A configured basename may
    select a grammar even when the path has no recognized suffix.
    """

    language_attr: str = "language"
    """Factory attribute read from `module_name` to construct the language.

    Most grammar packages expose `language`; variants such as OCaml override it.
    A missing attribute is treated as unavailable highlighting, not guessed.
    """

    query_package: str | None = None
    """Package containing the highlight resource when separate from the grammar.

    `None` resolves `query_path` in `module_name`. This changes resource lookup
    only; language construction always uses `module_name` and `language_attr`.
    """


_SpanPriority = tuple[int, int, int]
"""Precomputed coalescing priority for one syntax interval.

The triple (span length, negated class count, negated capture order) selects
the shortest, most specific, latest capture; it is fixed when the interval is
created so overlap resolution compares plain tuples at C speed.
"""


@dataclass(frozen=True)
class _SyntaxSpan:
    """Retain one resolved syntax interval before line-local conversion.

    Highlight overlap resolution creates these private values, then converts
    them to `SyntaxSpan` after assigning the interval to a rendered line.

    Offsets are line-local character positions after Tree-sitter byte columns
    have been converted. The value never crosses the rendering module boundary.
    """

    start: int
    """Inclusive character offset of this resolved slice in one display line.

    Interval merging creates values in increasing order and `_append_syntax_span`
    rejects empty ranges before storing them.
    """

    end: int
    """Exclusive character offset of this resolved slice in the same line.

    It is greater than `start` and no greater than the line length. Equal-class
    neighbors merge when the previous end equals the next start.
    """

    classes: tuple[SyntaxClass, ...]
    """Nonempty class hierarchy selected for the entire resolved interval.

    Overlap coalescing chooses this tuple by interval priority; public conversion
    copies it to a list without changing order.
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
"""Closed mapping from source names to syntax grammars and highlight queries.

`highlight_lines_for_path` scans this order after exact filename checks inside
each spec. Entries configure parser and query loading only; unsupported paths
remain undecorated rather than borrowing another language.
"""


def highlight_lines_for_path(
    path: str | None,
    text: str,
) -> list[list[SyntaxSpan]] | None:
    """Return syntax spans for display rendering, if a parser is available.

    Highlighting is part of the rendered row payload, not part of diff-engine
    comparison.  The renderer uses the path hint only to choose a tree-sitter
    language and query; unsupported languages, missing parsers, and missing
    query files all produce `None`; callers then leave that side undecorated.

    # Parameters

    - `path`: Source path hint used only to choose a configured grammar.
    - `text`: Complete source text parsed and partitioned into display lines.

    # Usage

    Pass the engine side's path hint and complete source text before mapping
    spans onto rows. `None` means no supported highlighter was available; it is
    not a rendering failure.

    # Returns

    - `list[list[SyntaxSpan]]`: One syntax-span list per display line, including
      empty lists for lines with no captured syntax.
    - `None`: The path is absent or unsupported, or its configured grammar or
      query cannot load. The caller must leave this side undecorated.
    """

    def _syntax_spec_for_path(path: str) -> _SyntaxLanguageSpec | None:
        """Choose one immutable grammar/query spec from a source path hint.

        Exact basenames win before suffixes. Unsupported paths return `None`,
        which tells the public highlighting boundary to leave the side
        undecorated rather than guessing a language.

        # Returns

        - `_SyntaxLanguageSpec`: The first exact-filename or suffix match from
          `_LANGUAGE_SPECS`.
        - `None`: No configured grammar claims the path. The caller must leave
          syntax highlighting unavailable for this side.
        """
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
    """Load and cache one grammar with its resolved highlight query.

    Cache keys describe the complete parser/query choice, so repeated Files of
    one language reuse immutable Tree-sitter setup. Import, attribute, resource,
    and query errors propagate to `_highlight_lines_with_spec`, the boundary
    that turns unsupported highlighting into no decoration.

    # Parameters

    - `module_name`: Python package exporting the grammar factory.
    - `language_attr`: Factory attribute inside that package.
    - `query_path`: Package-relative highlight query path.
    - `query_package`: Optional package containing the query instead of the
      grammar package.

    # Returns

    - `First`: The constructed Tree-sitter grammar.
    - `Second`: The highlight query compiled against that same grammar.
    """
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
    """Load a highlight query after expanding declared sibling inheritance.

    Inherited query names come from Tree-sitter's `; inherits:` comments. They
    are loaded before the current file so its captures retain final precedence.

    # Parameters

    - `package_name`: Package containing the query resources.
    - `query_path`: Package-relative path of the current query.
    """

    def _inherited_query_names(query_text: str) -> list[str]:
        """Extract ordered sibling query names from `; inherits:` comments.

        Empty names are discarded and multiple comments append in file order.
        The caller loads these resources before the current query so its
        captures retain final precedence.
        """
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
        """Address an inherited query beside the current query file.

        # Parameters

        - `query_path`: Current package-relative query path.
        - `query_name`: Bare inherited query name from its comment.
        """
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
    """Parse text with one syntax spec and return line-local highlight spans.

    Known setup failures mean highlighting is unavailable and return `None`.
    Successful parsing preserves line count even when the query captures
    nothing. Overlapping captures coalesce deterministically before conversion
    to public spans.

    # Parameters

    - `spec`: Grammar, query, and filename-independent language configuration.
    - `text`: Complete source text to parse.

    # Returns

    - `list[list[SyntaxSpan]]`: Captures converted to character-based spans for
      each display line, with overlapping classes merged deterministically.
    - `None`: Import, grammar, resource, or query setup failed. The caller must
      render the side without syntax decoration.
    """

    def _classes_for_capture(
        capture_name: str,
    ) -> tuple[SyntaxClass, ...]:
        """Return every declared CSS prefix of one dotted capture name.

        For `variable.member`, the result contains `ts-variable` followed by
        `ts-variable-member`. An undeclared prefix is a bundled-query contract
        failure and is asserted before a public syntax span can contain it.

        # Returns

        - `Members`: Declared CSS classes for every nonempty prefix of the dotted
          capture name.
        - `Order`: Classes run from the broadest prefix through the complete
          capture, matching the frontend's cascade order.

        # Failures

        Asserts when a bundled query emits a syntax class outside
        `SyntaxClass`.
        """
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
        """Convert one line-local byte column to its character column.

        ASCII lines use the byte column directly. A multibyte line builds and
        caches its boundary table once for every capture on that line.

        # Parameters

        - `line_index`: Zero-based display line addressed by Tree-sitter.
        - `byte_column`: UTF-8 byte offset within that line.
        """
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
                # The coalescing priority (shortest span, most classes, latest
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
    """Resolve overlapping syntax intervals into one ordered line partition.

    At each boundary the shortest, most specific, latest capture wins. Adjacent
    winning intervals with the same classes merge before public conversion.

    # Parameters

    - `line_text`: Complete display line defining the terminal boundary.
    - `intervals`: Captured character intervals and precomputed priorities.
    """
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
    """Append one non-empty resolved syntax slice, merging equal neighbors.

    # Parameters

    - `spans`: Ordered output list to extend.
    - `start`: Inclusive character offset in the display line.
    - `end`: Exclusive character offset in that line.
    - `classes`: Complete syntax-class tuple selected for the slice.
    """
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

    # Parameters

    - `rows`: Complete neutral engine rows to enrich without reordering.
    - `left_text`: Complete old text used for syntax span lookup.
    - `right_text`: Complete new text used for syntax and fold discovery.
    - `left_path_hint`: Optional old path selecting a syntax grammar.
    - `right_path_hint`: Optional new path selecting syntax and fold grammars.

    # Usage

    Call once after an engine has produced complete neutral rows for one text
    bay. Preserve the returned row order and bay-local hunk indexes.

    # Failures

    Raises `AssertionError` when an engine row's inline tokens cannot reproduce
    its text or syntax decoration violates the span contract. Unsupported
    syntax and fold grammars are omitted instead of raised.
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
