"""Tree-sitter fold hint discovery for rendered diff rows.

Fold hints are display metadata.  They describe source regions the frontend can
collapse after an engine has already aligned the old and new text.  This module
therefore lives under `dirdiff.rendering`: it uses rendered row numbers,
right-side source text, and parser metadata to enrich an existing display
payload, but it never chooses an engine and never changes diff semantics.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any, Literal, TypedDict

from tree_sitter import Language, Node, Parser, Query, QueryCursor

from dirdiff.engines import engine_row_has_change

__all__ = ["FoldHint", "fold_hints_for_path"]


class FoldHint(TypedDict):
    """Foldable source region discovered while rendering a file diff.

    Fold hints are optional metadata for the frontend. They do not change row
    alignment.
    """

    start_row: int
    """
    First rendered row covered by the fold hint.
    """

    end_row: int
    """
    One-past-the-last rendered row covered by the fold hint.
    """

    kind: Literal[
        "function_like",
        "class_like",
        "container",
        "section",
        "top_level",
    ]
    """
    Source-region category used by the frontend folding policy.
    """

    label: str
    """
    Human-readable fold label derived from the source region.
    """


RegionKind = Literal[
    "function_like",
    "class_like",
    "container",
    "section",
    "top_level",
]
StartMode = Literal["node_start", "next_line"]


@dataclass(frozen=True)
class FoldRule:
    """One query-pattern policy for turning syntax nodes into fold candidates.

    The query file decides which syntax node was captured.  The rule explains
    how that capture should be interpreted in rendered-row space: what kind of
    region it represents, where the hidden body starts, and how many rendered
    rows must be hidden before exposing a fold hint is worthwhile.
    """

    region_kind: RegionKind
    """
    Frontend-facing category for the fold hint produced by this rule.
    """

    start_mode: StartMode
    """
    Whether the hidden span starts on the captured node's first line or body.
    """

    min_hidden_rows: int
    """
    Minimum number of rendered rows that must be hidden to emit a hint.
    """


@dataclass(frozen=True)
class FoldLanguageSpec:
    """Tree-sitter language, query, and rule set for one file family.

    Fold detection is selected by path, then executed by loading the matching
    tree-sitter language and dirdiff-owned fold query.  The query pattern index
    is paired with `rules` to decide how each capture becomes a candidate.
    """

    module_name: str
    """
    Python module that exposes the tree-sitter language factory.
    """

    query_path: str
    """
    Package-resource path to the fold query under `dirdiff`.
    """

    suffixes: tuple[str, ...]
    """
    File suffixes that select this fold language.
    """

    rules: tuple[FoldRule, ...]
    """
    Rules indexed by tree-sitter query pattern number.
    """

    filenames: tuple[str, ...] = ()
    """
    Exact lower-case filenames that select this fold language.
    """

    language_attr: str = "language"
    """
    Attribute name for the language factory inside `module_name`.
    """


@dataclass
class FoldCandidate:
    """Intermediate foldable region before display policy accepts it.

    Candidates still know their syntax nodes, source byte spans, rendered row
    spans, rule, optional label, and parent relationship.  Later filtering uses
    this richer representation to avoid folding changed regions or hiding an
    outer region when an inner region is the better unchanged target.
    """

    rule: FoldRule
    """
    Rule that interpreted the query capture.
    """

    fold_node: Node
    """
    Syntax node whose lines form the foldable hidden range.
    """

    context_node: Node
    """
    Surrounding syntax node used to decide whether the whole region changed.
    """

    label_text: str | None
    """
    Optional label text captured by the tree-sitter query.
    """

    context_start_row: int
    """
    First rendered row in the full context region.
    """

    context_end_row: int
    """
    One-past-the-last rendered row in the full context region.
    """

    hidden_start_row: int
    """
    First rendered row that the frontend may hide.
    """

    hidden_end_row: int
    """
    One-past-the-last rendered row that the frontend may hide.
    """

    context_start_byte: int
    """
    Source byte offset for ordering and containment checks.
    """

    context_end_byte: int
    """
    Source byte offset for ordering and containment checks.
    """

    parent: FoldCandidate | None = None
    """
    Smallest containing candidate, assigned after collection.
    """


@dataclass(frozen=True)
class TopLevelItem:
    """Top-level source item used for grouped unchanged folds.

    Structural queries usually find foldable bodies.  Top-level items support a
    separate display optimization for unchanged runs of imports, declarations,
    or JSON members that do not have a body-style fold of their own.
    """

    start_row: int
    """
    First rendered row covered by the top-level item.
    """

    end_row: int
    """
    One-past-the-last rendered row covered by the top-level item.
    """

    start_byte: int
    """
    Source byte offset used for stable ordering.
    """

    end_byte: int
    """
    Source byte offset used for stable ordering.
    """

    label_kind: str
    """
    Category used to build grouped fold labels.
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


