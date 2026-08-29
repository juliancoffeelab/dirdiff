"""Composition of a File with no internal frame structure.

## Public interface

`flatfile_bays` decodes the supplied sides as project text and yields one
heading-less frame with one text bay. If either present side violates the text
contract, the complete File is represented by blob facts with a visible
warning instead of partial text.

## Purpose and boundaries

A File without format-specific structure still enters the same frame-and-bay
pipeline as notebooks and images. `Composer` selects this builder after path
classification; the shared text-bay operation renders its decoded sides later.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    FLATFILE_BAY_KEY,
    BayContext,
    TextBay,
    TextRejection,
    try_decode_text,
    whole_file_change,
)
from dirdiff.formats.blob import blob_bays

__all__ = [
    "flatfile_bays",
]


def flatfile_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
) -> Iterator[TextBay]:
    """Yield the single bay a flatfile composes into.

    Both sides arrive as exact bytes after path classification presumed text.
    This builder decodes each once. If either present side rejects the text
    contract, it yields blob facts for both sides with the rejection warning.

    `None` on a side means the File was not captured there. That is how an
    added or removed File is expressed. A captured empty File decodes to `""`
    and is a present side, not an absent one.

    The bay is never collapsible, and its `change` follows the whole-File rule:
    a flatfile has no fact beyond its own text and no positions to move within.

    # Parameters

    - `left`: Captured old bytes, or `None` when the File is new.
    - `right`: Captured new bytes, or `None` when the File was removed.
    - `context`: File paths and side labels copied into the resulting bay.

    # Usage

    `Composer.bays` calls this after path classification chooses presumed text.
    Iterate the result once; a decode rejection yields a blob-facts bay instead
    of a partial text bay.

    # Returns

    - `Accepted text`: One decoded whole-File bay with `FLATFILE_BAY_KEY` and no
      format warning.
    - `Rejected text`: One byte-facts bay containing warnings for every present
      side that violated the text boundary.

    # Failures

    Asserts if rejection narrowing leaves a `TextRejection` on the text path;
    input decode rejection itself yields blob facts and warnings.
    """
    left_text = None if left is None else try_decode_text(left)
    right_text = None if right is None else try_decode_text(right)
    rejections = [
        value
        for value in (left_text, right_text)
        if isinstance(value, TextRejection)
    ]
    if len(rejections) > 0:
        yield from blob_bays(
            left,
            right,
            context,
            left_media_type=None,
            right_media_type=None,
            warnings=tuple(
                {
                    "type": rejection.reason.replace("-", "_"),
                    "message": (
                        "Presumed text shown as byte facts: "
                        f"{rejection.detail}."
                    ),
                }
                for rejection in rejections
            ),
        )
        return
    assert not isinstance(left_text, TextRejection)
    assert not isinstance(right_text, TextRejection)
    change = whole_file_change(left_text, right_text)
    yield TextBay(
        # One frame, and nothing to name above a File that is entirely its own
        # text, so the frame is keyed "file" and carries no heading. "Code" is
        # what the inline view's single content column has always read.
        frame_key="file",
        heading=None,
        bay_key=FLATFILE_BAY_KEY,
        label="Code",
        detail=None,
        collapsible=False,
        default_expanded=True,
        change=change,
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left_text is not None,
            text=left_text,
            path_hint=context.left_path,
        ),
        right=DiffSide(
            exists=right_text is not None,
            text=right_text,
            path_hint=context.right_path,
        ),
    )
