"""Tree-sitter fold hint discovery for rendered diff rows.

## Public interface

`engine_row_has_change` applies the shared visible-change predicate.
`fold_hints_for_path` returns safe right-side fold ranges for supported source
names. `FoldHint` is the range and label contract consumed by the HUD.

## Purpose and boundaries

Tree-sitter identifies structural source regions, but displayed row status
decides whether those regions are unchanged and safe to fold. This module joins
those facts after engine alignment. It returns display metadata without
changing rows or diff semantics.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Literal, TypedDict

from tree_sitter import Language, Node, Parser, Query, QueryCursor

from dirdiff.engines import DiffEngineRow

__all__ = ["FoldHint", "engine_row_has_change", "fold_hints_for_path"]


def engine_row_has_change(row: DiffEngineRow) -> bool:
    """Return whether one neutral engine row contributes visible diff content.

    Non-equal row status is sufficient. An equal paired row still counts when
    either side contains a changed inline token, which keeps whitespace-only
    and GumTree-neutral rows out of foldable unchanged regions.

    # Usage

    Display enrichment uses this while assigning hunk starts, and fold
    discovery uses the same predicate so the two boundaries agree.
    """
    if row["status"] != "equal":
        return True
    return any(
        token["status"] != "unchanged" for token in row.get("left_tokens", [])
    ) or any(
        token["status"] != "unchanged" for token in row.get("right_tokens", [])
    )


class FoldHint(TypedDict):
    """Foldable source region discovered while rendering a file diff.

    Fold discovery returns these hints beside already-rendered rows. The HUD may
    hide the half-open row interval and show `label` under policy selected by
    `kind`.

    A hint never changes alignment, removes a hunk, or represents a folded DOM
    row itself.
    """

    start_row: int
    """Zero-based first rendered row the HUD may hide, included.

    It indexes the unchanged `DiffEngineRow` sequence supplied to discovery and
    is strictly less than `end_row`. It is a display-row coordinate, not a
    source line number.
    """

    end_row: int
    """Exclusive rendered-row boundary of the hidden interval.

    The half-open range `start_row:end_row` remains within the emitted row list
    and contains at least the selected rule's minimum hidden-row count.
    """

    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    """Structural category governing how the HUD presents this interval.

    It comes from the accepted language rule, except `top_level`, which is
    produced by grouped unchanged root items. The value is policy, not parser
    node identity or current folded state.
    """

    label: str
    """Presentation text shown while the interval is hidden.

    Structural hints prefer the visible line before a hidden body, with captured
    section labels taking precedence; grouped top-level hints summarize item
    categories. An empty string means no honest source label was available.
    """


RegionKind = Literal[
    "function_like",
    "class_like",
    "container",
    "section",
    "top_level",
]
"""Classify a foldable source region for HUD policy.

- `function_like` and `class_like` describe named declarations.
- `container` describes structural bodies such as objects or blocks.
- `section` describes a named document section.
- `top_level` groups unchanged top-level items.

Fold rules assign this value and `FoldHint` exposes it. It does not identify the
source language or decide whether the interval is currently folded.
"""
StartMode = Literal["node_start", "next_line"]
"""Choose where a fold rule begins its hidden interval.

- `node_start` includes the captured node's first row.
- `next_line` keeps the first row visible and starts with its body.

