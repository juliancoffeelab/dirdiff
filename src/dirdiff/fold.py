from __future__ import annotations

import importlib
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

from tree_sitter import Language, Node, Parser, Query, QueryCursor

RegionKind = Literal["function_like", "class_like", "container", "section"]
StartMode = Literal["node_start", "next_line"]
FoldHint = dict[str, int | str]


@dataclass(frozen=True)
class FoldRule:
    region_kind: RegionKind
    start_mode: StartMode
    min_hidden_rows: int


@dataclass(frozen=True)
class FoldLanguageSpec:
    module_name: str
    query_path: str
    suffixes: tuple[str, ...]
    rules: tuple[FoldRule, ...]
    filenames: tuple[str, ...] = ()
    language_attr: str = "language"


@dataclass
class FoldCandidate:
    rule: FoldRule
    fold_node: Node
    context_node: Node
    label_text: str | None
    context_start_row: int
    context_end_row: int
    hidden_start_row: int
    hidden_end_row: int
    context_start_byte: int
    context_end_byte: int
    parent: FoldCandidate | None = None


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
    text: str | None,
    rows: list[dict[str, Any]],
) -> list[FoldHint]:
    if not path or not text or not rows:
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
    if not right_line_to_row:
        return []

    cursor = QueryCursor(query)
    if any(rule.region_kind == "section" for rule in spec.rules):
        return _collect_markdown_section_hints(
            spec,
            cursor.matches(tree.root_node),
            source_bytes,
            right_line_to_row,
            rows,
        )

    candidates = _collect_candidates(
        spec,
        cursor.matches(tree.root_node),
        source_bytes,
        right_line_to_row,
    )
    if not candidates:
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


