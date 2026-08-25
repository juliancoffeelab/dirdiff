"""Adversarial tests for `dirdiff.formats.Composer`.

These pin the composed-diff shape a plain text File must produce: one
heading-less frame, one bay keyed `flatfile`, bay-local hunk indexes that are
gap-free and only mark changed-run starts, existence flags that follow the
captured byte sides, and the two engine-free guarantees `bays()` owes review
validation. They exercise the real engine and enrichment pipeline, not a stub.
"""

from __future__ import annotations

import pytest

from dirdiff.engines import DirdiffError, engine
from dirdiff.formats import (
    FLATFILE_BAY_KEY,
    BayContext,
    ComposeContext,
    Composer,
)


def test_plain_modification_is_one_frame_one_flatfile_bay() -> None:
    """A modified text File composes to one frame holding one `text` bay."""
    context = ComposeContext.build(
        left_path="m.py",
        right_path="m.py",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    left = b"a = 1\nb = 2\nc = 3\n"
    right = b"a = 1\nb = 20\nc = 3\n"
    composed = Composer().compose(left, right, context)

    assert len(composed["frames"]) == 1
    frame = composed["frames"][0]
    assert frame["frame_key"] == "file"
    assert frame["heading"] is None
    assert len(frame["bays"]) == 1

    bay = frame["bays"][0]
    assert bay["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert bay["default_expanded"] is True
    assert bay["left_label"] == "old"
    assert bay["right_label"] == "new"
    assert len(bay["rows"]) > 0, "a modification must render rows"
    assert composed["left_path"] == "m.py"
    assert composed["right_path"] == "m.py"


def test_hunk_indexes_are_gapless_and_mark_only_run_starts() -> None:
    """Bay-local hunk indexes are consecutive from zero on run starts only."""
    context = ComposeContext.build(
        left_path="m.txt",
        right_path="m.txt",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    left = b"1\n2\n3\n4\n5\n6\n7\n"
    right = b"1\nX\n3\n4\nY\n6\n7\n"
    composed = Composer().compose(left, right, context)
    rows = composed["frames"][0]["bays"][0]["rows"]

    carried = [
        row["hunk_index"] for row in rows if row["hunk_index"] is not None
    ]
    assert carried == [0, 1], "two separated edits are two bay-local hunks"


def test_identical_content_has_no_hunks() -> None:
    """Byte-identical sides compose to a File with zero hunks."""
    context = ComposeContext.build(
        left_path="s.txt",
        right_path="s.txt",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    same = b"unchanged\ncontent\n"
    composed = Composer().compose(same, same, context)
    rows = composed["frames"][0]["bays"][0]["rows"]
    assert len(rows) > 0, "identical sides must still render their rows"
    assert all(row["hunk_index"] is None for row in rows)


def test_added_file_has_left_absent_and_right_present() -> None:
    """A right-only File reports existence from the byte sides it was given."""
    context = ComposeContext.build(
        left_path=None,
        right_path="n.txt",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(None, b"new file\n", context)
    assert composed["summary"]["left_exists"] is False
    assert composed["summary"]["right_exists"] is True


def test_deleted_file_has_right_absent() -> None:
    """A left-only File reports the right side absent."""
    context = ComposeContext.build(
        left_path="g.txt",
        right_path=None,
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(b"gone\n", None, context)
    assert composed["summary"]["left_exists"] is True
    assert composed["summary"]["right_exists"] is False


def test_summary_aggregates_bay_stats_for_single_bay() -> None:
    """The File summary equals the one bay's engine stats for line counts."""
    context = ComposeContext.build(
        left_path="a.txt",
        right_path="a.txt",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    left = b"keep\ndrop\n"
    right = b"keep\nadd1\nadd2\n"
    composed = Composer().compose(left, right, context)
    bay_stats = composed["frames"][0]["bays"][0]["stats"]
    for key in (
        "changed_lines",
        "modified_lines",
        "added_lines",
        "removed_lines",
        "moved_lines",
    ):
        assert composed["summary"][key] == bay_stats[key]


def test_binary_side_is_rejected_at_the_decode_boundary() -> None:
    """A NUL byte is not text and raises rather than composing silently."""
    context = ComposeContext.build(
        left_path="b.bin",
        right_path="b.bin",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    with pytest.raises(DirdiffError):
        Composer().compose(b"ok\n", b"bad\x00byte", context)


def test_bays_is_engine_free_and_yields_decoded_sides() -> None:
    """`bays()` enumerates identity and decoded content without a renderer.

    It takes a `BayContext`, which carries no renderer, so review validation
    can recompute bay keys and read origin content with no engine in reach.
    """
    context = BayContext(
        left_path="c.py",
        right_path="c.py",
        left_label="old",
        right_label="new",
    )
    produced = list(Composer().bays(b"before\n", b"after\n", context))
    assert len(produced) == 1
    bay = produced[0]
    assert bay.bay_key == FLATFILE_BAY_KEY
    assert bay.frame_key == "file"
    assert bay.heading is None
    assert bay.left.text == "before\n"
    assert bay.right.text == "after\n"
    assert bay.left.path_hint == "c.py"