def fold_hints_for_path(
    path: str | None,
    text: str,
    rows: list[dict[str, Any]],
) -> list[FoldHint]:
    """Return fold hints for the right side of an already-rendered diff.

    Rendering owns this because fold hints depend on both source structure and
    displayed row status.  The parser sees `text` and `path` only to find
    foldable source regions; the final hints are accepted only when those
    regions map cleanly to `rows` and remain unchanged in the rendered diff.
    Unsupported languages, missing tree-sitter packages, parse/query failures,
    and paths without right-side rows all produce an empty list.
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
        if isinstance(row.get("right_no"), int)
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
    rows: list[dict[str, Any]],
) -> list[FoldHint]:
    """Collect hierarchical Markdown section folds from heading captures.

    Markdown folds are based on heading ranges rather than ordinary code-block
    containers.  The section body extends until the next heading at the same or
    higher level, and trailing blank rows are ignored when deciding whether a
    changed section can be folded.
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

    candidates: list[FoldCandidate] = []
    rule = spec.rules[0]
    for index, (heading, _level, label_text) in enumerate(headings):
        context_start_line, _ = _node_line_span(heading, source_bytes)
        _, heading_end_line = _node_line_span(heading, source_bytes)
        context_end_line = max(right_line_to_row)
        for next_heading, next_level, _next_label in headings[index + 1 :]:
            if next_level <= _markdown_heading_level(heading):
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
                context_end_byte=_line_end_byte_for_line(
                    source_bytes, context_end_line
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
    """

    for candidate in candidates:
        parent: FoldCandidate | None = None
        for other in candidates:
            if candidate is other:
                continue
            if not _contains(other, candidate):
                continue
            if parent is None or _contains(parent, other):
                parent = other
        candidate.parent = parent


def _collect_hints(
    candidate: FoldCandidate,
    all_candidates: list[FoldCandidate],
    rows: list[dict[str, Any]],
    hints: list[FoldHint],
) -> None:
    """Apply fold policy recursively and append accepted display hints.

    The policy prefers folding unchanged outer class/function regions.  When a
    function or section changed, unchanged child regions may still be useful, so
    recursion continues into the relevant child candidates.  Containers are
    intentionally limited outside function/class ancestors to avoid noisy nested
    folds inside code bodies.
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
    rows: list[dict[str, Any]],
) -> FoldHint | None:
    """Convert an accepted candidate into the compact API fold-hint shape.

    This is the last policy gate for structural folds.  Candidates that would
    hide too little rendered content are discarded, and labels prefer the first
    visible line immediately before the hidden body unless Markdown section
    captures supplied a better heading label.
    """

    hidden_rows = candidate.hidden_end_row - candidate.hidden_start_row
    if hidden_rows < candidate.rule.min_hidden_rows:
        return None
    visible_index = candidate.hidden_start_row - 1
    visible_label = ""
    if 0 <= visible_index < len(rows):
        raw_visible_label = rows[visible_index].get("right_text")
        visible_label = (
            "" if raw_visible_label is None else str(raw_visible_label).strip()
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
    rows: list[dict[str, Any]],
    existing_hints: list[FoldHint],
) -> list[FoldHint]:
    """Build grouped top-level folds for unchanged imports/declarations.

    Query-driven folds usually describe bodies.  This second pass covers runs
    of unchanged top-level items such as imports or adjacent declarations, but
    avoids duplicating an existing single-item structural fold.
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
    """

    if node.type == "export_statement":
        return node, "declaration"
    label_kind = TOP_LEVEL_NODE_KINDS.get(node.type)
    if label_kind is None:
        return None
    return node, label_kind


def _append_top_level_run_hint(
    run: list[TopLevelItem],
    rows: list[dict[str, Any]],
    existing_ranges: set[tuple[int, int]],
    hints: list[FoldHint],
) -> None:
    """Append a grouped top-level hint when the accumulated run is foldable.

    A run must be non-empty, unchanged, and not merely duplicate an existing
    single-item structural fold.  The resulting hint hides the whole run and
    labels it by the item categories it contains.
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
    readable text for the collapsed region.
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
    rows: list[dict[str, Any]],
) -> bool:
    """Return whether a candidate's rendered context has no engine changes.

    Structural folds hide only regions whose complete row span is unchanged
    under the canonical engine-row classification.
    """

    span = rows[candidate.context_start_row : candidate.context_end_row]
    return _rows_are_unchanged(span)


def _rows_are_unchanged(rows: list[dict[str, Any]]) -> bool:
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


def _line_end_byte_for_line(source_bytes: bytes, line_number: int) -> int:
    """Return the byte offset just after a one-based source line.

    Markdown section candidates use this to extend a heading's context to the
    line before the next sibling heading.  Missing or out-of-range lines clamp
    to the end of the source bytes.
    """

    current_line = 1
    index = 0
    start = 0
    while index < len(source_bytes):
        if current_line == line_number:
            start = index
            break
        if source_bytes[index : index + 1] == b"\n":
            current_line += 1
        index += 1
    else:
        return len(source_bytes)

    end = start
    while end < len(source_bytes) and source_bytes[end : end + 1] != b"\n":
        end += 1
    if end < len(source_bytes):
        end += 1
    return end


def _spec_for_path(path: str) -> FoldLanguageSpec | None:
    """Choose the fold language spec for a source path.

    Exact filename matches win before suffix matches so files such as
    `pyproject.toml` can opt into a language even when suffix handling is
    otherwise broad.
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
    """

    module = importlib.import_module(module_name)
    language_factory = getattr(module, language_attr)
    language = Language(language_factory())
    query = Query(
        language,
        files("dirdiff").joinpath(query_path).read_text(encoding="utf-8"),
    )
    return language, query
