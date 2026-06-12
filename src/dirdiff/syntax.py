from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from functools import lru_cache
import importlib
from importlib.resources import files
import re

from tree_sitter import Language, Parser, Query, QueryCursor


@dataclass(frozen=True)
class SyntaxLanguageSpec:
    module_name: str
    query_path: str
    suffixes: tuple[str, ...]
    filenames: tuple[str, ...] = ()
    language_attr: str = "language"
    query_package: str | None = None


@dataclass(frozen=True)
class SyntaxSpan:
    start: int
    end: int
    classes: tuple[str, ...]


LANGUAGE_SPECS: tuple[SyntaxLanguageSpec, ...] = (
    SyntaxLanguageSpec(
        module_name="tree_sitter_python",
        query_path="queries/highlights.scm",
        suffixes=(".py", ".pyi", ".pyw"),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_javascript",
        query_path="queries/highlights/javascript.scm",
        suffixes=(".js", ".mjs", ".cjs"),
        query_package="dirdiff",
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_javascript",
        query_path="queries/highlights/jsx.scm",
        suffixes=(".jsx",),
        query_package="dirdiff",
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/highlights/typescript.scm",
        suffixes=(".ts", ".mts", ".cts"),
        language_attr="language_typescript",
        query_package="dirdiff",
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_typescript",
        query_path="queries/highlights/tsx.scm",
        suffixes=(".tsx",),
        language_attr="language_tsx",
        query_package="dirdiff",
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_rust",
        query_path="queries/highlights.scm",
        suffixes=(".rs",),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_bash",
        query_path="queries/highlights.scm",
        suffixes=(".sh", ".bash", ".zsh"),
        filenames=(".bashrc", ".zshrc"),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_css",
        query_path="queries/highlights.scm",
        suffixes=(".css",),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_html",
        query_path="queries/highlights.scm",
        suffixes=(".html", ".htm", ".xhtml"),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_json",
        query_path="queries/highlights.scm",
        suffixes=(".json",),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_markdown",
        query_path="queries/markdown/highlights.scm",
        suffixes=(".md", ".markdown"),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_toml",
        query_path="queries/highlights.scm",
        suffixes=(".toml",),
        filenames=("pyproject.toml",),
    ),
    SyntaxLanguageSpec(
        module_name="tree_sitter_yaml",
        query_path="queries/highlights.scm",
        suffixes=(".yaml", ".yml"),
    ),
)


def highlight_lines_for_path(
    path: str | None,
    text: str | None,
) -> list[list[dict[str, object]]] | None:
    if not path or not text:
        return None

    spec = _spec_for_path(path)
    if spec is None:
        return None

    return _highlight_lines_with_spec(spec, text)


def _spec_for_path(path: str) -> SyntaxLanguageSpec | None:
    normalized = path.casefold()
    basename = normalized.rsplit("/", 1)[-1]
    for spec in LANGUAGE_SPECS:
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
    query_package: str | None,
) -> tuple[Language, Query]:
    module = importlib.import_module(module_name)
    language_factory = getattr(module, language_attr)
    language = Language(language_factory())
    query_text = _load_query_text(query_package or module_name, query_path)
    query = Query(language, query_text)
    return language, query


@lru_cache(maxsize=None)
def _load_query_text(package_name: str, query_path: str) -> str:
    query_file = files(package_name).joinpath(query_path)
    query_text = query_file.read_text(encoding="utf-8")
    inherited_query_texts = [
        _load_query_text(package_name, _sibling_query_path(query_path, inherited_name))
        for inherited_name in _inherited_query_names(query_text)
    ]
    return "\n".join([*inherited_query_texts, query_text])


def _inherited_query_names(query_text: str) -> list[str]:
    inherited_names: list[str] = []
    for line in query_text.splitlines():
        match = re.match(r"\s*;\s*inherits:\s*(.+)$", line)
        if not match:
            continue
        inherited_names.extend(
            name.strip() for name in match.group(1).split(",") if name.strip()
        )
    return inherited_names


