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

What this module does not own: classification, or decoding. It never decides
whether a File is a flatfile; `composer.py` does, by decoding the two sides, and
calls this module with the text it already has. It also owns no engine — the
sides it yields are that decoded text, and rendering them is
`text_kind_payload()`'s job.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    FLATFILE_BAY_KEY,
    BayContext,
    TextBay,
    whole_file_change,
)

__all__ = [
    "flatfile_bays",
]


def flatfile_bays(
    left_text: str | None,
    right_text: str | None,
    context: BayContext,
) -> Iterator[TextBay]:
    """Yield the single bay a flatfile composes into.

    Both sides arrive already decoded, because deciding that a File *is* a
    flatfile means decoding it: classification decodes once, hands the result
    here, and sends content that did not decode to the blob terminal instead.
    Nothing is decoded twice and this builder cannot be handed content that is
    not text.

    `None` on a side means the File was not captured there — that is how an
    added or removed File is expressed. A captured empty File decodes to `""`
    and is a present side, not an absent one.

    The bay is never collapsible, and its `change` follows the whole-File rule:
    a flatfile has no fact beyond its own text and no positions to move within.
    """
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