This controls candidate construction only. It does not set minimum size or HUD
expansion state.
"""


@dataclass(frozen=True)
class FoldRule:
    """One query-pattern policy for turning syntax nodes into fold candidates.

    Fold language specs pair query-pattern indexes with these rules. Candidate
    construction uses the rule to translate a captured syntax node into
    rendered-row coordinates.

    It defines category, start position, and minimum hidden size. It does not
    contain a query, parser, source range, or current change state.
    """

    region_kind: RegionKind
    """HUD policy category assigned to candidates from this query pattern.

    It controls unchanged-region recursion and becomes `FoldHint.kind` if the
    candidate survives size and change checks; it does not select the parser.
    """

    start_mode: StartMode
    """Rule for translating the captured syntax node into hidden source lines.

    `node_start` includes its first line; `next_line` keeps that line visible and
    begins with the following line. Context coordinates remain the full node.
    """

    min_hidden_rows: int
    """Positive minimum length of the candidate's hidden row interval.

    `_candidate_to_hint` applies it after mapping source lines to display rows.
    Shorter candidates are omitted even when their full context is unchanged.
    """


@dataclass(frozen=True)
class FoldLanguageSpec:
    """Tree-sitter language, query, and rule set for one file family.

    Module initialization defines one value per supported file family. Fold
    discovery selects it by filename or suffix, loads the Tree-sitter language
    and query, and maps each query pattern to `rules`.

    The type stores configuration only. It has no parser instance, parsed tree,
    source text, or fold result.
    """

    module_name: str
    """Importable grammar package containing the configured language factory.

    Fold discovery imports it only after a path selects this spec. Import or
    factory lookup failure makes fold discovery unavailable for that File.
    """

    query_path: str
    """Package-relative fold query loaded from dirdiff resources.

    Query pattern numbers index `rules`; a pattern beyond that tuple is ignored
    because no policy contract exists for its captures.
    """

    suffixes: tuple[str, ...]
    """Lowercase path endings that select this grammar and query spec.

    Selection runs after exact filename checks and follows spec declaration
    order, so a matched suffix fixes both parsing and rule interpretation.
    """

    rules: tuple[FoldRule, ...]
    """Complete positional policy table for supported query patterns.

    Candidate collection uses each match's pattern index directly. The tuple
    therefore must stay ordered with `query_path`; it is not a set of equivalent
    rules and must not be reordered independently.
    """

    filenames: tuple[str, ...] = ()
    """Exact case-folded basenames that select this spec before suffixes.

    The empty tuple supplies no basename exception. This permits extensionless
    language files to choose the same grammar and rule table explicitly.
    """

    language_attr: str = "language"
    """Factory attribute read from the selected grammar package.

    The default matches most Tree-sitter bindings; language variants override
    it. Missing attributes stop folding for the File instead of being guessed.
    """


@dataclass
class FoldCandidate:
    """Intermediate foldable region before display policy accepts it.

    Query processing creates these values before policy decides which folds to
    expose. Parent assignment and filtering use the retained syntax nodes, byte
    spans, rendered rows, rule, and label.

    This mutable scratch value exists only during one discovery call. It is not
    a `FoldHint`, source region exposed to callers, or persisted fold state.
    """

    rule: FoldRule
    """Pattern-indexed policy that produced this candidate.

    Later filtering reads its category, hidden-start mode, and minimum size;
    parent assignment and source coordinates never replace this rule.
    """

    fold_node: Node
    """Captured syntax node from which the hidden line interval is derived.

    `rule.start_mode` decides whether its first line is included. The node
    belongs to the one parsed tree retained for this discovery call only.
    """

    context_node: Node
    """Syntax node whose full row span must be unchanged before folding.

    Function and class body captures use their parent declaration as context;
    other candidates use `fold_node`. It also supplies byte containment bounds.
    """

    label_text: str | None
    """Trimmed `fold.label` capture, or `None` when absent or blank.

    Section hints prefer it over visible-row text; other structural hints use it
    only when no nonblank line immediately precedes the hidden interval.
    """

    context_start_row: int
    """Zero-based first display row of `context_node`, included.

    Change policy inspects rows from here through `context_end_row`; this may
    begin before the interval the frontend is allowed to hide.
    """

    context_end_row: int
    """Exclusive display-row boundary of the complete context region.

    Together with `context_start_row` it defines the nonempty slice that must be
    engine-unchanged. It is separate from the narrower hidden boundary.
    """

    hidden_start_row: int
    """Zero-based first display row proposed for hiding, included.

    It comes from `fold_node` and `rule.start_mode`, lies within the context
    range, and becomes `FoldHint.start_row` only after policy accepts it.
    """

    hidden_end_row: int
    """Exclusive display-row boundary of the proposed hidden slice.

    It lies no later than `context_end_row`. The difference from
    `hidden_start_row` is checked against the rule's minimum before emission.
    """

    context_start_byte: int
    """Inclusive Tree-sitter byte offset of `context_node` in parsed source.

    Candidate sorting and parent assignment use it with `context_end_byte`;
    frontend hints never expose this private parser coordinate.
    """

    context_end_byte: int
    """Exclusive Tree-sitter byte offset of `context_node` in parsed source.

    Strict containment requires this pair to enclose another candidate with at
    least one unequal edge, so equal spans never become parent and child.
    """

    parent: FoldCandidate | None = None
    """Nearest candidate whose context byte span strictly contains this one.

    Collection leaves it `None`; `_assign_candidate_parents` sets it once after
    deduplication. `None` afterward means a root candidate, and policy traversal
    must not infer a parent from row overlap.
    """


@dataclass(frozen=True)
class TopLevelItem:
    """Top-level source item used for grouped unchanged folds.

    Fold discovery creates these values for imports, declarations, or JSON
    members that have no body-style fold. Adjacent unchanged items with the same
    category may become one `top_level` fold hint.

    The value is private discovery input. It does not represent a syntax node,
    rendered fold state, or independently navigable region.
    """

    start_row: int
    """Zero-based first rendered row covered by this root item, included.

    The coordinate exists only when both source-span edges map to right-side
    rows. Grouping uses it to include intervening unchanged display rows.
    """

    end_row: int
    """Exclusive rendered-row boundary of this root item.

    The half-open slice must be nonempty and unchanged to join a grouped hint;
    a changed row ends the current top-level run.
    """

    start_byte: int
    """Inclusive Tree-sitter byte offset of the classified root node.

    Collection retains source order through this value even when rendered-row
    alignment contains inserted rows between adjacent items.
    """

    end_byte: int
    """Exclusive Tree-sitter byte offset of the same classified root node.

    It bounds the private source item only; grouped public hints use row
    coordinates and never expose this byte range.
    """

    label_kind: str
    """Stable item category used to form and label homogeneous runs.

    Adjacent unchanged items with a different category end the run. The value is
    presentation vocabulary such as declaration or import, not a syntax-node id.
    """


TOP_LEVEL_NODE_KINDS: dict[str, str] = {
    "abstract_class_declaration": "declaration",
    "class_declaration": "declaration",
    "class_definition": "declaration",
    "const_item": "declaration",
    "decorated_definition": "declaration",
    "enum_item": "declaration",
    "expression_statement": "declaration",
    "function_declaration": "declaration",
    "function_definition": "declaration",
    "function_item": "declaration",
    "future_import_statement": "import",
    "generator_function_declaration": "declaration",
    "impl_item": "declaration",
    "import_from_statement": "import",
    "import_statement": "import",
    "interface_declaration": "declaration",
    "let_declaration": "declaration",
    "lexical_declaration": "declaration",
    "method_definition": "declaration",
    "media_statement": "declaration",
    "keyframes_statement": "declaration",
    "rule_set": "declaration",
    "static_item": "declaration",
    "struct_item": "declaration",
    "supports_statement": "declaration",
    "trait_item": "declaration",
    "type_alias_declaration": "declaration",
    "type_item": "declaration",
    "use_declaration": "import",
    "variable_declaration": "declaration",
}
"""Tree-sitter root-child kinds eligible for grouped top-level folds.

