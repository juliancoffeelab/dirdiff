"""Behavior tests for GumTree-backed structural diff rendering.

This module uses the checked-in GumTree presets to verify action matching,
token mapping, and rendered payload shape.  It may call GumTree engine internals
only where the public payload cannot expose the tree-action detail under test;
ordinary UI payload behavior goes through composition, the way the file-diff
endpoint produces it.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dirdiff.engines.gumtree import (
    GumTreeDiffEngine,
    GumTreeJson,
)
from dirdiff.engines.gumtree.logic import (
    _line_segments,
    _range_from_tree,
)
from dirdiff.formats import ComposeContext, ComposedFilePayload, Composer

__all__: list[str] = []

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"
FIXTURE_ROOT = PRESETS_ROOT / "python" / "extract-python-helper-function"
LEFT_PATH = "python/extract-python-helper-function/old.py"
RIGHT_PATH = "python/extract-python-helper-function/new.py"


@dataclass(frozen=True)
class ExpectedTokenStatus:
    side: str
    status: str
    line_no: int
    start: int
    end: int
    text: str
    tree: str


@dataclass(frozen=True)
class ActualTokenStatus:
    start: int
    end: int
    text: str


def _extract_helper_engine() -> GumTreeDiffEngine:
    return GumTreeDiffEngine()


def _extract_helper_payload() -> ComposedFilePayload:
    return Composer().compose(
        (FIXTURE_ROOT / "old.py").read_bytes(),
        (FIXTURE_ROOT / "new.py").read_bytes(),
        ComposeContext.build(
            left_path=LEFT_PATH,
            right_path=RIGHT_PATH,
            left_label="python",
            right_label="new",
            renderer=_extract_helper_engine(),
        ),
    )


def _source_text(side: str) -> str:
    file_name = "old.py" if side == "left" else "new.py"
    return (FIXTURE_ROOT / file_name).read_text()


def _expected_for_tree(
    *,
    side: str,
    status: str,
    tree: str,
) -> list[ExpectedTokenStatus]:
    text = _source_text(side)
    source_range = _range_from_tree(tree)
    expected: list[ExpectedTokenStatus] = []
    for segment in _line_segments(text):
        if source_range.end <= segment.start:
            break
        if source_range.start >= segment.segment_end:
            continue

        overlap_start = max(source_range.start, segment.start)
        overlap_end = min(source_range.end, segment.content_end)
        if overlap_start >= overlap_end:
            continue

        start = overlap_start - segment.start
        end = overlap_end - segment.start
        expected.append(
            ExpectedTokenStatus(
                side=side,
                status=status,
                line_no=segment.index + 1,
                start=start,
                end=end,
                text=text[overlap_start:overlap_end],
                tree=tree,
            )
        )
    return expected


def _expected_action_statuses(
    diff_json: GumTreeJson,
) -> list[ExpectedTokenStatus]:
    def _action_tree(diff_json: GumTreeJson, action: str, tree: str) -> str:
        """Return the exact GumTree action tree required by this fixture."""
        actions = diff_json.get("actions", [])
        for candidate in actions:
            if candidate["action"] == action and candidate["tree"] == tree:
                return candidate["tree"]
        raise AssertionError(f"GumTree action is missing: {action} {tree}")

    def _matched_dest_tree(diff_json: GumTreeJson, src_tree: str) -> str:
        """Return the destination tree required by this fixture's match."""
        matches = diff_json.get("matches", [])
        for match in matches:
            if match["src"] == src_tree:
                return match["dest"]
        raise AssertionError(
            f"GumTree destination match is missing: {src_tree}"
        )

    expectations: list[ExpectedTokenStatus] = []

    insert_trees = [
        "expression_statement [55,96]",
        "type [297,320]",
        "return_statement [632,655]",
    ]
    for tree in insert_trees:
        action_tree = _action_tree(diff_json, "insert-tree", tree)
        expectations.extend(
            _expected_for_tree(
                side="right",
                status="insert",
                tree=action_tree,
            )
        )

    update_tree = _action_tree(
        diff_json,
        "update-node",
        "identifier: render_order [4,16]",
    )
    expectations.extend(
        _expected_for_tree(side="left", status="replace", tree=update_tree)
    )
    expectations.extend(
        _expected_for_tree(
            side="right",
            status="replace",
            tree=_matched_dest_tree(diff_json, update_tree),
        )
    )

    move_trees = [
        "type [46,49]",
        "for_statement [388,477]",
        "return_statement [482,505]",
    ]
    for tree in move_trees:
        action_tree = _action_tree(diff_json, "move-tree", tree)
        expectations.extend(
            _expected_for_tree(side="left", status="move", tree=action_tree)
        )
        expectations.extend(
            _expected_for_tree(
                side="right",
                status="move",
                tree=_matched_dest_tree(diff_json, action_tree),
            )
        )

    return expectations


