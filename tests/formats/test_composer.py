"""Check `Composer` with ordinary text and rejected text bytes.

The tests pin the flatfile frame and bay shape, bay-local hunk indexes, captured
side existence, and blob termination after decoding rejection. Engine-free
cases verify that `bays()` exposes identity and decoded content without running
a renderer. Full composition uses the real text engine and enrichment path.
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
    """Keep a plain text modification in the total flatfile composition shape.

    The result must have one heading-less frame and the stable flatfile bay key,
    with both paths and text kind intact. It must not acquire notebook/image
    chrome merely because its contents differ.
    """
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
    """Assign navigation identity once per separated changed-row run.

    The two edits in the fixture must produce indexes zero and one on their
    first rows only. Equal continuation rows and later rows in the same run
    carry `None`; no File-wide renumbering is allowed.
    """
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
    """Keep byte-identical flatfile rows outside the hunk sequence.

    The composed text bay still exists and replays both sides, but its hunk
    count is zero and every row omits hunk identity. Equality must not create a
    synthetic navigation stop.
    """
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
    """Derive added-File side existence from captured inputs, not rendered rows.

    A missing left byte side must remain absent in the composed summary while
    the right side exists. The text builder may render insertion rows but those
    rows are not the authority for File existence.
    """
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
    """Derive deleted-File side existence directly from captured inputs.

    The left side remains present and the missing right side remains false in
    the summary. Composition must not infer presence from labels, paths, or
    deletion rows.
    """
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
    """Forward one flatfile bay's exact engine line totals to the File summary.

    Modified, added, removed, and moved counts must agree with the rendered bay
    without recounting display rows differently. Side-existence facts remain a
    separate captured-input calculation.
    """
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