Values are label categories, not parser kinds. `_classify_top_level_node` keeps
this set closed so incidental syntax nodes never become review-hiding groups.
"""


FOLD_LANGUAGE_SPECS: tuple[FoldLanguageSpec, ...] = (
    FoldLanguageSpec(
        module_name="tree_sitter_python",
        query_path="queries/folds/python.scm",
        suffixes=(".py", ".pyi", ".pyw"),
        rules=(
            FoldRule("function_like", "node_start", 1),
            FoldRule("class_like", "node_start", 1),
            FoldRule("container", "next_line", 2),
        ),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_javascript",
        query_path="queries/folds/javascript.scm",
        suffixes=(".js", ".jsx", ".mjs", ".cjs"),
        rules=(
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("container", "next_line", 2),
        ),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/folds/typescript.scm",
        suffixes=(".ts", ".mts", ".cts"),
        rules=(
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("container", "next_line", 2),
        ),
        language_attr="language_typescript",
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/folds/typescript.scm",
        suffixes=(".tsx",),
        rules=(
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("container", "next_line", 2),
        ),
        language_attr="language_tsx",
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_rust",
        query_path="queries/folds/rust.scm",
        suffixes=(".rs",),
        rules=(
            FoldRule("function_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("class_like", "next_line", 1),
            FoldRule("container", "next_line", 2),
        ),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_css",
        query_path="queries/folds/css.scm",
        suffixes=(".css",),
        rules=(
            FoldRule("container", "next_line", 2),
            FoldRule("container", "next_line", 2),
            FoldRule("container", "next_line", 2),
            FoldRule("container", "next_line", 2),
        ),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_json",
        query_path="queries/folds/json.scm",
        suffixes=(".json",),
        rules=(FoldRule("container", "next_line", 2),),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_toml",
        query_path="queries/folds/toml.scm",
        suffixes=(".toml",),
        rules=(FoldRule("container", "next_line", 2),),
        filenames=("pyproject.toml",),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_yaml",
        query_path="queries/folds/yaml.scm",
        suffixes=(".yaml", ".yml"),
        rules=(FoldRule("container", "node_start", 2),),
    ),
    FoldLanguageSpec(
        module_name="tree_sitter_markdown",
        query_path="queries/folds/markdown.scm",
        suffixes=(".md", ".markdown"),
        rules=(FoldRule("section", "node_start", 1),),
    ),
)
"""Fold queries and pattern-index rules for every supported source family.

