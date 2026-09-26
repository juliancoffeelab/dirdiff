"""Composition of captured bytes that dirdiff presents as opaque content.

## Public interface

`blob_media_type` supplies the deliberately nonspecific media classification.
`blob_bays` represents each present side through the shared media facts and
yields one ordinary text bay, so size, type, and digest remain reviewable.

## Purpose and boundaries

Opaque content still needs a readable and commentable representation. This
module states captured byte facts as text without sniffing or decoding the
payload. `Composer` decides when this terminal applies and later renders the
resulting text bay.
"""

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
"""Known suffixes that classify otherwise unreadable Files as opaque blobs.

Classification consults this immutable table after notebook and image claims,
but before presumed text. Values become displayed media facts; no entry
authorizes decoding, previewing, or content sniffing.
"""


def blob_media_type(path: str) -> Option[str]:
    """Return the suffix-declared media type used in opaque blob facts.

    Matching is case-insensitive and tests the complete configured suffixes.
    An unlisted path returns `Nothing`; callers preserve that unknown fact
    instead of guessing from bytes or a shorter extension.

    # Usage

    `Composer.bays` calls this during path classification. Pass its result to
    `blob_bays`; do not use it to decide whether captured bytes decode as text.

    # Returns

    - `Some[str]`: The configured media type for the path's matching listed suffix.
    - `Nothing`: The path has no listed blob suffix. The caller must
      continue format classification rather than guess from the bytes.
    """
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
    """Yield the single bay a blob File composes into.

    Takes the raw captured bytes because that is all a blob File is, and states
    the facts about them as text. Both sides are optional under the whole-File
    rule: `Nothing` is a side the File was not captured on, and `change` is read
    from the stated facts. Equal bytes with different path-declared media types
    therefore remain `changed`; identical facts are `unchanged` and take no
    navigation stop.

    The context supplies the two side headings the grid is written under, the
    same as any other text bay. It supplies no path hint: the facts are not
    source in any language, so both sides carry `MEDIA_FACTS_PATH_HINT`.

    # Parameters

    - `left`: Captured old bytes, or `Nothing` when absent.
    - `right`: Captured new bytes under the same convention.
    - `context`: Side labels used by the facts text bay.
    - `left_media_type`: Old path's declared type, or `Nothing` when classification
      knows nothing more specific than `application/octet-stream`.
    - `right_media_type`: New path's declared type under the same rule.
    - `warnings`: Existing classification or decode warnings attached unchanged
      to the resulting bay.

    # Usage

    `Composer.bays` calls this after a blob suffix claim or a failed presumed
    text decode. Preserve existing warnings so the resulting bay states why it
    was represented as byte facts.

    # Returns

    - `Yielded bay`: One byte-facts text bay whose sides state media type, size,
      and digest for each captured byte side.
    - `Identity and warnings`: The bay uses `BLOB_BAY_KEY`, preserves absent
      sides, and carries the supplied warnings unchanged.
    """
    yield byte_facts_bay(
        left,
        right,
        context,
        left_media_type=left_media_type,
        right_media_type=right_media_type,
        warnings=warnings,
    )
