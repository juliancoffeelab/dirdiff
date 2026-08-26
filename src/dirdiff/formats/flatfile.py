"""The flatfile format: a File with no internal structure to decompose.

A flatfile is a File that no other format claims and that decodes as text —
source code, configuration, prose, anything dirdiff renders as its own text
rather than interpreting. By volume it is the ordinary case: most Files in most
reviews are flatfiles. It is not the terminal of `composer.py`'s ordered
classification; `blob.py` is, and it takes the content that does not decode.

The name states a fact about the File rather than a judgement about it. A
flatfile has no parts, so composition has nothing to split: it produces one
heading-less frame holding one bay whose text is the whole File. That single
bay is what makes `bay_key` a total coordinate — every File composes at
least one bay, so a review target or line pin always names one, and no
consumer needs a "this file has no bays" branch.

Public interface: `flatfile_bays()`, which yields that one bay.

`FLATFILE_BAY_KEY` lives in `base.py` rather than here, because consumers far
outside this package — review targets, line pins, the agent API — compare
against it, and they must not import a format module to do so.

What this module does not own: classification. It receives bytes only after
`composer.py` has selected presumed text, decodes each side once, and degrades
the whole File to blob facts with a warning when either side is not project
text. It owns no engine: rendering decoded sides is
`text_kind_payload()`'s job.
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

    `None` on a side means the File was not captured there — that is how an
    added or removed File is expressed. A captured empty File decodes to `""`
    and is a present side, not an absent one.

    The bay is never collapsible, and its `change` follows the whole-File rule:
    a flatfile has no fact beyond its own text and no positions to move within.
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
