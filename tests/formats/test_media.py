"""Adversarial tests for the Files whose content is bytes rather than lines.

These pin what `Composer` decides about content this project shows rather than
diffs: which Files become an `image` bay, which fall through to the `blob`
terminal, what each side's reference says about the bytes behind it, and the
guarantee that no captured byte ever reaches a payload. They exercise the real
classification, not a stub, and they use the real preset fixtures where the
question is whether actual captured content survives composition unchanged.

Only the picture is a kind of its own. What is *known* about bytes — media type,
size, digest — is text, so an image File composes a facts bay beside its picture
and a blob File composes that facts bay alone. These tests therefore read a blob
File's media type out of its rendered rows, which is where a reviewer reads it.

The `unchanged` cases matter more than they look: a bay whose two sides are
byte-identical must say so, because that is what stops an untouched picture from
taking a navigation stop in a File the reviewer has no reason to visit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from dirdiff.engines import engine
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


def test_image_pair_composes_picture_metadata_and_facts_bays() -> None:
    """A `.png` pair composes picture, parsed metadata, and byte facts.

    Each side's reference must describe the exact bytes handed in, because the
    reviewer decides whether the picture changed by comparing the two digests
    before either picture has finished loading. The facts bay repeats them as
    rows, which is what makes the digest commentable.
    """
    context = ComposeContext.build(
        left_path="logo.png",
        right_path="logo.png",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    left = b"\x89PNG\r\n\x1a\n-old-bytes"
    right = b"\x89PNG\r\n\x1a\n-new-bytes-longer"
    composed = Composer().compose(left, right, context)

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
    """Two equal sides are `unchanged`, for an image and for a blob File.

    Both sides are present and both are shown, which is precisely why this must
    not be reported as a change: the whole-File rule reads the bytes, not the
    presence, and an untouched asset dragged along by a wide diff must take no
    navigation stop.
    """
    same_image = b"\x89PNG\r\n\x1a\nidentical"
    image_context = ComposeContext.build(
        left_path="icon.png",
        right_path="icon.png",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    image_bays = Composer().compose(same_image, same_image, image_context)[
        "frames"
    ][0]["bays"]
    picture_content = image_bays[0]["kind_data"]
    assert picture_content["kind"] == "image"
    assert image_bays[0]["change"] == {"kind": "unchanged"}
    assert picture_content["left"] == picture_content["right"]
    # Identical bytes state identical facts, so the facts bay is unchanged too.
    assert image_bays[2]["bay_key"] == IMAGE_FACTS_BAY_KEY
    assert image_bays[2]["change"] == {"kind": "unchanged"}

    same_blob = b"\x00\x01\x02identical"
    blob_context = ComposeContext.build(
        left_path="blob.dat",
        right_path="blob.dat",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    blob_bay = Composer().compose(same_blob, same_blob, blob_context)["frames"][
        0
    ]["bays"][0]
    assert blob_bay["bay_key"] == BLOB_BAY_KEY
    assert blob_bay["kind_data"]["kind"] == "text"
    assert blob_bay["change"] == {"kind": "unchanged"}


def test_added_and_removed_images_carry_exactly_one_side() -> None:
    """An uncaptured side is `None`, never a reference to empty bytes.

    A zero-byte reference would tell the widget there is something to fetch and
    the endpoint there is something to serve. Absence is a different fact from
    emptiness and stays one all the way to the wire — including in the facts
    bay, where an absent side states no facts rather than stating zeroes.
    """
    added_context = ComposeContext.build(
        left_path=None,
        right_path="new-asset.webp",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    added = Composer().compose(None, b"RIFF....WEBP", added_context)
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
        left_path="old-asset.gif",
        right_path=None,
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    removed = Composer().compose(b"GIF89a....", None, removed_context)
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
    """A `.png` holding readable text still composes as an image.

    This is the documented consequence of classifying by extension: what the
    repository calls the file is what the reviewer is shown it as. The test
    exists so the tradeoff is visible and a future sniffing implementation has
    to change it deliberately rather than discover it.
    """
    context = ComposeContext.build(
        left_path="mislabelled.png",
        right_path="mislabelled.png",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        b"not a png at all\n", b"still not\n", context
    )
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "image"
    assert bay["bay_key"] == IMAGE_BAY_KEY
    # The text engine never saw the picture's own bytes: the only rows composed
    # are the facts bay's, so nothing a reviewer reads is the mislabelled text.
    assert "not a png at all" not in repr(composed)
    assert "still not" not in repr(composed)


def test_extension_matching_ignores_case_and_pairs_jpg_with_jpeg() -> None:
    """`.JPG` and `.jpeg` are the same media type, whatever the case.

    A repository that shouts its filenames is not a repository of binary
    files, and two spellings of one format must not serve two `Content-Type`s.
    """
    shouted_context = ComposeContext.build(
        left_path="PHOTO.JPG",
        right_path="PHOTO.JPG",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    shouted = Composer().compose(
        b"\xff\xd8\xff-a", b"\xff\xd8\xff-b", shouted_context
    )
    shouted_content = shouted["frames"][0]["bays"][0]["kind_data"]
    assert shouted_content["kind"] == "image"
    assert shouted_content["left"] is not None
    assert shouted_content["left"]["media_type"] == "image/jpeg"

    spelled_context = ComposeContext.build(
        left_path="photo.jpeg",
        right_path="photo.jpeg",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    spelled = Composer().compose(
        b"\xff\xd8\xff-a", b"\xff\xd8\xff-b", spelled_context
    )
    spelled_content = spelled["frames"][0]["bays"][0]["kind_data"]
    assert spelled_content["kind"] == "image"
    assert spelled_content["left"] is not None
    assert spelled_content["left"]["media_type"] == "image/jpeg"


def test_svg_is_diffed_as_the_text_it_is() -> None:
    """An `.svg` pair composes a flatfile bay, not an `image` one.

    SVG is source: a reviewer changing a path's `d` attribute wants the two
    lines side by side, not two pictures to compare by eye. It is left off the
    image list for that reason, so it must reach the flatfile step and be
    diffed there.
    """
    context = ComposeContext.build(
        left_path="chart.svg",
        right_path="chart.svg",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        b'<svg width="10"></svg>\n',
        b'<svg width="20"></svg>\n',
        context,
    )
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert composed["summary"]["changed_lines"] > 0


def test_an_unlisted_image_format_reaches_the_blob_terminal() -> None:
    """A `.tiff` pair is composed as a blob, never as a broken picture.

    The list of image types is what the browser displays natively. A format
    outside it must not be handed to an `<img>` that would fail to decode it;
    the facts the blob bay states still answer whether the content changed.
    """
    context = ComposeContext.build(
        left_path="scan.tiff",
        right_path="scan.tiff",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(b"II*\x00-old", b"II*\x00-new", context)
    bays = composed["frames"][0]["bays"]
    assert len(bays) == 1, "a blob File composes its facts and nothing else"
    content = bays[0]["kind_data"]
    assert content["kind"] == "text"
    assert bays[0]["bay_key"] == BLOB_BAY_KEY
    first = content["rows"][0]
    assert first["left_text"] == "type: application/octet-stream"
    assert first["right_text"] == "type: application/octet-stream"


def test_a_file_that_is_an_image_on_one_side_only_is_not_an_image() -> None:
    """A picture replaced by unreadable bytes composes one blob bay.

    Both sides must claim an image type, because a File is one classification:
    half an image bay would mean displaying a picture beside a side the widget
    cannot show, which reports a format change as a missing file.
    """
    context = ComposeContext.build(
        left_path="asset.png",
        right_path="asset.dat",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        b"\x89PNG\r\n\x1a\n", b"\x00\x01\x02\x03", context
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
    """Two absent sides compose no picture, whatever the paths claim.

    There is nothing to show and nothing for the endpoint to serve, so the
    File takes the ordinary route rather than producing an image bay with two
    absent sides.
    """
    context = ComposeContext.build(
        left_path="ghost.png",
        right_path="ghost.png",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(None, None, context)
    bay = composed["frames"][0]["bays"][0]
    assert bay["kind_data"]["kind"] == "text"
    assert bay["bay_key"] == FLATFILE_BAY_KEY
    assert composed["summary"]["left_exists"] is False
    assert composed["summary"]["right_exists"] is False


def test_bays_yields_an_image_bay_holding_the_exact_captured_bytes() -> None:
    """`bays()` hands the media endpoint the bytes it must serve, unaltered.

    The endpoint has no other way in: there is one enumeration of a File's
    bays, and the bytes it carries are the ones capture retained. No
    transcoding, no re-encoding, no thumbnail. The facts bay beside it holds no
    bytes at all, which is why the endpoint can refuse everything but the
    picture.
    """
    left = bytes(range(256))
    right = bytes(reversed(range(256)))
    produced = list(
        Composer().bays(
            left,
            right,
            BayContext(
                left_path="sprite.bmp",
                right_path="sprite.bmp",
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
    assert picture.left is not None and picture.left.data == left
    assert picture.right is not None and picture.right.data == right
    assert picture.left.media_type == "image/bmp"

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
    """The payload describes the content and never contains it.

    An image bay's whole point is that its bytes travel over a separate
    endpoint, so the serialized side must hold exactly the three describing
    fields. This is the check that would fail the day someone adds a convenient
    `data` field to `MediaRef` — or renders the picture's bytes into the facts
    bay beside it.
    """
    marker = b"\x00SECRET-CAPTURED-BYTES"
    context = ComposeContext.build(
        left_path="a.ico",
        right_path="a.ico",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(marker, marker + b"!", context)
    content = composed["frames"][0]["bays"][0]["kind_data"]
    assert content["kind"] == "image"
    assert content["left"] is not None
    assert set(content["left"]) == {"media_type", "byte_size", "digest"}
    # `repr` of a `bytes` spells its printable run out as ASCII, so the marker
    # would be legible in it if any field anywhere in the envelope held it.
    assert "SECRET-CAPTURED-BYTES" not in repr(composed)


def test_a_file_made_of_bytes_reports_only_its_facts_lines() -> None:
    """A File's line counts are its facts bay's, because facts are its lines.

    No diff engine reads the content itself, so nothing else can contribute:
    the counts a reviewer sees in the file list are exactly how many stated
    facts changed. Counting the picture too, or reporting zero for a File whose
    digest moved, would both misdescribe the change.
    """
    context = ComposeContext.build(
        left_path="clip.ogg",
        right_path="clip.ogg",
        left_label="old",
        right_label="new",
        renderer=engine("dirdiff"),
    )
    composed = Composer().compose(
        b"OggS\x00-one", b"OggS\x00-two-longer", context
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
    """Every `formats` preset case composes the bays its name promises.

    These are real downloaded assets, not constructed byte strings, so this is
    what proves the sizes and digests a reviewer will read are the sizes and
    digests of the files on disk.
    """
    basic = Path(__file__).parents[1] / "presets" / "formats" / "basic"
    expectations = {
        "image-changed": (IMAGE_BAY_KEY, "old.png", "new.png", "changed"),
        "image-added": (IMAGE_BAY_KEY, None, "new.png", "added"),
        "image-removed": (IMAGE_BAY_KEY, "old.png", None, "removed"),
        "blob-content-changed": (BLOB_BAY_KEY, "old.ogg", "new.ogg", "changed"),
    }
    assert sorted(
        path.name for path in basic.iterdir() if path.is_dir()
    ) == sorted(expectations)

    for case, (bay_key, old_name, new_name, change) in expectations.items():
        left = (
            None if old_name is None else (basic / case / old_name).read_bytes()
        )
        right = (
            None if new_name is None else (basic / case / new_name).read_bytes()
        )
        context = ComposeContext.build(
            left_path=None if old_name is None else f"{case}/{old_name}",
            right_path=None if new_name is None else f"{case}/{new_name}",
            left_label="old",
            right_label="new",
            renderer=engine("dirdiff"),
        )
        bays = Composer().compose(left, right, context)["frames"][0]["bays"]
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
