"""The blob terminal: content this project has decided it cannot read.

Every File that no format claims and that does not decode as text ends here, and
that is what makes classification total: `composer.py` always reaches an answer,
so no File can arrive at the frontend as an error where a diff was expected. A
blob File used to raise at the text decode boundary and become an error
`LazyFile`; it now becomes an ordinary composed diff.

Blob is a File classification, not a bay kind. What can honestly be shown for
unreadable bytes is each side's media type, size, and digest, in the spirit of
what `git diff` prints for a binary file — and those are lines of text. So the
one bay a blob File composes is a `text` bay holding them, and the reviewer gets
a real diff of the facts rather than one undiffed line to eyeball twice: the
digest row changed, the size row changed, the media type row did not. A comment
can land on the digest specifically, because it is a real line.

Public interface: `blob_bays()`, which yields that one bay.

What this module does not own: guessing. It never sniffs the bytes, never names
a media type more specific than "unknown", and never tries to find a textual
representation — the preceding classification steps already decided this content
is none of those things, and re-litigating that here would put two answers in
the codebase. It also owns no bytes on the wire: a blob File composes no bay
that carries any, so the media endpoint has nothing here to serve.
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
"""Extensions dirdiff explicitly presents as opaque byte facts."""


def blob_media_type(path: str | None) -> str | None:
    """Return the declared blob media type for `path`, if explicitly known."""
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
    from the stated facts, so two byte-identical sides state identical facts,
    are `unchanged`, and take no navigation stop.

    The context supplies the two side headings the grid is written under, the
    same as any other text bay. It supplies no path hint: the facts are not
    source in any language, so both sides carry `MEDIA_FACTS_PATH_HINT`.
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
