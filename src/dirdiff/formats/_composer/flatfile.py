from __future__ import annotations

from collections.abc import Iterator

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    FLATFILE_BAY_KEY,
    BayContext,
    TextBay,
    byte_facts_bay,
    try_decode_text,
    whole_file_change,
)
from dirdiff.util import Nothing, Ok, Option, Some

__all__ = [
    "flatfile_bays",
]


def flatfile_bays(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
) -> Iterator[TextBay]:
    # Nothing means not captured on that side (added/removed File); a captured
    # empty File decodes to "" and is present.
    left_text = left.map(try_decode_text)
    right_text = right.map(try_decode_text)
    rejections = [
        value.value.error
        for value in (left_text, right_text)
        if Some.present(value) and not Ok.good(value.value)
    ]
    # One rejected side turns the whole File into byte facts, never partial
    # text.
    if len(rejections) > 0:
        yield byte_facts_bay(
            left,
            right,
            context,
            left_media_type=Nothing(),
            right_media_type=Nothing(),
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
    # Nullable extraction is the boundary to the engine's DiffSide contract.
    left_decoded: Option[str] = Nothing()
    right_decoded: Option[str] = Nothing()
    if Some.present(left_text):
        assert Ok.good(left_text.value)
        left_decoded = Some(left_text.value.value)
    if Some.present(right_text):
        assert Ok.good(right_text.value)
        right_decoded = Some(right_text.value.value)
    left_engine_text = left_decoded.get()
    right_engine_text = right_decoded.get()
    change = whole_file_change(left_decoded, right_decoded)
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
            exists=Some.present(left_text),
            text=left_engine_text,
            path_hint=context.left_path.get(),
        ),
        right=DiffSide(
            exists=Some.present(right_text),
            text=right_engine_text,
            path_hint=context.right_path.get(),
        ),
    )