def _row_for_line(
    payload: ComposedFilePayload,
    *,
    side: str,
    line_no: int,
) -> dict[str, Any]:
    line_key = f"{side}_no"
    # A flat Python file composes into exactly one text bay, and its rows are
    # the rows this module asserts against.
    (frame,) = payload["frames"]
    (bay,) = frame["bays"]
    kind_data = bay["kind_data"]
    assert kind_data["kind"] == "text"
    rows: list[Any] = kind_data["rows"]
    row: dict[str, Any]
    for row in rows:
        if row[line_key] == line_no:
            return row
    raise AssertionError(f"Missing {side} row for line {line_no}")


def _actual_status_intervals(
    payload: ComposedFilePayload,
    *,
    side: str,
    status: str,
    line_no: int,
) -> list[ActualTokenStatus]:
    row = _row_for_line(payload, side=side, line_no=line_no)
    cursor = 0
    intervals: list[ActualTokenStatus] = []
    for part in row[f"{side}_parts"]:
        token_text = part["text"]
        start = cursor
        end = cursor + len(token_text)
        if part["diff_status"] == status:
            intervals.append(
                ActualTokenStatus(
                    start=start,
                    end=end,
                    text=token_text,
                )
            )
        cursor = end
    return intervals


def _is_covered(
    *,
    expected: ExpectedTokenStatus,
    actual_intervals: list[ActualTokenStatus],
) -> bool:
    covered_until = expected.start
    for actual in actual_intervals:
        if actual.end <= covered_until:
            continue
        if actual.start > covered_until:
            return False
        covered_until = actual.end
        if covered_until >= expected.end:
            return True
    return False


def test_gumtree_json_action_ranges_are_projected_to_token_statuses() -> None:
    payload = _extract_helper_payload()
    engine = _extract_helper_engine()
    diff_json = engine._run_gumtree_json(
        left_text=_source_text("left"),
        right_text=_source_text("right"),
        left_path_hint=LEFT_PATH,
        right_path_hint=RIGHT_PATH,
    )

    missing: list[str] = []
    for expected in _expected_action_statuses(diff_json):
        actual_intervals = _actual_status_intervals(
            payload,
            side=expected.side,
            status=expected.status,
            line_no=expected.line_no,
        )
        if _is_covered(
            expected=expected,
            actual_intervals=actual_intervals,
        ):
            continue

        actual_texts = [interval.text for interval in actual_intervals]
        missing.append(
            f"{expected.side} line {expected.line_no} "
            f"{expected.status} {expected.text!r} from {expected.tree!r}; "
            f"actual {expected.status} parts: {actual_texts!r}"
        )

    assert missing == []


def test_gumtree_keeps_rows_neutral_and_summarizes_token_ranges() -> None:
    payload = _extract_helper_payload()

    assert _row_for_line(payload, side="right", line_no=2)["status"] == "equal"
    assert _row_for_line(payload, side="right", line_no=6)["status"] == "equal"
    assert _row_for_line(payload, side="left", line_no=13)["status"] == "equal"
    assert _row_for_line(payload, side="right", line_no=4)["status"] == "equal"
    assert _row_for_line(payload, side="left", line_no=1)["status"] == "equal"

    assert payload["summary"]["changed_lines"] == 12
    assert payload["summary"]["modified_lines"] == 5
    assert payload["summary"]["added_lines"] == 2
    assert payload["summary"]["removed_lines"] == 0
    assert payload["summary"]["moved_lines"] == 5
