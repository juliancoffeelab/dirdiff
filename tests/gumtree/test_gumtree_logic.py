"""Check GumTree action ranges and their rendered token status.

Checked-in presets verify action matching, token mapping, and neutral row shape.
Tests call engine internals only when the public payload cannot expose the tree
fact under test. User-visible payload cases go through ordinary composition.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dirdiff.engines.gumtree import (
    GumTreeDiffEngine,
    GumTreeJson,
)
from dirdiff.engines.gumtree.logic import (
    line_segments,
    range_from_tree,
)
from dirdiff.formats import ComposeContext, ComposedFilePayload, Composer
from dirdiff.rendering import DiffRow

PRESETS_ROOT = Path(__file__).parents[1] / "presets" / "gumtree"
"""GumTree behavior fixture catalog for structural range-mapping tests.

This module selects one readable real change from the catalog rather than
duplicating its source strings in assertions.
"""
FIXTURE_ROOT = PRESETS_ROOT / "python" / "extract-python-helper-function"
"""Real helper-extraction pair used to inspect update, insert, and move ranges.

Its GumTree result contains every action family this module maps to rendered
token intervals.
"""
LEFT_PATH = "python/extract-python-helper-function/old.py"
"""Old parser hint for the helper-extraction fixture.

The `.py` suffix selects GumTree's Python generator; the string is never used to
load source content.
"""
RIGHT_PATH = "python/extract-python-helper-function/new.py"
"""New parser hint for the helper-extraction fixture.

