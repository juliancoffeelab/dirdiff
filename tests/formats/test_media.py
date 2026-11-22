from __future__ import annotations

import hashlib
from pathlib import Path

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
    ComposeContext,
    Composer,
)
from dirdiff.util import maybe


def test_svg_is_diffed_as_the_text_it_is() -> None:
    context = ComposeContext.build(
        left_path=maybe("chart.svg"),
        right_path=maybe("chart.svg"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b'<svg width="10"></svg>\n'),
        maybe(b'<svg width="20"></svg>\n'),
        context,
    )
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert composed["summary"]["changed_lines"] > 0


def test_an_unlisted_image_format_reaches_the_blob_terminal() -> None:
    context = ComposeContext.build(
        left_path=maybe("scan.tiff"),
        right_path=maybe("scan.tiff"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b"II*\x00-old"), maybe(b"II*\x00-new"), context
    )
    bays = composed["frames"][0]["bays"]
    assert len(bays) == 1, "a blob File composes its facts and nothing else"
    content = bays[0]["kind_data"]
    assert content["kind"] == "text"
    assert bays[0]["bay_key"] == BLOB_BAY_KEY
    first = content["rows"][0]
    assert first["left_text"] == "type: application/octet-stream"
    assert first["right_text"] == "type: application/octet-stream"


def test_a_file_that_is_an_image_on_one_side_only_is_not_an_image() -> None:
    context = ComposeContext.build(
        left_path=maybe("asset.png"),
        right_path=maybe("asset.dat"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b"\x89PNG\r\n\x1a\n"), maybe(b"\x00\x01\x02\x03"), context
    )
    bay = composed["frames"][0]["bays"][0]
    content = bay["kind_data"]
    assert bay["bay_key"] == BLOB_BAY_KEY
    assert content["kind"] == "text"
    stated = [(row["left_text"], row["right_text"]) for row in content["rows"]]
    assert (
        "type: application/octet-stream",
        "type: application/octet-stream",
    ) in stated
    left_digests = [
        row["left_text"]
        for row in content["rows"]
        if row["left_text"] is not None
        and row["left_text"].startswith("sha256: ")
    ]
    right_digests = [
        row["right_text"]
        for row in content["rows"]
        if row["right_text"] is not None
        and row["right_text"].startswith("sha256: ")
    ]
    assert left_digests != right_digests


def test_a_file_captured_on_neither_side_is_not_an_image() -> None:
    context = ComposeContext.build(
        left_path=maybe("ghost.png"),
        right_path=maybe("ghost.png"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(maybe(None), maybe(None), context)
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert composed["summary"]["left_exists"] is False
    assert composed["summary"]["right_exists"] is False


def test_a_file_made_of_bytes_reports_only_its_facts_lines() -> None:
    context = ComposeContext.build(
        left_path=maybe("clip.ogg"),
        right_path=maybe("clip.ogg"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b"OggS\x00-one"), maybe(b"OggS\x00-two-longer"), context
    )
    facts_content = composed["frames"][0]["bays"][0]["kind_data"]
    assert facts_content["kind"] == "text"
    summary = composed["summary"]
    for key in (
        "changed_lines",
        "modified_lines",
        "added_lines",
        "removed_lines",
        "moved_lines",
    ):
        assert summary[key] == facts_content["stats"][key], key
    # The size and the digest moved; the media type did not.
    assert summary["changed_lines"] == 2


def test_real_preset_fixtures_compose_to_their_captured_bytes() -> None:
    formats = Path(__file__).parents[1] / "presets" / "formats"
    expectations = {
        ("images", "image-changed"): (
            IMAGE_BAY_KEY,
            "old.png",
            "new.png",
            "changed",
        ),
        ("images", "image-added"): (
            IMAGE_BAY_KEY,
            None,
            "new.png",
            "added",
        ),
        ("images", "image-removed"): (
            IMAGE_BAY_KEY,
            "old.png",
            None,
            "removed",
        ),
        ("unsupported", "blob-content-changed"): (
            BLOB_BAY_KEY,
            "old.ogg",
            "new.ogg",
            "changed",
        ),
    }

    for (group, case), (
        bay_key,
        old_name,
        new_name,
        change,
    ) in expectations.items():
        fixture = formats / group / case
        left = None if old_name is None else (fixture / old_name).read_bytes()
        right = None if new_name is None else (fixture / new_name).read_bytes()
        context = ComposeContext.build(
            left_path=maybe(None if old_name is None else f"{case}/{old_name}"),
            right_path=maybe(
                None if new_name is None else f"{case}/{new_name}"
            ),
            left_label="old",
            right_label="new",
            renderer=TextDiffEngine(),
        )
        bays = Composer().compose(maybe(left), maybe(right), context)["frames"][
            0
        ]["bays"]
        assert bays[0]["bay_key"] == bay_key, case
        assert bays[0]["change"] == {"kind": change}, case

        # Whichever classification produced them, the facts a reviewer reads
        # are the facts of the bytes on disk: the picture states them in its
        # reference, and a blob states them as its rows.
        facts = next(
            bay
            for bay in bays
            if bay["bay_key"] == BLOB_BAY_KEY
            or bay["bay_key"] == IMAGE_FACTS_BAY_KEY
        )
        facts_content = facts["kind_data"]
        assert facts_content["kind"] == "text", case
        # An absent side numbers no lines; a present one states every fact.
        left_stated = [
            row["left_text"]
            for row in facts_content["rows"]
            if row["left_no"] is not None
        ]
        right_stated = [
            row["right_text"]
            for row in facts_content["rows"]
            if row["right_no"] is not None
        ]
        for stated, data in ((left_stated, left), (right_stated, right)):
            if data is None:
                assert stated == [], case
                continue
            assert f"size: {len(data)} bytes" in stated, case
            assert f"sha256: {hashlib.sha256(data).hexdigest()}" in stated, case
