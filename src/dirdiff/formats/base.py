from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from dirdiff.change_source import CapturedLink
from dirdiff.engines import (
    DiffEngineProtocol,
    DiffSide,
    DiffSummary,
    EngineWarning,
)
from dirdiff.rendering import (
    DiffRow,
    FoldHint,
    enrich_rows_for_display,
)
from dirdiff.util import Err, Nothing, Ok, Option, Result, Some

__all__ = [
    "BLOB_BAY_KEY",
    "FLATFILE_BAY_KEY",
    "IMAGE_BAY_KEY",
    "IMAGE_FACTS_BAY_KEY",
    "IMAGE_METADATA_BAY_KEY",
    "MEDIA_FACTS_PATH_HINT",
    "Bay",
    "BayChange",
    "BayContext",
    "BayKindPayload",
    "BayPayload",
    "BayWarning",
    "ChangeStatus",
    "ComposeContext",
    "ComposedFilePayload",
    "ComposedSummary",
    "FramePayload",
    "ImageBay",
    "MediaSide",
    "MovedChangeStatus",
    "TextBay",
    "byte_facts_bay",
    "image_kind_payload",
    "media_facts",
    "media_ref",
    "text_kind_payload",
    "try_decode_text",
    "whole_file_change",
]


class ChangeStatus(TypedDict):
    kind: Literal["added", "removed", "changed", "unchanged"]


class MovedChangeStatus(TypedDict):
    kind: Literal["moved"]
    from_heading: str | None
    to_heading: str | None


BayChange = ChangeStatus | MovedChangeStatus


FLATFILE_BAY_KEY = "flatfile"


IMAGE_BAY_KEY = "image"


IMAGE_FACTS_BAY_KEY = "image-facts"


IMAGE_METADATA_BAY_KEY = "image-metadata"


BLOB_BAY_KEY = "blob"

BLOB_MEDIA_TYPE = "application/octet-stream"


def whole_file_change[Content](
    left: Option[Content],
    right: Option[Content],
) -> BayChange:
    if not Some.present(left):
        return ChangeStatus(kind="added")
    if not Some.present(right):
        return ChangeStatus(kind="removed")
    if left == right:
        return ChangeStatus(kind="unchanged")
    return ChangeStatus(kind="changed")


@dataclass(frozen=True)
class BayContext:
    left_path: Option[str]
    right_path: Option[str]
    left_label: str
    right_label: str
    left_link: Option[CapturedLink] = field(default_factory=Nothing)
    right_link: Option[CapturedLink] = field(default_factory=Nothing)


@dataclass(frozen=True)
class ComposeContext:
    bays: BayContext
    renderer: DiffEngineProtocol

    @classmethod
    def build(
        cls,
        *,
        left_path: Option[str],
        right_path: Option[str],
        left_label: str,
        right_label: str,
        left_link: Option[CapturedLink] = Nothing(),
        right_link: Option[CapturedLink] = Nothing(),
        renderer: DiffEngineProtocol,
    ) -> ComposeContext:
        # One nested BayContext keeps path, label, and link inputs shared with
        # engine-free bay inspection instead of duplicating them on the context.
        return cls(
            bays=BayContext(
                left_path=left_path,
                right_path=right_path,
                left_label=left_label,
                right_label=right_label,
                left_link=left_link,
                right_link=right_link,
            ),
            renderer=renderer,
        )


@dataclass(frozen=True)
class TextBay:
    frame_key: str
    heading: str | None
    bay_key: str
    label: str
    detail: str | None
    collapsible: bool
    default_expanded: bool
    change: BayChange
    left_label: str
    right_label: str
    left: DiffSide
    right: DiffSide
    warnings: tuple[BayWarning, ...] = ()


@dataclass(frozen=True)
class MediaSide:
    media_type: str
    data: bytes


@dataclass(frozen=True)
class ImageBay:
    frame_key: str
    heading: str | None
    bay_key: str
    label: str
    detail: str | None
    collapsible: bool
    default_expanded: bool
    change: BayChange
    left: Option[MediaSide]
    right: Option[MediaSide]
    warnings: tuple[BayWarning, ...] = ()


Bay = TextBay | ImageBay


class BayWarning(TypedDict):
    type: str
    message: str


