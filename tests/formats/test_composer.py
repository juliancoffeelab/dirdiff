"""Adversarial tests for `dirdiff.formats.Composer`.

These pin the composed-diff shape a plain text File must produce: one
heading-less frame, one bay keyed `flatfile`, bay-local hunk indexes that are
gap-free and only mark changed-run starts, existence flags that follow the
captured byte sides, the blob terminal that keeps classification total, and
the two engine-free guarantees `bays()` owes review validation. They exercise the real engine and enrichment pipeline, not a stub.
"""

from __future__ import annotations

from dirdiff.engines import engine
from dirdiff.formats import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    BayContext,
    ComposeContext,
    Composer,
    TextBay,
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
    content = bay["kind_data"]
    assert content["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert bay["default_expanded"] is True
    assert content["left_label"] == "old"
    assert content["right_label"] == "new"
    assert len(content["rows"]) > 0, "a modification must render rows"
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
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "text"
    rows = content["rows"]

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
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "text"
    rows = content["rows"]
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
    stats_content = composed["frames"][0]["bays"][0]["kind_data"]
    assert stats_content["kind"] == "text"
    bay_stats = stats_content["stats"]
    for key in (
        "changed_lines",
        "modified_lines",
        "added_lines",
        "removed_lines",
        "moved_lines",
    ):
        assert composed["summary"][key] == bay_stats[key]


def test_one_binary_side_sends_the_whole_file_to_the_blob_terminal() -> None:
    """A NUL byte on either side composes the blob bay rather than raising.

    Both sides go, not just the offending one: a File is one classification, and
    diffing readable text against a digest would compare two different things.
    The blob bay is `text` — its rows are the facts known about the bytes — so
    what a reviewer reads is the size and digest changing, line by line.
    """
    context = ComposeContext.build(
        left_path="b.bin",
        right_path="b.bin",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(b"ok\n", b"bad\x00byte", context)
    bay = composed["frames"][0]["bays"][0]
    content = bay["kind_data"]
    assert content["kind"] == "text"
    assert bay["bay_key"] == BLOB_BAY_KEY
    assert bay["collapsible"] is False, "the facts are the blob frame's body"
    assert bay["change"] == {"kind": "changed"}

    left_facts = [
        row["left_text"]
        for row in content["rows"]
        if row["left_text"] is not None
    ]
    right_facts = [
        row["right_text"]
        for row in content["rows"]
        if row["right_text"] is not None
    ]
    assert "size: 3 bytes" in left_facts
    assert "size: 8 bytes" in right_facts
    left_digests = [line for line in left_facts if line.startswith("sha256: ")]
    right_digests = [
        line for line in right_facts if line.startswith("sha256: ")
    ]
    assert len(left_digests) == 1 and len(right_digests) == 1
    assert left_digests != right_digests, (
        "different bytes state different digests"
    )
    # The facts are lines, so they count as lines: a blob File whose bytes
    # changed reports the fact lines that changed, not a lineless zero.
    assert (
        composed["summary"]["changed_lines"]
        == content["stats"]["changed_lines"]
    )
    assert composed["summary"]["changed_lines"] > 0


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
    assert isinstance(bay, TextBay)
    assert bay.bay_key == FLATFILE_BAY_KEY
    assert bay.frame_key == "file"
    assert bay.heading is None
    assert bay.left.text == "before\n"
    assert bay.right.text == "after\n"
    assert bay.left.path_hint == "c.py"
