"""Composition of browser-displayable image Files.

## Public interface

`image_media_type` recognizes the supported path extensions. `image_bays`
yields the picture bytes, parsed dimensions and EXIF as text, and the shared
byte facts in a stable order. A metadata decode failure is contained to the
metadata bay and reported there.

## Purpose and boundaries

The picture, its parsed metadata, and its byte identity answer different review
questions, so the builder emits separate bays for them. Pillow reads metadata
only. The exact captured image bytes remain unchanged for browser display, and
`Composer` reduces them to references before serialization.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from io import BytesIO

from PIL import ExifTags, Image

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
    IMAGE_METADATA_BAY_KEY,
    MEDIA_FACTS_PATH_HINT,
    Bay,
    BayContext,
    BayWarning,
    ImageBay,
    MediaSide,
    TextBay,
    media_facts,
    whole_file_change,
)

__all__ = [
    "image_bays",
    "image_media_type",
]


_IMAGE_MEDIA_TYPES = {
    ".apng": "image/apng",
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
"""The extensions this project treats as images, and what each one is.

Deliberately a closed list of what browsers display natively without a decoder
of ours. `image/svg+xml` is absent and stays absent: an SVG is
author-controlled markup that the frontend would have to execute to display,
which is the security question `formats.md` records for HTML outputs, and it
is also perfectly readable as text. A flatfile diff of an SVG tells a reviewer
more than a picture of it does.
"""


def image_media_type(path: str | None) -> str | None:
    """Return the media type this path claims to be, or `None` if not an image.

    Classification is by filename extension, matched case-insensitively. That is
    what the repository asserts the file is, and for a review tool showing a
    reviewer what a repository contains it is the honest answer: a `.png` whose
    bytes are a JPEG is a fact about the repository, not about this function.

    An absent path is not an image, which is how a side the File does not have
    answers.

    # Usage

    `Composer.bays` calls this for each present path. Pass a returned media type
    to `image_bays`; `None` leaves the path available to later classifiers.

    # Returns

    - `str`: The browser-native media type declared by the path's image suffix.
    - `None`: The path is absent or has no supported image suffix. The caller
      must continue format classification.
    """
    # TODO: extension matching is the simple answer and it is wrong for a file
    # with no extension, a file whose extension lies, and every format not on
    # the list above. Reading the leading magic bytes would settle the first
    # two, and a third-party library such as `filetype` or `puremagic` would
    # settle all three. Neither is worth a dependency until a real review runs
    # into it; when one does, this function is the only thing that changes.
    if path is None:
        return None
    lowered = path.lower()
    for suffix, media_type in _IMAGE_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media_type
    return None


def image_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
    *,
    left_media_type: str | None,
    right_media_type: str | None,
) -> Iterator[Bay]:
    """Yield the picture, parsed metadata, and byte-facts bays for an image File.

    The two media types are the ones classification already derived from the two
    paths, passed in rather than derived again, so what was classified and what
    is served cannot disagree. A present side requires its media type: a side
    whose path did not name an image is not an image File, and classification
    must not have called this.

    All three bays follow the whole-File rule for `change`, and read it from
    different content on purpose. The picture's is read from the exact bytes, so
    two byte-identical sides are `unchanged` and the bay takes no navigation
    stop. The facts bay derives its change from the facts text, which changes
    whenever the content changes. The digest is total. This result is also more
    accurate for a rename that changes only the declared media type. The type
    row reads as changed while the picture reads unchanged.

    # Parameters

    - `left`: Captured old image bytes, or `None` for an absent side.
    - `right`: Captured new image bytes under the same convention.
    - `context`: Paths and labels for the resulting image and text bays.
    - `left_media_type`: Type already concluded from the old path. Required
      whenever `left` is present.
    - `right_media_type`: Type already concluded from the new path. Required
      whenever `right` is present.

    # Usage

    `Composer.bays` calls this only after every present path has an image media
    type. Iterate all three bays in order; media serving uses the first bay's
    exact bytes, while composition renders the two text attachments.

    # Returns

    - `Yielded bays`: The picture bay, metadata text bay, and byte-facts text bay
      all describe the same two captured sides.
    - `Order`: Picture comes first for media serving, followed by metadata and
      byte facts for ordinary text rendering.

    # Failures

    Raises `AssertionError` when a present byte side lacks its classified media
    type. Expected Pillow and EXIF damage is returned as bay warnings rather
    than raised.
    """

    def metadata(
        side: MediaSide | None,
        side_label: str,
    ) -> tuple[str | None, tuple[BayWarning, ...]]:
        """Read bounded dimensions and EXIF text for one captured image side.

        Failure to open or read the image returns an empty text side with a
        visible warning, damaging this attachment only. An individual EXIF
        value that cannot be rendered is omitted with a warning scoped to that
        value. Oversized EXIF values become their length and digest rather than
        entering the diff verbatim.

        # Parameters

        - `side`: Captured bytes and declared type, or `None` when absent.
        - `side_label`: Reviewer-facing side name used in warning messages.

        # Usage

        `image_bays` calls this once per side before constructing the metadata
        text bay. Preserve returned warnings beside the text from that side.

        # Returns

        - `First`: Dimension and EXIF rows for a readable side; an unreadable
          present image returns `""` and a warning.
        - `None`: The first item is absent when the File has no side here, so the
          metadata bay must preserve side absence.
        - `Second`: Warnings for unreadable metadata or omitted EXIF values, in
          discovery order.
        """
        if side is None:
            return None, ()
        try:
            with Image.open(BytesIO(side.data)) as image:
                rows = [
                    f"width: {image.width} px",
                    f"height: {image.height} px",
                ]
                warnings: list[BayWarning] = []
                # PNG's override decodes the complete raster when no early EXIF
                # chunk exists. The base implementation reads only metadata
                # already available from opening the container.
                exif = Image.Image.getexif(image)
                for tag_id, value in sorted(
                    exif.items(),
                    key=lambda item: ExifTags.TAGS.get(item[0], str(item[0])),
                ):
                    tag = ExifTags.TAGS.get(tag_id, str(tag_id))
                    try:
                        if isinstance(value, bytes) and len(value) > 256:
                            rendered = (
                                f"<{len(value)} bytes, sha256 "
                                f"{hashlib.sha256(value).hexdigest()}>"
                            )
                        else:
                            rendered = str(value)
                            if len(rendered) > 256:
                                rendered = (
                                    f"<{len(rendered)} characters, sha256 "
                                    f"{hashlib.sha256(rendered.encode()).hexdigest()}>"
                                )
                    except (TypeError, ValueError) as error:
                        warnings.append(
                            {
                                "type": "image_invalid_exif_value",
                                "message": (
                                    f"{side_label} EXIF {tag} could not be "
                                    f"shown: {error}."
                                ),
                            }
                        )
                        continue
                    rows.append(f"exif:{tag}: {rendered}")
                return "\n".join(rows), tuple(warnings)
        except (
            OSError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombError,
        ) as error:
            return "", (
                {
                    "type": "image_decode_failed",
                    "message": (
                        f"{side_label} image metadata could not be read: "
                        f"{error}."
                    ),
                },
            )

    left_side: MediaSide | None = None
    if left is not None:
        assert left_media_type is not None, (
            "an image File's captured left side must have a media type"
        )
        left_side = MediaSide(media_type=left_media_type, data=left)
    right_side: MediaSide | None = None
    if right is not None:
        assert right_media_type is not None, (
            "an image File's captured right side must have a media type"
        )
        right_side = MediaSide(media_type=right_media_type, data=right)

    # One frame, and nothing to name above a File that is entirely one picture,
    # so both bays are keyed "file" and carry no heading, exactly as a
    # flatfile's one bay does.
    yield ImageBay(
        frame_key="file",
        heading=None,
        bay_key=IMAGE_BAY_KEY,
        label="Image",
        detail=None,
        collapsible=False,
        default_expanded=True,
        change=whole_file_change(left, right),
        left=left_side,
        right=right_side,
    )

    # Then show metadata, like things from EXIF
    left_metadata, left_warnings = metadata(left_side, context.left_label)
    right_metadata, right_warnings = metadata(right_side, context.right_label)
    yield TextBay(
        frame_key="file",
        heading=None,
        bay_key=IMAGE_METADATA_BAY_KEY,
        label="Image metadata",
        detail=None,
        collapsible=True,
        default_expanded=True,
        change=whole_file_change(left_metadata, right_metadata),
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left_side is not None,
            text=left_metadata,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=right_side is not None,
            text=right_metadata,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        warnings=(*left_warnings, *right_warnings),
    )

    # And finally, generic file data, like file size
    left_facts = media_facts(left_side)
    right_facts = media_facts(right_side)
    yield TextBay(
        frame_key="file",
        heading=None,
        bay_key=IMAGE_FACTS_BAY_KEY,
        label="Media facts",
        detail=None,
        collapsible=True,
        default_expanded=True,
        change=whole_file_change(left_facts, right_facts),
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left_side is not None,
            text=left_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=right_side is not None,
            text=right_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
    )
