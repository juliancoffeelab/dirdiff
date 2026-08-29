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

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    BLOB_BAY_KEY,
    MEDIA_FACTS_PATH_HINT,
    BayContext,
    BayWarning,
    MediaSide,
    TextBay,
    media_facts,
    whole_file_change,
)

__all__ = [
    "blob_bays",
    "blob_media_type",
]


BLOB_MEDIA_TYPE = "application/octet-stream"
"""What this project says about the media type of content it cannot read.

The honest answer is that it does not know, and `application/octet-stream` is
how a media type spells that. It is the media type the facts bay states for both
sides, which is the row a reviewer reads as "nothing here understood this".
"""


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


def blob_media_type(path: str | None) -> str | None:
    """Return the suffix-declared media type used in opaque blob facts.

    Matching is case-insensitive and tests the complete configured suffixes.
    `None` or an unlisted path returns `None`; callers preserve that unknown fact
    instead of guessing from bytes or a shorter extension.

    # Usage

    `Composer.bays` calls this during path classification. Pass its result to
    `blob_bays`; do not use it to decide whether captured bytes decode as text.

    # Returns

    - `str`: The configured media type for the path's matching listed suffix.
    - `None`: The path is absent or has no listed blob suffix. The caller must
      continue format classification rather than guess from the bytes.
    """
    if path is None:
        return None
    lowered = path.lower()
    for suffix, media_type in _BLOB_MEDIA_TYPES.items():
        if lowered.endswith(suffix):
            return media_type
    return None


def blob_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
    *,
    left_media_type: str | None,
    right_media_type: str | None,
    warnings: tuple[BayWarning, ...],
) -> Iterator[TextBay]:
    """Yield the single bay a blob File composes into.

    Takes the raw captured bytes because that is all a blob File is, and states
    the facts about them as text. Both sides are optional under the whole-File
    rule: `None` is a side the File was not captured on, and `change` is read
    from the stated facts. Equal bytes with different path-declared media types
    therefore remain `changed`; identical facts are `unchanged` and take no
    navigation stop.

    The context supplies the two side headings the grid is written under, the
    same as any other text bay. It supplies no path hint: the facts are not
    source in any language, so both sides carry `MEDIA_FACTS_PATH_HINT`.

    # Parameters

    - `left`: Captured old bytes, or `None` when absent.
    - `right`: Captured new bytes under the same convention.
    - `context`: Side labels used by the facts text bay.
    - `left_media_type`: Old path's declared type, or `None` when classification
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
    left_facts = media_facts(
        None
        if left is None
        else MediaSide(
            media_type=left_media_type or BLOB_MEDIA_TYPE,
            data=left,
        )
    )
    right_facts = media_facts(
        None
        if right is None
        else MediaSide(
            media_type=right_media_type or BLOB_MEDIA_TYPE,
            data=right,
        )
    )
    yield TextBay(
        # One frame, and nothing to name above a File that is entirely one
        # opaque payload, so the frame is keyed "file" and carries no heading,
        # exactly as a flatfile's does.
        frame_key="file",
        heading=None,
        bay_key=BLOB_BAY_KEY,
        label="Binary content",
        detail=None,
        collapsible=False,
        default_expanded=True,
        change=whole_file_change(left_facts, right_facts),
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left is not None,
            text=left_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=right is not None,
            text=right_facts,
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        warnings=warnings,
    )
