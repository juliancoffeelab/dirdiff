from __future__ import annotations

import hashlib
from pathlib import Path

from dirdiff.engines import TextDiffEngine
from dirdiff.formats import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
    IMAGE_METADATA_BAY_KEY,
    BayContext,
    ComposeContext,
    Composer,
    ImageBay,
    TextBay,
)
from dirdiff.util import Some, maybe


def test_image_pair_composes_picture_metadata_and_facts_bays() -> None:
    context = ComposeContext.build(
        left_path=maybe("logo.png"),
        right_path=maybe("logo.png"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    left = b"\x89PNG\r\n\x1a\n-old-bytes"
    right = b"\x89PNG\r\n\x1a\n-new-bytes-longer"
    composed = Composer().compose(maybe(left), maybe(right), context)

    assert len(composed["frames"]) == 1
    frame = composed["frames"][0]
    assert frame["frame_key"] == "file"
    assert frame["heading"] is None
    assert len(frame["bays"]) == 3

    picture = frame["bays"][0]
    picture_content = picture["kind_data"]
    assert picture_content["kind"] == "image"
    assert picture["bay_key"] == IMAGE_BAY_KEY
    assert picture["collapsible"] is False
    assert picture["default_expanded"] is True
    assert picture["change"] == {"kind": "changed"}
    assert picture_content["left"] == {
        "media_type": "image/png",
        "byte_size": len(left),
        "digest": hashlib.sha256(left).hexdigest(),
    }
    assert picture_content["right"] == {
        "media_type": "image/png",
        "byte_size": len(right),
        "digest": hashlib.sha256(right).hexdigest(),
    }

    metadata = frame["bays"][1]
    assert metadata["bay_key"] == IMAGE_METADATA_BAY_KEY
    assert metadata["kind_data"]["kind"] == "text"
    assert metadata["warnings"] != [], (
        "the deliberately invalid PNG bytes must report metadata degradation"
    )

    facts = frame["bays"][2]
    facts_content = facts["kind_data"]
    assert facts_content["kind"] == "text"
    assert facts["bay_key"] == IMAGE_FACTS_BAY_KEY
    assert facts["collapsible"] is True
    assert facts["default_expanded"] is True, (
        "the size and digest are read, not hunted for"
    )
    assert facts["change"] == {"kind": "changed"}
    assert [row["left_text"] for row in facts_content["rows"]] == [
        "type: image/png",
        f"size: {len(left)} bytes",
        f"sha256: {hashlib.sha256(left).hexdigest()}",
    ]

    # Existence follows the captured sides, exactly as it does for text.
    assert composed["summary"]["left_exists"] is True
    assert composed["summary"]["right_exists"] is True


def test_byte_identical_media_sides_are_unchanged() -> None:
    same_image = b"\x89PNG\r\n\x1a\nidentical"
    image_context = ComposeContext.build(
        left_path=maybe("icon.png"),
        right_path=maybe("icon.png"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    image_bays = Composer().compose(
        maybe(same_image), maybe(same_image), image_context
    )["frames"][0]["bays"]
    picture_content = image_bays[0]["kind_data"]
    assert picture_content["kind"] == "image"
    assert image_bays[0]["change"] == {"kind": "unchanged"}
    assert picture_content["left"] == picture_content["right"]
    # Identical bytes state identical facts, so the facts bay is unchanged too.
    assert image_bays[2]["bay_key"] == IMAGE_FACTS_BAY_KEY
    assert image_bays[2]["change"] == {"kind": "unchanged"}

    same_blob = b"\x00\x01\x02identical"
    blob_context = ComposeContext.build(
        left_path=maybe("blob.dat"),
        right_path=maybe("blob.dat"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    blob_bay = Composer().compose(
        maybe(same_blob), maybe(same_blob), blob_context
    )["frames"][0]["bays"][0]
    assert blob_bay["bay_key"] == BLOB_BAY_KEY
    assert blob_bay["kind_data"]["kind"] == "text"
    assert blob_bay["change"] == {"kind": "unchanged"}


def test_added_and_removed_images_carry_exactly_one_side() -> None:
    added_context = ComposeContext.build(
        left_path=maybe(None),
        right_path=maybe("new-asset.webp"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    added = Composer().compose(
        maybe(None), maybe(b"RIFF....WEBP"), added_context
    )
    added_bays = added["frames"][0]["bays"]
    added_content = added_bays[0]["kind_data"]
    assert added_content["kind"] == "image"
    assert added_bays[0]["change"] == {"kind": "added"}
    assert added_content["left"] is None
    assert added_content["right"] is not None
    assert added_content["right"]["media_type"] == "image/webp"
    added_facts = added_bays[2]["kind_data"]
    assert added_facts["kind"] == "text"
    assert all(row["left_no"] is None for row in added_facts["rows"])
    assert "type: image/webp" in [
        row["right_text"] for row in added_facts["rows"]
    ]
    assert added["summary"]["left_exists"] is False
    assert added["summary"]["right_exists"] is True

    removed_context = ComposeContext.build(
        left_path=maybe("old-asset.gif"),
        right_path=maybe(None),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    removed = Composer().compose(
        maybe(b"GIF89a...."), maybe(None), removed_context
    )
    removed_bays = removed["frames"][0]["bays"]
    removed_content = removed_bays[0]["kind_data"]
    assert removed_content["kind"] == "image"
    assert removed_bays[0]["change"] == {"kind": "removed"}
    assert removed_content["right"] is None
    assert removed_content["left"] is not None
    assert removed_content["left"]["media_type"] == "image/gif"
    removed_facts = removed_bays[2]["kind_data"]
    assert removed_facts["kind"] == "text"
    assert all(row["right_no"] is None for row in removed_facts["rows"])
    assert removed["summary"]["left_exists"] is True
    assert removed["summary"]["right_exists"] is False


def test_image_classification_reads_the_path_and_not_the_bytes() -> None:
    context = ComposeContext.build(
        left_path=maybe("mislabelled.png"),
        right_path=maybe("mislabelled.png"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(
        maybe(b"not a png at all\n"), maybe(b"still not\n"), context
    )
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "image"
    assert bay["bay_key"] == IMAGE_BAY_KEY
    # The text engine never saw the picture's own bytes: the only rows composed
    # are the facts bay's, so nothing a reviewer reads is the mislabelled text.
    assert "not a png at all" not in repr(composed)
    assert "still not" not in repr(composed)


def test_extension_matching_ignores_case_and_pairs_jpg_with_jpeg() -> None:
    shouted_context = ComposeContext.build(
        left_path=maybe("PHOTO.JPG"),
        right_path=maybe("PHOTO.JPG"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    shouted = Composer().compose(
        maybe(b"\xff\xd8\xff-a"), maybe(b"\xff\xd8\xff-b"), shouted_context
    )
    shouted_content = shouted["frames"][0]["bays"][0]["kind_data"]
    assert shouted_content["kind"] == "image"
    assert shouted_content["left"] is not None
    assert shouted_content["left"]["media_type"] == "image/jpeg"

    spelled_context = ComposeContext.build(
        left_path=maybe("photo.jpeg"),
        right_path=maybe("photo.jpeg"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    spelled = Composer().compose(
        maybe(b"\xff\xd8\xff-a"), maybe(b"\xff\xd8\xff-b"), spelled_context
    )
    spelled_content = spelled["frames"][0]["bays"][0]["kind_data"]
    assert spelled_content["kind"] == "image"
    assert spelled_content["left"] is not None
    assert spelled_content["left"]["media_type"] == "image/jpeg"


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


def test_bays_yields_an_image_bay_holding_the_exact_captured_bytes() -> None:
    left = bytes(range(256))
    right = bytes(reversed(range(256)))
    produced = list(
        Composer().bays(
            maybe(left),
            maybe(right),
            BayContext(
                left_path=maybe("sprite.bmp"),
                right_path=maybe("sprite.bmp"),
                left_label="old",
                right_label="new",
            ),
        )
    )
    assert len(produced) == 3
    picture = produced[0]
    assert isinstance(picture, ImageBay)
    assert picture.bay_key == IMAGE_BAY_KEY
    assert picture.frame_key == "file"
    assert picture.heading is None
    assert Some.present(picture.left) and picture.left.value.data == left
    assert Some.present(picture.right) and picture.right.value.data == right
    assert picture.left.value.media_type == "image/bmp"

    metadata = produced[1]
    assert isinstance(metadata, TextBay)
    assert metadata.bay_key == IMAGE_METADATA_BAY_KEY
    assert metadata.warnings != ()

    facts = produced[2]
    assert isinstance(facts, TextBay)
    assert facts.bay_key == IMAGE_FACTS_BAY_KEY
    assert facts.frame_key == "file", "one frame holds both bays"
    assert facts.left.text is not None
    assert facts.left.text.startswith("type: image/bmp\n")


def test_a_rendered_image_bay_carries_no_captured_byte() -> None:
    marker = b"\x00SECRET-CAPTURED-BYTES"
    context = ComposeContext.build(
        left_path=maybe("a.ico"),
        right_path=maybe("a.ico"),
        left_label="old",
        right_label="new",
        renderer=TextDiffEngine(),
    )
    composed = Composer().compose(maybe(marker), maybe(marker + b"!"), context)
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "image"
    assert content["left"] is not None
    assert set(content["left"]) == {"media_type", "byte_size", "digest"}
    # `repr` of a `bytes` spells its printable run out as ASCII, so the marker
    # would be legible in it if any field anywhere in the envelope held it.
    assert "SECRET-CAPTURED-BYTES" not in repr(composed)


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