class MediaRef(TypedDict):
    media_type: str
    byte_size: int
    digest: str


class TextKindPayload(TypedDict):
    kind: Literal["text"]
    left_label: str
    right_label: str
    rows: list[DiffRow]
    fold_hints: list[FoldHint]
    stats: DiffSummary
    interleave_inline_rows: bool


class ImageKindPayload(TypedDict):
    kind: Literal["image"]
    left: MediaRef | None
    right: MediaRef | None


BayKindPayload = TextKindPayload | ImageKindPayload


class BayPayload(TypedDict):
    bay_key: str
    label: str
    detail: str | None
    collapsible: bool
    default_expanded: bool
    change: BayChange
    warnings: list[BayWarning]
    kind_data: BayKindPayload


class FramePayload(TypedDict):
    frame_key: str
    heading: str | None
    bays: list[BayPayload]


class ComposedSummary(DiffSummary):
    left_exists: bool
    right_exists: bool


class ComposedFilePayload(TypedDict):
    left_label: str
    right_label: str
    left_path: str | None
    right_path: str | None
    summary: ComposedSummary
    default_expanded: bool
    frames: list[FramePayload]


@dataclass(frozen=True)
class TextRejection:
    reason: Literal["nul-byte", "invalid-utf8"]
    detail: str


def try_decode_text(data: bytes) -> Result[str, TextRejection]:
    nul_offset = data.find(b"\x00")
    if nul_offset >= 0:
        return Err(
            TextRejection(
                reason="nul-byte",
                detail=f"NUL byte at byte {nul_offset}",
            )
        )
    try:
        return Ok(data.decode("utf-8-sig"))
    except UnicodeDecodeError as error:
        return Err(
            TextRejection(
                reason="invalid-utf8",
                detail=f"invalid UTF-8 at byte {error.start}",
            )
        )


def media_ref(side: MediaSide) -> MediaRef:
    return {
        "media_type": side.media_type,
        "byte_size": len(side.data),
        "digest": hashlib.sha256(side.data).hexdigest(),
    }


MEDIA_FACTS_PATH_HINT = "media"


def media_facts(side: MediaSide) -> str:
    ref = media_ref(side)
    return (
        f"type: {ref['media_type']}\n"
        f"size: {ref['byte_size']} bytes\n"
        f"sha256: {ref['digest']}"
    )


def byte_facts_bay(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
    *,
    left_media_type: Option[str],
    right_media_type: Option[str],
    warnings: tuple[BayWarning, ...],
) -> TextBay:
    left_facts = left.map(
        lambda data: MediaSide(
            media_type=left_media_type.get_or(BLOB_MEDIA_TYPE), data=data
        )
    ).map(media_facts)
    right_facts = right.map(
        lambda data: MediaSide(
            media_type=right_media_type.get_or(BLOB_MEDIA_TYPE), data=data
        )
    ).map(media_facts)
    return TextBay(
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
            exists=Some.present(left),
            text=left_facts.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        right=DiffSide(
            exists=Some.present(right),
            text=right_facts.get(),
            path_hint=MEDIA_FACTS_PATH_HINT,
        ),
        warnings=warnings,
    )


def image_kind_payload(bay: ImageBay) -> ImageKindPayload:
    return {
        "kind": "image",
        "left": bay.left.get_map(media_ref),
        "right": bay.right.get_map(media_ref),
    }


def text_kind_payload(
    bay: TextBay,
    renderer: DiffEngineProtocol,
) -> tuple[TextKindPayload, EngineWarning | None]:
    rendered = renderer.render_diff(old=bay.left, new=bay.right)
    left_text = "" if bay.left.text is None else bay.left.text
    right_text = "" if bay.right.text is None else bay.right.text
    display = enrich_rows_for_display(
        rows=rendered["rows"],
        left_text=left_text,
        right_text=right_text,
        left_path_hint=bay.left.path_hint,
        right_path_hint=bay.right.path_hint,
    )
    return (
        {
            "kind": "text",
            "left_label": bay.left_label,
            "right_label": bay.right_label,
            "rows": display["rows"],
            "fold_hints": display.get("fold_hints", []),
            "stats": rendered["summary"],
            "interleave_inline_rows": rendered["interleave_inline_rows"],
        },
        rendered.get("engine_warning"),
    )