def _collect_markdown_section_hints(
    spec: FoldLanguageSpec,
    matches: list[tuple[int, dict[str, list[Node]]]],
    source_bytes: bytes,
    right_line_to_row: dict[int, int],
    rows: list[dict[str, Any]],
) -> list[FoldHint]:
    if not spec.rules:
        return []

    headings: list[tuple[Node, int, str]] = []
    for pattern_index, capture_map in matches:
        if pattern_index >= len(spec.rules):
            continue
        label_nodes = capture_map.get("fold.label")
        if not label_nodes:
            continue
        heading = label_nodes[0]
        headings.append(
            (
                heading,
                _markdown_heading_level(heading),
                _markdown_heading_label(heading, source_bytes),
            )
        )

    if not headings:
        return []

    candidates: list[FoldCandidate] = []
    rule = spec.rules[0]
    for index, (heading, _level, label_text) in enumerate(headings):
        context_start_line, _ = _node_line_span(heading, source_bytes)
        heading_start_line, heading_end_line = _node_line_span(
            heading, source_bytes
        )
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

    if not candidates:
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
    deduped: dict[tuple[str, int, int, int, int], FoldCandidate] = {}

    for pattern_index, capture_map in matches:
        if pattern_index >= len(spec.rules):
            continue
        fold_nodes = capture_map.get("fold")
        if not fold_nodes:
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
        candidate = FoldCandidate(
            rule=rule,
            fold_node=fold_node,
            context_node=context_node,
            label_text=label_text or None,
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
            not existing.label_text and candidate.label_text
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
    if candidate.rule.region_kind == "class_like":
        for child in _child_candidates(candidate, all_candidates):
            if child.rule.region_kind in {"function_like", "class_like"}:
                _collect_hints(child, all_candidates, rows, hints)
        return

    if candidate.rule.region_kind == "function_like":
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
        return

    if candidate.rule.region_kind == "container":
        if _has_ancestor_kind(candidate, "function_like"):
            return
        if _has_ancestor_kind(candidate, "class_like"):
            return
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
            return
        for child in _child_candidates(candidate, all_candidates):
            if child.rule.region_kind == "container":
                _collect_hints(child, all_candidates, rows, hints)
        return

    if candidate.rule.region_kind == "section":
        if _region_is_unchanged(candidate, rows):
            hint = _candidate_to_hint(candidate, rows)
            if hint is not None:
                hints.append(hint)
            return
        for child in _child_candidates(candidate, all_candidates):
            if child.rule.region_kind == "section":
                _collect_hints(child, all_candidates, rows, hints)


def _candidate_to_hint(
    candidate: FoldCandidate,
    rows: list[dict[str, Any]],
) -> FoldHint | None:
    hidden_rows = candidate.hidden_end_row - candidate.hidden_start_row
    if hidden_rows < candidate.rule.min_hidden_rows:
        return None
    visible_index = candidate.hidden_start_row - 1
    visible_label = ""
    if 0 <= visible_index < len(rows):
        visible_label = str(rows[visible_index].get("right_text") or "").strip()
    if candidate.rule.region_kind == "section":
        label = candidate.label_text or visible_label or ""
    else:
        label = visible_label or candidate.label_text or ""
    return {
        "start_row": candidate.hidden_start_row,
        "end_row": candidate.hidden_end_row,
        "label": label,
    }


def _fold_hint_sort_key(hint: FoldHint) -> tuple[int, int]:
    return int(hint["start_row"]), int(hint["end_row"])


def _child_candidates(
    parent: FoldCandidate,
    all_candidates: list[FoldCandidate],
) -> list[FoldCandidate]:
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
    span = rows[candidate.context_start_row : candidate.context_end_row]
    if candidate.rule.region_kind == "section":
        span = _trim_markdown_section_trailing_blank_rows(span)
    return bool(span) and all(not _row_has_any_change(row) for row in span)


def _trim_markdown_section_trailing_blank_rows(
    span: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    end = len(span)
    while end > 0:
        row = span[end - 1]
        right_text = str(row.get("right_text") or "")
        left_text = str(row.get("left_text") or "")
        if right_text.strip() or left_text.strip():
            break
        end -= 1
    return span[:end]


def _has_ancestor_kind(
    candidate: FoldCandidate,
    region_kind: RegionKind,
) -> bool:
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
    return (
        outer.context_start_byte <= inner.context_start_byte
        and outer.context_end_byte >= inner.context_end_byte
        and (
            outer.context_start_byte < inner.context_start_byte
            or outer.context_end_byte > inner.context_end_byte
        )
    )


def _node_line_span(node: Node, source_bytes: bytes) -> tuple[int, int]:
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
    start_line, end_line = _node_line_span(node, source_bytes)
    if start_mode == "next_line":
        start_line += 1
    return start_line, end_line


def _lines_to_row_span(
    right_line_to_row: dict[int, int],
    start_line: int,
    end_line: int,
) -> tuple[int, int] | None:
    start_row = right_line_to_row.get(start_line)
    end_row = right_line_to_row.get(end_line)
    if start_row is None or end_row is None:
        return None
    return start_row, end_row + 1


def _node_text(node: Node, source_bytes: bytes) -> str:
    return source_bytes[node.start_byte : node.end_byte].decode(
        "utf-8", errors="ignore"
    )


def _markdown_heading_level(node: Node) -> int:
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


def _markdown_heading_label(node: Node, source_bytes: bytes) -> str:
    text = _node_text(node, source_bytes).splitlines()
    return text[0].strip() if text else ""


def _line_end_byte_for_line(source_bytes: bytes, line_number: int) -> int:
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


def _row_has_any_change(row: dict[str, Any]) -> bool:
    if row.get("status") != "equal":
        return True
    if row.get("left_text") != row.get("right_text"):
        return True
    return any(
        token.get("status") != "unchanged"
        for token in row.get("left_tokens", []) + row.get("right_tokens", [])
    )


def _spec_for_path(path: str) -> FoldLanguageSpec | None:
    normalized = path.casefold()
    basename = normalized.rsplit("/", 1)[-1]
    for spec in FOLD_LANGUAGE_SPECS:
        if basename in spec.filenames:
            return spec
        if any(normalized.endswith(suffix) for suffix in spec.suffixes):
            return spec
    return None


@lru_cache(maxsize=None)
def _load_language_query(
    module_name: str,
    language_attr: str,
    query_path: str,
) -> tuple[Language, Query]:
    module = importlib.import_module(module_name)
    language_factory = getattr(module, language_attr)
    language = Language(language_factory())
    query = Query(
        language,
        files("dirdiff").joinpath(query_path).read_text(encoding="utf-8"),
    )
    return language, query
