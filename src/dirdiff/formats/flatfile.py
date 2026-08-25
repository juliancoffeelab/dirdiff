"""The flatfile format: a File with no internal structure to decompose.

A flatfile is a File that no other format claims — source code, configuration,
prose, anything dirdiff renders as its own text rather than interpreting. It is
the terminal of `composer.py`'s ordered classification, and by volume it is the
ordinary case: most Files in most reviews are flatfiles.

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

What this module does not own: classification. It never decides whether a File
is a flatfile; `composer.py` does, and calls this module once that answer is
final. It also owns no engine — the sides it yields are decoded text, and
rendering them is `render_text_bay()`'s job.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.backend import decode_text_content
from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    FLATFILE_BAY_KEY,
    BayChange,
    BayContext,
    ChangeStatus,
    TextBay,
)

__all__ = [
    "flatfile_bays",
]


def flatfile_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
) -> Iterator[TextBay]:
    """Yield the single bay a flatfile composes into.

    Both sides are decoded here, because a flatfile *is* its decoded text: a
    binary or non-UTF-8 side raises `DirdiffError` at this boundary, which the
    request handler reports as an unsupported file diff. An absent side is
    absent, not empty — that is how an added or removed File is expressed.

    The bay is never collapsible. Its `change` is read from the two decoded
    texts rather than from rendered rows, because a flatfile has no fact beyond
    its own text: one side absent is an addition or a removal, identical text is
    `unchanged`, and anything else is `changed`. A flatfile is never `moved`,
    which is a position within a document and a flatfile has no positions.
    """
    left_text = (
        None
        if left is None
        else decode_text_content(
            left, label=f"{context.left_label}:{context.left_path}"
        )
    )
    right_text = (
        None
        if right is None
        else decode_text_content(
            right, label=f"{context.right_label}:{context.right_path}"
        )
    )
    change: BayChange
    if left_text is None:
        change = ChangeStatus(kind="added")
    elif right_text is None:
        change = ChangeStatus(kind="removed")
    elif left_text == right_text:
        change = ChangeStatus(kind="unchanged")
    else:
        change = ChangeStatus(kind="changed")
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
            exists=left is not None, text=left_text, path_hint=context.left_path
        ),
        right=DiffSide(
            exists=right is not None,
            text=right_text,
            path_hint=context.right_path,
        ),
    )
