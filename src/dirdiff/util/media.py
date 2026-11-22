from __future__ import annotations

from io import BytesIO

__all__ = ["image_media_type"]


def image_media_type(data: bytes) -> str | None:
    """The media type of the image the bytes hold, or None when Pillow does not
    recognise them as an image. Only the header is read, so the image may still
    fail to decode. Which types are acceptable is the caller's policy.
    """
    # Every CLI command imports this package; Pillow loads on first use.
    from PIL import Image  # noqa: PLC0415

    try:
        with Image.open(BytesIO(data)) as image:
            return image.get_format_mimetype()
    except (
        OSError,
        SyntaxError,
        ValueError,
        Image.DecompressionBombError,
    ):
        return None
