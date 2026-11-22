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
from dirdiff.util import Nothing, Option, Some, image_media_type, maybe

__all__ = [
    "displayable_image_type",
    "image_bays",
]


# Only formats browsers display natively. SVG is excluded on purpose: it is
# author-controlled markup (a script-execution risk) and reads fine as text.
# Pillow has no SVG reader, so `image_media_type` never names it.
_IMAGE_MEDIA_TYPES = frozenset(
    {
        "image/apng",
        "image/avif",
        "image/bmp",
        "image/gif",
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/x-icon",
    }
)


def displayable_image_type(data: bytes) -> Option[str]:
    return maybe(image_media_type(data)).and_then(
        lambda media_type: Some.when(
            media_type in _IMAGE_MEDIA_TYPES, media_type
        )
    )


def image_bays(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
    *,
    left_media_type: Option[str],
    right_media_type: Option[str],
) -> Iterator[Bay]:
    def metadata(
        side: Option[MediaSide],
        side_label: str,
    ) -> tuple[Option[str], tuple[BayWarning, ...]]:
        if not Some.present(side):
            return Nothing(), ()
        try:
            with Image.open(BytesIO(side.value.data)) as image:
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
                return Some("\n".join(rows)), tuple(warnings)
        except (
            OSError,
            SyntaxError,
            ValueError,
            Image.DecompressionBombError,
        ) as error:
            return Some(""), (
                {
                    "type": "image_decode_failed",
                    "message": (
                        f"{side_label} image metadata could not be read: "
                        f"{error}."
                    ),
                },
            )

    left_side = left.map(
        lambda data: MediaSide(media_type=left_media_type.must(), data=data)
    )
    right_side = right.map(
        lambda data: MediaSide(media_type=right_media_type.must(), data=data)
    )
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
            exists=Some.present(left_side),
            text=left_metadata.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=Some.present(right_side),
            text=right_metadata.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        warnings=(*left_warnings, *right_warnings),
    )

    left_facts = left_side.map(media_facts)
    right_facts = right_side.map(media_facts)
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
            exists=Some.present(left_side),
            text=left_facts.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=Some.present(right_side),
            text=right_facts.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
    )
