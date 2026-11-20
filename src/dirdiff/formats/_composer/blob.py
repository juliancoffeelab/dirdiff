from __future__ import annotations

from collections.abc import Iterator

from dirdiff.formats.base import (
    BayContext,
    BayWarning,
    TextBay,
    byte_facts_bay,
)
from dirdiff.util import Nothing, Option, Some

__all__ = [
    "blob_bays",
    "blob_media_type",
]


# Consulted after notebook and image claims, before presumed text. Values are
# displayed facts only; nothing here authorizes decoding or content sniffing.
_BLOB_MEDIA_TYPES = {
    ".7z": "application/x-7z-compressed",
    ".bz2": "application/x-bzip2",
    ".db": "application/vnd.sqlite3",
    ".gz": "application/gzip",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".ogg": "audio/ogg",
    ".otf": "font/otf",
    ".pdf": "application/pdf",
    ".sqlite": "application/vnd.sqlite3",
    ".ttf": "font/ttf",
    ".wasm": "application/wasm",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".xz": "application/x-xz",
    ".zip": "application/zip",
}


def blob_media_type(path: str) -> Option[str]:
    lowered = path.lower()
    for suffix, media_type in _BLOB_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return Some(media_type)
    return Nothing()


def blob_bays(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
    *,
    left_media_type: Option[str],
    right_media_type: Option[str],
    warnings: tuple[BayWarning, ...],
) -> Iterator[TextBay]:
    # Absent sides stay absent. Equal bytes with different declared media types
    # still read as changed because the stated facts differ.
    yield byte_facts_bay(
        left,
        right,
        context,
        left_media_type=left_media_type,
        right_media_type=right_media_type,
        warnings=warnings,
    )
