from __future__ import annotations

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    BayContext,
    ComposeContext,
    Composer,
    TextBay,
)
from dirdiff.util import maybe


def test_plain_modification_is_one_frame_one_flatfile_bay() -> None:
    context = ComposeContext.build(
        left_path=maybe("m.py"),
        right_path=maybe("m.py"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    left = b"a = 1\nb = 2\nc = 3\n"
    right = b"a = 1\nb = 20\nc = 3\n"
    composed = Composer().compose(maybe(left), maybe(right), context)

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
    context = ComposeContext.build(
        left_path=maybe("m.txt"),
        right_path=maybe("m.txt"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    left = b"1\n2\n3\n4\n5\n6\n7\n"
    right = b"1\nX\n3\n4\nY\n6\n7\n"
    composed = Composer().compose(maybe(left), maybe(right), context)
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "text"
    rows = content["rows"]

    carried = [
        row["hunk_index"] for row in rows if row["hunk_index"] is not None
    ]
    assert carried == [0, 1], "two separated edits are two bay-local hunks"


def test_identical_content_has_no_hunks() -> None:
    context = ComposeContext.build(
        left_path=maybe("s.txt"),
        right_path=maybe("s.txt"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    same = b"unchanged\ncontent\n"
    composed = Composer().compose(maybe(same), maybe(same), context)
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "text"
    rows = content["rows"]
    assert len(rows) > 0, "identical sides must still render their rows"
    assert all(row["hunk_index"] is None for row in rows)


def test_added_file_has_left_absent_and_right_present() -> None:
    context = ComposeContext.build(
        left_path=maybe(None),
        right_path=maybe("n.txt"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(maybe(None), maybe(b"new file\n"), context)
    assert composed["summary"]["left_exists"] is False
    assert composed["summary"]["right_exists"] is True


def test_deleted_file_has_right_absent() -> None:
    context = ComposeContext.build(
        left_path=maybe("g.txt"),
        right_path=maybe(None),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(maybe(b"gone\n"), maybe(None), context)
    assert composed["summary"]["left_exists"] is True
    assert composed["summary"]["right_exists"] is False


def test_summary_aggregates_bay_stats_for_single_bay() -> None:
    context = ComposeContext.build(
        left_path=maybe("a.txt"),
        right_path=maybe("a.txt"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    left = b"keep\ndrop\n"
    right = b"keep\nadd1\nadd2\n"
    composed = Composer().compose(maybe(left), maybe(right), context)
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
    context = ComposeContext.build(
        left_path=maybe("b.bin"),
        right_path=maybe("b.bin"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b"ok\n"), maybe(b"bad\x00byte"), context
    )
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
    context = BayContext(
        left_path=maybe("c.py"),
        right_path=maybe("c.py"),
        left_label="old",
        right_label="new",
    )
    produced = list(
        Composer().bays(maybe(b"before\n"), maybe(b"after\n"), context)
    )
    assert len(produced) == 1
    bay = produced[0]
    assert isinstance(bay, TextBay)
    assert bay.bay_key == FLATFILE_BAY_KEY
    assert bay.frame_key == "file"
    assert bay.heading is None
    assert bay.left.text == "before\n"
    assert bay.right.text == "after\n"
    assert bay.left.path_hint == "c.py"