def _sibling_query_path(query_path: str, query_name: str) -> str:
    parent = query_path.rsplit("/", 1)[0]
    return f"{parent}/{query_name}.scm"


def _highlight_lines_with_spec(
    spec: SyntaxLanguageSpec,
    text: str,
) -> list[list[dict[str, object]]] | None:
    try:
        language, query = _load_language_query(
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

    if not captures:
        return [[] for _ in text.splitlines()]

    byte_boundaries = [0]
    for character in text:
        byte_boundaries.append(byte_boundaries[-1] + len(character.encode("utf-8")))

    line_texts = text.splitlines()
    line_starts = [0]
    for index, character in enumerate(text):
        if character == "\n":
            line_starts.append(index + 1)

    line_intervals: list[list[tuple[int, int, tuple[str, ...]]]] = [
        [] for _ in line_texts
    ]
    for order, (capture_name, start_byte, end_byte) in enumerate(captures):
        start_char = bisect_right(byte_boundaries, start_byte) - 1
        end_char = bisect_right(byte_boundaries, end_byte) - 1
        if start_char >= end_char:
            continue

        classes = _classes_for_capture(capture_name)
        first_line = bisect_right(line_starts, start_char) - 1
        last_line = bisect_right(line_starts, max(start_char, end_char - 1)) - 1

        for line_index in range(first_line, min(last_line + 1, len(line_texts))):
            line_start = line_starts[line_index]
            line_end = line_start + len(line_texts[line_index])
            local_start = max(start_char, line_start) - line_start
            local_end = min(end_char, line_end) - line_start
            if local_start >= local_end:
                continue
            line_intervals[line_index].append((local_start, local_end, classes, order))

    return [
        [
            {"start": span.start, "end": span.end, "classes": list(span.classes)}
            for span in _collapse_line_intervals(line, intervals)
        ]
        for line, intervals in zip(line_texts, line_intervals)
    ]


def _classes_for_capture(capture_name: str) -> tuple[str, ...]:
    parts = [part for part in capture_name.split(".") if part]
    classes = []
    for index in range(1, len(parts) + 1):
        classes.append(f"ts-{'-'.join(parts[:index])}")
    return tuple(classes)


def _collapse_line_intervals(
    line_text: str,
    intervals: list[tuple[int, int, tuple[str, ...], int]],
) -> list[SyntaxSpan]:
    if not line_text or not intervals:
        return []

    events: list[tuple[int, int, int]] = []
    for index, (start, end, _classes, _order) in enumerate(intervals):
        events.append((start, 1, index))
        events.append((end, 0, index))
    events.sort(key=lambda item: (item[0], item[1]))

    active: dict[int, tuple[int, int, tuple[str, ...], int]] = {}
    position = 0
    spans: list[SyntaxSpan] = []

    for event_position, event_kind, interval_index in events:
        if event_position > position and active:
            chosen = min(
                active.values(),
                key=lambda item: (
                    item[1] - item[0],
                    -len(item[2]),
                    -item[3],
                ),
            )
            _append_span(spans, position, event_position, chosen[2])

        interval = intervals[interval_index]
        if event_kind == 0:
            active.pop(interval_index, None)
        else:
            active[interval_index] = interval
        position = event_position

    if position < len(line_text) and active:
        chosen = min(
            active.values(),
            key=lambda item: (
                item[1] - item[0],
                -len(item[2]),
                -item[3],
            ),
        )
        _append_span(spans, position, len(line_text), chosen[2])

    return spans


def _append_span(
    spans: list[SyntaxSpan],
    start: int,
    end: int,
    classes: tuple[str, ...],
) -> None:
    if start >= end:
        return
    if spans and spans[-1].end == start and spans[-1].classes == classes:
        previous = spans[-1]
        spans[-1] = SyntaxSpan(previous.start, end, previous.classes)
        return
    spans.append(SyntaxSpan(start, end, classes))