It mirrors `LEFT_PATH` so both temporary inputs use the same Python parser.
"""


@dataclass(frozen=True)
class ExpectedTokenStatus:
    """Describe one expected GumTree token derived from an action tree range.

    Tests expand one GumTree tree string across intersected source lines into
    these values, then compare them with token decorations in composed rows.
    """

    side: Literal["left", "right"]
    """Source document whose absolute GumTree offsets address text.

    The value also selects the composed row and decorated-part fields used for
    comparison; it never changes the expected status.
    """

    status: str
    """Dirdiff inline classification required over the expected source slice.

    The action kind determines it before syntax weaving may split the slice into
    several adjacent decorated parts.
    """

    line_no: int
    """One-based source line containing this visible range intersection.

    Multi-line GumTree trees produce one expectation per intersected display
    line, each addressed independently in the composed payload.
    """

    start: int
    """Zero-based inclusive character offset within the displayed line.

    It is derived from GumTree's absolute offset after intersecting the line's
    visible content, excluding its terminator.
    """

    end: int
    """Zero-based exclusive character offset within the same display line.

    The interval is always non-empty and may be covered by several adjacent
    actual parts when syntax decoration introduces extra boundaries.
    """

    text: str
    """Exact source slice covered by this line-local expectation.

    Failure diagnostics show it so a range mismatch is readable without
    translating offsets by hand.
    """

    tree: str
    """Raw GumTree tree spelling from which this expectation originated.

    It is diagnostic provenance only. Actual payload lookup uses `side` and
    `line_no`, never this external string.
    """


@dataclass(frozen=True)
class ActualTokenStatus:
    """Record one rendered changed part in line-local expectation coordinates.

    The extraction helper derives these values from composed decorated parts,
    then compares them with ranges parsed independently from GumTree actions.
    The record omits syntax classes and row status because neither determines
    action-range coverage.
    """

    start: int
    """Inclusive line-local offset accumulated from preceding decorated parts.

    It refers to rendered source characters, not bytes or GumTree tree text.
    """

    end: int
    """Exclusive line-local offset after this complete decorated part.

    Neighboring intervals may touch and jointly cover one GumTree expectation.
    """

    text: str
    """Exact rendered part text occupying this interval.

    Joining all parts for the row reproduces source; this test retains only
    parts carrying the status under inspection.
    """


def _extract_helper_engine() -> GumTreeDiffEngine:
    """Construct the stateless engine used by the helper-extraction fixture.

    Payload composition and raw JSON inspection call this same constructor so
    neither path carries hidden engine configuration.
    """
    return GumTreeDiffEngine()


def _extract_helper_payload() -> ComposedFilePayload:
    """Compose the helper-extraction fixture through the ordinary File path.

    Assertions consume the same enriched payload the endpoint would return,
    rather than a test-only copy of token weaving.

    # Usage

    Range-coverage tests use this payload together with raw JSON from
    `_extract_helper_engine` so expected and rendered ranges share one fixture.
    """
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
    """Load one exact side of the helper-extraction source pair.

    `left` selects `old.py`; every other test-internal value selects `new.py`.
    Callers pass only the closed side values used by this module.
    """
    file_name = "old.py" if side == "left" else "new.py"
    return (FIXTURE_ROOT / file_name).read_text()


def _expected_for_tree(
    *,
    side: Literal["left", "right"],
    status: str,
    tree: str,
) -> list[ExpectedTokenStatus]:
    """Expand one absolute GumTree range into line-local expectations.

    # Parameters

    - `side`: Source document whose offsets the tree addresses.
    - `status`: Inline classification expected over every visible intersection.
    - `tree`: Raw GumTree tree description carrying an absolute range.

    # Usage

    `_expected_action_statuses` calls this after selecting one required GumTree
    tree range and its side-specific status.
    """
    text = _source_text(side)
    source_range = range_from_tree(tree)
    expected: list[ExpectedTokenStatus] = []
    for segment in line_segments(text):
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
    """Build fixture expectations directly from its required GumTree actions.

    The helper names the actions whose semantics the test covers and follows
    GumTree matches for update and move destination ranges. Missing facts fail
    immediately rather than weakening the expected coverage.

    # Usage

    Pass raw JSON from the helper-extraction fixture, then compare each returned
    interval against `_actual_status_intervals` from the composed payload.

    # Failures

    Raises `AssertionError` when any required action or destination match is
    absent from the fixture result.
    """

    def _action_tree(diff_json: GumTreeJson, action: str, tree: str) -> str:
        """Return the exact GumTree action tree required by this fixture.

        # Parameters

        - `diff_json`: Raw result whose actions are searched.
        - `action`: Exact operation name expected in the fixture.
        - `tree`: Exact source-tree description expected for that operation.

        # Failures

        Raises `AssertionError` when the fixture JSON lacks the exact action.
        """
        actions = diff_json.get("actions", [])
        for candidate in actions:
            if candidate["action"] == action and candidate["tree"] == tree:
                return candidate["tree"]
        raise AssertionError(f"GumTree action is missing: {action} {tree}")

    def _matched_dest_tree(diff_json: GumTreeJson, src_tree: str) -> str:
        """Return the destination paired with one required source tree.

        # Parameters

        - `diff_json`: Raw result whose match table is searched.
        - `src_tree`: Exact update or move source tree requiring a destination.

        # Failures

        Raises `AssertionError` when the fixture JSON lacks the source match.
        """
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
    side: Literal["left", "right"],
    line_no: int,
) -> DiffRow:
    """Find the enriched row addressing one exact source line.

    # Parameters

    - `payload`: Flatfile composition expected to contain one text bay.
    - `side`: Side whose line-number field is matched.
    - `line_no`: One-based source line that must exist in the payload.

    # Usage

    `_actual_status_intervals` calls this for the exact side and line addressed
    by one expected GumTree range.

    # Failures

    Raises `AssertionError` when the payload is not one flatfile text bay or the
    addressed line is absent.
    """
    # A flat Python file composes into exactly one text bay, and its rows are
    # the rows this module asserts against.
    (frame,) = payload["frames"]
    (bay,) = frame["bays"]
    kind_data = bay["kind_data"]
    assert kind_data["kind"] == "text"
    for row in kind_data["rows"]:
        row_line_no = row["left_no"] if side == "left" else row["right_no"]
        if row_line_no == line_no:
            return row
    raise AssertionError(f"Missing {side} row for line {line_no}")


def _actual_status_intervals(
    payload: ComposedFilePayload,
    *,
    side: Literal["left", "right"],
    status: str,
    line_no: int,
) -> list[ActualTokenStatus]:
    """Return line-local intervals carrying one visible diff status.

    # Parameters

    - `payload`: Composed helper-extraction diff.
    - `side`: Source side whose decorated parts are scanned.
    - `status`: Diff classification selected from those parts.
    - `line_no`: One-based source line addressed by the expectation.

    # Usage

    Call for one expected line and status, then use `_is_covered` to compare the
    returned adjacent decorated intervals with the expected range.
    """
    row = _row_for_line(payload, side=side, line_no=line_no)
    cursor = 0
    intervals: list[ActualTokenStatus] = []
    parts = row["left_parts"] if side == "left" else row["right_parts"]
    for part in parts:
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
    """Report whether adjacent actual intervals cover an expected source span.

    # Parameters

    - `expected`: Required line-local range and status from GumTree.
    - `actual_intervals`: Ordered rendered intervals carrying that status.

    # Usage

    Pass intervals returned by `_actual_status_intervals` for the same side,
    line, and status as `expected`.
    """
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
    """Cover every required GumTree action range with matching rendered tokens.

    The assertion permits adjacent parts because syntax weaving may split one
    diff token, but it permits no gaps in the source range.
    """
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
    """Keep GumTree's visual rows neutral while summaries follow token ranges.

    GumTree decorates independent source lines rather than line-aligning them;
    row status stays equal and the File summary derives changes from tokens.
    """
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
