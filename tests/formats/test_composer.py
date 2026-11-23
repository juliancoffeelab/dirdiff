from __future__ import annotations

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BLOB_BAY_KEY,
    ComposeContext,
    Composer,
)
from dirdiff.util import maybe


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