`_spec_for_path` selects one entry by exact filename or suffix. Rule order must
match the corresponding query's pattern order because captures carry only that
numeric pattern index into candidate construction.
"""


def fold_hints_for_path(
    path: str | None,
    text: str,
    rows: list[DiffEngineRow],
) -> list[FoldHint]:
    """Return fold hints for the right side of an already-rendered diff.

    Fold hints are computed during rendering because they depend on both source
    structure and displayed row status. The parser sees `text` and `path` only
    to find foldable source regions; the final hints are accepted only when those
    regions map cleanly to `rows` and remain unchanged in the rendered diff.
    Unsupported languages, missing tree-sitter packages, parse/query failures,
    and paths without right-side rows all produce an empty list.

    # Parameters

    - `path`: New-side source path used only to select fold grammar policy.
    - `text`: Complete new-side text parsed for structural regions.
    - `rows`: Already-rendered rows whose right line numbers and changes decide
      whether a candidate can become a hint.

    # Usage

    `enrich_rows_for_display` calls this with the new-side path, complete
    new-side text, and the engine rows for the same bay. Treat an empty list as
    no safe structural folds, not as a failed diff.
    """

    if path is None or rows == []:
        return []

    spec = _spec_for_path(path)
    if spec is None:
        return []

    try:
        language, query = _load_language_query(
            spec.module_name,
            spec.language_attr,
            spec.query_path,
        )
    except ImportError, AttributeError, FileNotFoundError, OSError, ValueError:
        return []

    parser = Parser(language)
    source_bytes = text.encode("utf-8")
    tree = parser.parse(source_bytes)
    right_line_to_row = {
        row["right_no"]: index
        for index, row in enumerate(rows)
        if row["right_no"] is not None
    }
    if right_line_to_row == {}:
        return []

    cursor = QueryCursor(query)
    if any(rule.region_kind == "section" for rule in spec.rules):
        hints = _collect_markdown_section_hints(
            spec,
            cursor.matches(tree.root_node),
            source_bytes,
            right_line_to_row,
            rows,
        )
    else:
        candidates = _collect_candidates(
            spec,
            cursor.matches(tree.root_node),
            source_bytes,
            right_line_to_row,
        )
        hints = []
        if candidates != []:
            _assign_candidate_parents(candidates)
            root_candidates = sorted(
                (
                    candidate
                    for candidate in candidates
                    if candidate.parent is None
                ),
                key=lambda candidate: (
                    candidate.context_start_byte,
                    candidate.context_end_byte,
                ),
            )

            for candidate in root_candidates:
                _collect_hints(candidate, candidates, rows, hints)
        hints.extend(
            _collect_top_level_hints(
                tree.root_node,
                source_bytes,
                right_line_to_row,
                rows,
                hints,
            )
        )
        hints.sort(key=_fold_hint_sort_key)
    return hints


def _collect_markdown_section_hints(
    spec: FoldLanguageSpec,
    matches: list[tuple[int, dict[str, list[Node]]]],
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
    rows: list[DiffEngineRow],
) -> list[FoldHint]:
    """Collect hierarchical Markdown section folds from heading captures.

    Markdown folds are based on heading ranges rather than ordinary code-block
    containers.  The section body extends until the next heading at the same or
    higher level, and trailing blank rows are ignored when deciding whether a
    changed section can be folded.

    # Parameters

    - `spec`: Markdown language policy whose first rule describes sections.
    - `matches`: Query matches containing heading labels in source order.
    - `source_bytes`: Exact UTF-8 source parsed by Tree-sitter.
    - `right_line_to_row`: One-based source-line to rendered-row mapping.
    - `rows`: Rendered rows used to reject changed section contexts.
    """

    if spec.rules == ():
        return []

    headings: list[tuple[Node, int, str]] = []
    for pattern_index, capture_map in matches:
        if pattern_index >= len(spec.rules):
            continue
        label_nodes = capture_map.get("fold.label")
        if label_nodes is None or label_nodes == []:
            continue
        heading = label_nodes[0]
        heading_lines = _node_text(heading, source_bytes).splitlines()
        label_text = heading_lines[0].strip() if heading_lines else ""
        headings.append(
            (
                heading,
                _markdown_heading_level(heading),
                label_text,
            )
        )

    if headings == []:
        return []

    # One pass over the source fixes every line's end offset, so per-heading
    # context extension is an index lookup instead of a byte re-walk.
    line_end_bytes: list[int] = []
    position = 0
    for line in source_bytes.splitlines(keepends=True):
        position += len(line)
        line_end_bytes.append(position)

    candidates: list[FoldCandidate] = []
    rule = spec.rules[0]
    for index, (heading, level, label_text) in enumerate(headings):
        context_start_line, _ = _node_line_span(heading, source_bytes)
        _, heading_end_line = _node_line_span(heading, source_bytes)
        context_end_line = max(right_line_to_row)
        for next_heading, next_level, _next_label in headings[index + 1 :]:
            if next_level <= level:
                context_end_line = next_heading.start_point.row
                break

        row_span = _lines_to_row_span(
            right_line_to_row,
            context_start_line,
            context_end_line,
        )
        hidden_row_span = _lines_to_row_span(
            right_line_to_row,
            heading_end_line + 1,
            context_end_line,
        )
        if row_span is None or hidden_row_span is None:
            continue

        candidates.append(
            FoldCandidate(
                rule=rule,
                fold_node=heading,
                context_node=heading,
                label_text=label_text,
                context_start_row=row_span[0],
                context_end_row=row_span[1],
                hidden_start_row=hidden_row_span[0],
                hidden_end_row=hidden_row_span[1],
                context_start_byte=heading.start_byte,
                context_end_byte=(
                    line_end_bytes[context_end_line - 1]
                    if 1 <= context_end_line <= len(line_end_bytes)
                    else len(source_bytes)
                ),
            )
        )

    if candidates == []:
        return []

    _assign_candidate_parents(candidates)
    root_candidates = sorted(
        (candidate for candidate in candidates if candidate.parent is None),
        key=lambda candidate: (
            candidate.context_start_byte,
            candidate.context_end_byte,
        ),
    )
    hints: list[FoldHint] = []
    for candidate in root_candidates:
        _collect_hints(candidate, candidates, rows, hints)
    hints.sort(key=_fold_hint_sort_key)
    return hints


def _collect_candidates(
    spec: FoldLanguageSpec,
    matches: list[tuple[int, dict[str, list[Node]]]],
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
) -> list[FoldCandidate]:
    """Convert tree-sitter query matches into deduplicated fold candidates.

    Query captures are expressed in source-node space.  This helper projects
    each accepted capture into rendered right-side rows, keeps useful labels,
    chooses the surrounding context node for functions/classes, and deduplicates
    repeated captures that describe the same hidden row span.

    # Parameters

    - `spec`: Selected language rules indexed by query pattern.
    - `matches`: Tree-sitter query matches and their captures.
    - `source_bytes`: Exact parsed UTF-8 source.
    - `right_line_to_row`: Source-line to rendered-row mapping.
    """

    deduped: dict[tuple[str, int, int, int, int], FoldCandidate] = {}

    for pattern_index, capture_map in matches:
        if pattern_index >= len(spec.rules):
            continue
        fold_nodes = capture_map.get("fold")
        if fold_nodes is None or fold_nodes == []:
            continue

        rule = spec.rules[pattern_index]
        fold_node = fold_nodes[0]
        context_node = (
            fold_node.parent
            if rule.region_kind in {"function_like", "class_like"}
            and fold_node.parent is not None
            else fold_node
        )
        context_start_line, context_end_line = _node_line_span(
            context_node, source_bytes
        )
        hidden_start_line, hidden_end_line = _hidden_line_span(
            fold_node,
            source_bytes,
            rule.start_mode,
        )
        if hidden_start_line > hidden_end_line:
            continue

        row_span = _lines_to_row_span(
            right_line_to_row,
            context_start_line,
            context_end_line,
        )
        hidden_row_span = _lines_to_row_span(
            right_line_to_row,
            hidden_start_line,
            hidden_end_line,
        )
        if row_span is None or hidden_row_span is None:
            continue

        label_nodes = capture_map.get("fold.label", [])
        label_text = (
            _node_text(label_nodes[0], source_bytes).strip()
            if label_nodes
            else None
        )
        if label_text == "":
            label_text = None
        candidate = FoldCandidate(
            rule=rule,
            fold_node=fold_node,
            context_node=context_node,
            label_text=label_text,
            context_start_row=row_span[0],
            context_end_row=row_span[1],
            hidden_start_row=hidden_row_span[0],
            hidden_end_row=hidden_row_span[1],
            context_start_byte=context_node.start_byte,
            context_end_byte=context_node.end_byte,
        )
        dedupe_key = (
            rule.region_kind,
            candidate.context_start_byte,
            candidate.context_end_byte,
            candidate.hidden_start_row,
            candidate.hidden_end_row,
        )
        existing = deduped.get(dedupe_key)
        if existing is None or (
            existing.label_text is None and candidate.label_text is not None
        ):
            deduped[dedupe_key] = candidate

    return sorted(
        deduped.values(),
        key=lambda candidate: (
            candidate.context_start_byte,
            candidate.context_end_byte,
        ),
    )


def _assign_candidate_parents(candidates: list[FoldCandidate]) -> None:
    """Attach each candidate to the smallest containing candidate, if any.

    Parent links let later policy recurse through the syntax hierarchy without
    repeatedly searching by byte span.  Containment is strict, so a candidate
    cannot become its own parent or parent a duplicate with the same span.
    One sweep over the candidates ordered by span (outermost first at equal
    starts) keeps the currently enclosing candidates on a stack, so the work
    is sort-bound instead of quadratic in candidate count.
    """

    ordered = sorted(
        candidates,
        key=lambda candidate: (
            candidate.context_start_byte,
            -candidate.context_end_byte,
        ),
    )
    enclosing: list[FoldCandidate] = []
    for candidate in ordered:
        while len(enclosing) > 0 and not _contains(enclosing[-1], candidate):
            enclosing.pop()
        candidate.parent = enclosing[-1] if len(enclosing) > 0 else None
        enclosing.append(candidate)


def _collect_hints(
    candidate: FoldCandidate,
    all_candidates: list[FoldCandidate],
    rows: list[DiffEngineRow],
    hints: list[FoldHint],
) -> None:
    """Apply fold policy recursively and append accepted display hints.

    The policy prefers folding unchanged outer class/function regions.  When a
    function or section changed, unchanged child regions may still be useful, so
    recursion continues into the relevant child candidates.  Containers are
    intentionally limited outside function/class ancestors to avoid noisy nested
    folds inside code bodies.

    # Parameters

    - `candidate`: Current structural candidate whose policy is evaluated.
    - `all_candidates`: Complete hierarchy used to locate direct children.
    - `rows`: Rendered rows used for unchanged-region decisions and labels.
    - `hints`: Output list receiving accepted hints in traversal order.
    """

    if candidate.rule.region_kind == "class_like":
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
        for child in _child_candidates(candidate, all_candidates):
            if child.rule.region_kind in {"function_like", "class_like"}:
                _collect_hints(child, all_candidates, rows, hints)
    elif candidate.rule.region_kind == "function_like":
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
        else:
            for child in _child_candidates(candidate, all_candidates):
                if child.rule.region_kind in {"function_like", "class_like"}:
                    _collect_hints(child, all_candidates, rows, hints)
    elif candidate.rule.region_kind == "container":
        if not _has_ancestor_kind(candidate, "function_like") and not (
            _has_ancestor_kind(candidate, "class_like")
        ):
            if _region_is_unchanged(candidate, rows):
                hint = _candidate_to_hint(candidate, rows)
                if hint is not None:
                    hints.append(hint)
            else:
                for child in _child_candidates(candidate, all_candidates):
                    if child.rule.region_kind == "container":
                        _collect_hints(child, all_candidates, rows, hints)
    elif candidate.rule.region_kind == "section":
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
        else:
            for child in _child_candidates(candidate, all_candidates):
                if child.rule.region_kind == "section":
                    _collect_hints(child, all_candidates, rows, hints)


def _candidate_to_hint(
    candidate: FoldCandidate,
    rows: list[DiffEngineRow],
) -> FoldHint | None:
    """Convert an accepted candidate into the compact API fold-hint shape.

    This is the last policy gate for structural folds.  Candidates that would
    hide too little rendered content are discarded, and labels prefer the first
    visible line immediately before the hidden body unless Markdown section
    captures supplied a better heading label.

    # Parameters

    - `candidate`: Policy-accepted structural region.
    - `rows`: Rendered rows supplying size and visible label context.

    # Returns

    - `FoldHint`: The candidate's half-open rendered row range, structural kind,
      and best available visible label.
    - `None`: The candidate hides fewer rows than its rule permits. The caller
      must omit it from the fold-hint list.
    """

    hidden_rows = candidate.hidden_end_row - candidate.hidden_start_row
    if hidden_rows < candidate.rule.min_hidden_rows:
        return None
    visible_index = candidate.hidden_start_row - 1
    visible_label = ""
    if 0 <= visible_index < len(rows):
        raw_visible_label = rows[visible_index]["right_text"]
        visible_label = (
            "" if raw_visible_label is None else raw_visible_label.strip()
        )
    if candidate.rule.region_kind == "section":
        if candidate.label_text is not None:
            label = candidate.label_text
        else:
            label = visible_label
    elif visible_label != "":
        label = visible_label
    elif candidate.label_text is not None:
        label = candidate.label_text
    else:
        label = ""
    return {
        "start_row": candidate.hidden_start_row,
        "end_row": candidate.hidden_end_row,
        "kind": candidate.rule.region_kind,
        "label": label,
    }


def _collect_top_level_hints(
    root_node: Node,
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
    rows: list[DiffEngineRow],
    existing_hints: list[FoldHint],
) -> list[FoldHint]:
    """Build grouped top-level folds for unchanged imports/declarations.

    Query-driven folds usually describe bodies.  This second pass covers runs
    of unchanged top-level items such as imports or adjacent declarations, but
    avoids duplicating an existing single-item structural fold.

    # Parameters

    - `root_node`: Parsed source root whose direct items may group.
    - `source_bytes`: Exact UTF-8 source used for node spans and labels.
    - `right_line_to_row`: Source-line to rendered-row mapping.
    - `rows`: Rendered rows used to split runs at changes.
    - `existing_hints`: Structural hints whose exact ranges must not duplicate.
    """

    items = _collect_top_level_items(
        root_node,
        source_bytes,
        right_line_to_row,
    )
    if items == []:
        return []

    existing_ranges = {
        (int(hint["start_row"]), int(hint["end_row"]))
        for hint in existing_hints
    }
    hints: list[FoldHint] = []
    run: list[TopLevelItem] = []

    for item in items:
        if not _rows_are_unchanged(rows[item.start_row : item.end_row]):
            _append_top_level_run_hint(run, rows, existing_ranges, hints)
            run = []
            continue
        if run != []:
            intervening_rows = rows[run[-1].end_row : item.start_row]
            if any(engine_row_has_change(row) for row in intervening_rows):
                _append_top_level_run_hint(run, rows, existing_ranges, hints)
                run = []
        if run != [] and run[-1].label_kind != item.label_kind:
            _append_top_level_run_hint(run, rows, existing_ranges, hints)
            run = []
        run.append(item)

    _append_top_level_run_hint(run, rows, existing_ranges, hints)
    return hints


def _collect_top_level_items(
    root_node: Node,
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
) -> list[TopLevelItem]:
    """Return top-level source items projected into rendered row spans.

    The result is intentionally limited to nodes that can be mapped to actual
    right-side diff rows.  If a source item is absent from the rendered right
    side, it cannot participate in frontend folding.

    # Parameters

    - `root_node`: Parsed language root whose children are considered.
    - `source_bytes`: Exact UTF-8 source used to normalize node line spans.
    - `right_line_to_row`: Source-line to rendered-row mapping.
    """

    if root_node.type == "document":
        return _collect_json_top_level_items(
            root_node,
            source_bytes,
            right_line_to_row,
        )

    items: list[TopLevelItem] = []
    for child in root_node.children:
        classified = _classify_top_level_node(child)
        if classified is None:
            continue
        node, label_kind = classified
        start_line, end_line = _node_line_span(node, source_bytes)
        row_span = _lines_to_row_span(
            right_line_to_row,
            start_line,
            end_line,
        )
        if row_span is None:
            continue
        items.append(
            TopLevelItem(
                start_row=row_span[0],
                end_row=row_span[1],
                start_byte=node.start_byte,
                end_byte=node.end_byte,
                label_kind=label_kind,
            )
        )
    return items


def _collect_json_top_level_items(
    root_node: Node,
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
) -> list[TopLevelItem]:
    """Return top-level JSON object/array members as grouped fold items.

    JSON parser roots wrap the actual document in a `document` node, so this
    helper unwraps exactly one top-level container and treats its named children
    as foldable top-level items.

    # Parameters

    - `root_node`: JSON `document` node expected to wrap one container.
    - `source_bytes`: Exact UTF-8 source used for member line spans.
    - `right_line_to_row`: Source-line to rendered-row mapping.
    """

    containers = [
        child
        for child in root_node.children
        if child.is_named and child.type in {"object", "array"}
    ]
    if len(containers) != 1:
        return []

    top_container = containers[0]
    items: list[TopLevelItem] = []
    for child in top_container.children:
        if not child.is_named:
            continue
        label_kind = _json_top_level_label_kind(child)
        if label_kind is None:
            continue
        start_line, end_line = _node_line_span(child, source_bytes)
        row_span = _lines_to_row_span(
            right_line_to_row,
            start_line,
            end_line,
        )
        if row_span is None:
            continue
        items.append(
            TopLevelItem(
                start_row=row_span[0],
                end_row=row_span[1],
                start_byte=child.start_byte,
                end_byte=child.end_byte,
                label_kind=label_kind,
            )
        )
    return items


def _json_top_level_label_kind(node: Node) -> str | None:
    """Classify one JSON child node for top-level fold labels.

    Object pairs get a `property` label, while array values and primitive
    top-level members are grouped as generic `item` entries.

    # Returns

    - `str`: `property` for an object pair or `item` for a supported array value.
    - `None`: The node is not a JSON top-level item this grouping pass knows.
      The caller must skip it.
    """

    if node.type == "pair":
        return "property"
    if node.type in {
        "object",
        "array",
        "string",
        "number",
        "true",
        "false",
        "null",
    }:
        return "item"
    return None


def _classify_top_level_node(node: Node) -> tuple[Node, str] | None:
    """Classify one root child for top-level grouping.

    Most languages can use the node type directly.  JavaScript and TypeScript
    exports wrap declarations in `export_statement`, so those wrappers are
    kept as the top-level declaration span.

    # Returns

    - `First`: The node span to group; export wrappers retain the wrapper node.
    - `Second`: Its closed top-level fold label category.
    - `None`: The node type is outside `TOP_LEVEL_NODE_KINDS`. The caller must
      leave it out of grouped top-level folds.
    """

    if node.type == "export_statement":
        return node, "declaration"
    label_kind = TOP_LEVEL_NODE_KINDS.get(node.type)
    if label_kind is None:
        return None
    return node, label_kind


def _append_top_level_run_hint(
    run: list[TopLevelItem],
    rows: list[DiffEngineRow],
    existing_ranges: set[tuple[int, int]],
    hints: list[FoldHint],
) -> None:
    """Append a grouped top-level hint when the accumulated run is foldable.

    A run must be non-empty, unchanged, and not merely duplicate an existing
    single-item structural fold.  The resulting hint hides the whole run and
    labels it by the item categories it contains.

    # Parameters

    - `run`: Consecutive same-category top-level items accumulated so far.
    - `rows`: Rendered rows used for the final unchanged check.
    - `existing_ranges`: Structural hint ranges already emitted.
    - `hints`: Output list receiving the grouped hint when eligible.
    """

    if run == []:
        return
    start_row = run[0].start_row
    end_row = run[-1].end_row
    if end_row - start_row < 1:
        return
    if len(run) == 1 and (start_row, end_row) in existing_ranges:
        return
    if not _rows_are_unchanged(rows[start_row:end_row]):
        return
    hints.append(
        {
            "start_row": start_row,
            "end_row": end_row,
            "kind": "top_level",
            "label": _top_level_run_label(run),
        }
    )


def _top_level_run_label(run: list[TopLevelItem]) -> str:
    """Build the visible label for one grouped top-level fold.

    Homogeneous runs get specific labels such as unchanged imports.  Mixed runs
    fall back to a broader top-level declaration label so the frontend still has
    readable text for the folded region.
    """

    if all(item.label_kind == "import" for item in run):
        return _plural_label(len(run), "unchanged import")
    if len({item.label_kind for item in run}) == 1:
        label_kind = run[0].label_kind
        return _plural_label(len(run), f"unchanged {label_kind}")
    return _plural_label(len(run), "unchanged top-level declaration")


def _plural_label(count: int, noun: str) -> str:
    """Pluralize the small set of fold labels used by grouped hints.

    This is intentionally not a general inflector.  It only covers labels that
    this module constructs, including the `property` to `properties` case.

    # Parameters

    - `count`: Number rendered before the label noun.
    - `noun`: Singular phrase from this module's closed label vocabulary.
    """

    if count == 1:
        return f"{count} {noun}"
    if noun.endswith("property"):
        return f"{count} {noun.removesuffix('property')}properties"
    return f"{count} {noun}s"


def _fold_hint_sort_key(hint: FoldHint) -> tuple[int, int]:
    """Sort hints by rendered row range for stable API output.

    Stable order keeps API responses and golden snapshots deterministic even
    when hints came from multiple collection passes.

    # Returns

    - `First`: The inclusive start row, ordering earlier regions first.
    - `Second`: The exclusive end row, ordering equal starts by their
      extent for stable fold-hint output.
    """

    return int(hint["start_row"]), int(hint["end_row"])


def _child_candidates(
    parent: FoldCandidate,
    all_candidates: list[FoldCandidate],
) -> list[FoldCandidate]:
    """Return direct child candidates in source order.

    Candidate parent links are assigned once after collection.  This helper
    reads those links and orders children by byte span before recursive policy
    processing.

    # Parameters

    - `parent`: Candidate whose directly assigned children are wanted.
    - `all_candidates`: Complete parent-linked candidate collection.
    """

    return sorted(
        (
            candidate
            for candidate in all_candidates
            if candidate.parent is parent
        ),
        key=lambda candidate: (
            candidate.context_start_byte,
            candidate.context_end_byte,
        ),
    )


def _region_is_unchanged(
    candidate: FoldCandidate,
    rows: list[DiffEngineRow],
) -> bool:
    """Return whether a candidate's rendered context has no engine changes.

    Structural folds hide only regions whose complete row span is unchanged
    under the canonical engine-row classification.

    # Parameters

    - `candidate`: Region whose complete context span is inspected.
    - `rows`: Complete rendered rows indexed by the candidate coordinates.
    """

    span = rows[candidate.context_start_row : candidate.context_end_row]
    return _rows_are_unchanged(span)


def _rows_are_unchanged(rows: list[DiffEngineRow]) -> bool:
    """Return whether every row in a non-empty span is engine-unchanged.

    Empty spans are not foldable. Non-empty spans use the exact classification
    that hunk assignment and engine summaries consume.
    """

    return bool(rows) and all(not engine_row_has_change(row) for row in rows)


def _has_ancestor_kind(
    candidate: FoldCandidate,
    region_kind: RegionKind,
) -> bool:
    """Return whether a candidate has an ancestor with `region_kind`.

    Container fold policy uses ancestry to avoid creating generic container
    folds inside function/class bodies where function/class folding is clearer.

    # Parameters

    - `candidate`: Candidate from which to walk toward the root.
    - `region_kind`: Ancestor category being queried.
    """

    parent = candidate.parent
    while parent is not None:
        if parent.rule.region_kind == region_kind:
            return True
        parent = parent.parent
    return False


def _contains(
    outer: FoldCandidate,
    inner: FoldCandidate,
) -> bool:
    """Return whether `outer` strictly contains `inner` in source bytes.

    Strict containment requires one edge to be different.  Equal spans are
    treated as duplicates, not as parent/child relationships.

    # Parameters

    - `outer`: Candidate proposed as the containing parent.
    - `inner`: Candidate proposed as its strict descendant.
    """

    return (
        outer.context_start_byte <= inner.context_start_byte
        and outer.context_end_byte >= inner.context_end_byte
        and (
            outer.context_start_byte < inner.context_start_byte
            or outer.context_end_byte > inner.context_end_byte
        )
    )


def _node_line_span(node: Node, source_bytes: bytes) -> tuple[int, int]:
    """Return a one-based inclusive line span for a syntax node.

    Tree-sitter points are zero-based and end-exclusive.  When a node ends at
    the start of the next line because it consumed a trailing newline, this
    helper pulls the end line back to the line that actually contains content.

    # Parameters

    - `node`: Tree-sitter node whose byte and point coordinates refer to source.
    - `source_bytes`: Exact parsed bytes used to recognize a trailing newline.

    # Returns

    - `First`: The node's one-based inclusive starting source line.
    - `Second`: Its one-based inclusive final content line; a trailing newline
      ending at column zero does not add an empty final line.
    """

    start_line = node.start_point.row + 1
    end_line = node.end_point.row + 1
    if (
        node.end_byte > node.start_byte
        and node.end_point.column == 0
        and source_bytes[node.end_byte - 1 : node.end_byte] == b"\n"
    ):
        end_line -= 1
    return start_line, max(start_line, end_line)


def _hidden_line_span(
    node: Node,
    source_bytes: bytes,
    start_mode: StartMode,
) -> tuple[int, int]:
    """Return the one-based source lines hidden by a fold candidate.

    Some regions hide from the captured node's start line, while body-style
    folds keep the opening line visible and hide from the following line.

    # Parameters

    - `node`: Captured fold node.
    - `source_bytes`: Exact parsed bytes used to normalize its line span.
    - `start_mode`: Whether the captured first line stays visible.

    # Returns

    - `First`: The one-based first hidden line, advanced when the opening
      line remains visible.
    - `Second`: The node's one-based inclusive final content line.
    """

    start_line, end_line = _node_line_span(node, source_bytes)
    if start_mode == "next_line":
        start_line += 1
    return start_line, end_line


def _lines_to_row_span(
    right_line_to_row: dict[int, int],
    start_line: int,
    end_line: int,
) -> tuple[int, int] | None:
    """Map a one-based inclusive source line span to rendered row indexes.

    Fold hints are expressed in rendered row indexes, not raw source lines.  If
    either edge cannot be mapped to a right-side row, the source region is not
    representable in the current diff view.

    # Parameters

    - `right_line_to_row`: One-based source-line to rendered-row mapping.
    - `start_line`: Inclusive first source line of the region.
    - `end_line`: Inclusive final source line of the region.

    # Returns

    - `First`: The inclusive rendered start row corresponding to `start_line`.
    - `Second`: The exclusive rendered end row after `end_line`.
    - `None`: At least one source-line edge has no right-side rendered row. The
      caller must omit that unrepresentable fold region.
    """

    start_row = right_line_to_row.get(start_line)
    end_row = right_line_to_row.get(end_line)
    if start_row is None or end_row is None:
        return None
    return start_row, end_row + 1


def _node_text(node: Node, source_bytes: bytes) -> str:
    """Decode the source text covered by `node`.

    Query labels are best-effort display text.  Decode errors are ignored so a
    malformed byte sequence cannot break rendering of the whole diff.

    # Parameters

    - `node`: Syntax node whose byte range supplies label text.
    - `source_bytes`: Exact parsed bytes containing that range.
    """

    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="ignore"
    )


def _markdown_heading_level(node: Node) -> int:
    """Return a Markdown heading level from an atx or setext heading node.

    Markdown section folding needs heading levels to find the next sibling or
    parent section.  Unknown heading node shapes default to level six, which is
    conservative for containment.
    """

    if node.type == "atx_heading":
        for child in node.children:
            if child.type.startswith("atx_h") and child.type.endswith(
                "_marker"
            ):
                return int(
                    child.type.removeprefix("atx_h").removesuffix("_marker")
                )
        return 6
    if node.type == "setext_heading":
        for child in node.children:
            if child.type == "setext_h1_underline":
                return 1
            if child.type == "setext_h2_underline":
                return 2
        return 2
    return 6


def _spec_for_path(path: str) -> FoldLanguageSpec | None:
    """Choose the fold language spec for a source path.

    Exact filename matches win before suffix matches so files such as
    `pyproject.toml` can opt into a language even when suffix handling is
    otherwise broad.

    # Returns

    - `FoldLanguageSpec`: The first exact-filename or suffix match from
      `FOLD_LANGUAGE_SPECS`.
    - `None`: No fold grammar claims the path. The caller must skip structural
      folding for this File.
    """

    normalized = path.casefold()
    basename = normalized.rsplit("/", 1)[-1]
    for spec in FOLD_LANGUAGE_SPECS:
        if basename in spec.filenames:
            return spec
        if any(normalized.endswith(suffix) for suffix in spec.suffixes):
            return spec
    return None


@cache
def _load_language_query(
    module_name: str,
    language_attr: str,
    query_path: str,
) -> tuple[Language, Query]:
    """Load and cache the tree-sitter language plus dirdiff fold query.

    Parser setup is relatively expensive and independent of the file contents.
    Caching by module, language attribute, and query path keeps repeated diff
    rendering from reloading the same grammar and query resources.

    # Parameters

    - `module_name`: Python package exporting the Tree-sitter grammar.
    - `language_attr`: Grammar factory attribute within that package.
    - `query_path`: Package-relative dirdiff fold query path.

    # Returns

    - `First`: The constructed Tree-sitter grammar.
    - `Second`: The dirdiff fold query compiled against that same grammar.
    """

    module = importlib.import_module(module_name)
    language_factory = getattr(module, language_attr)
    language = Language(language_factory())
    query = Query(
        language,
        files("dirdiff").joinpath(query_path).read_text(encoding="utf-8"),
    )
    return language, query
