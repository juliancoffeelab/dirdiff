"""Composition of captured Files into frames and bays.

Import `Composer`, its contexts and results, bay types, and media/text helpers
from `dirdiff.formats`. `Composer.bays()` exposes the exact ordered bay stream
without running an engine; `Composer.compose()` renders that same stream and
groups it into frames.

## Purpose and boundaries

This package turns supplied path hints and bytes into the common semantic File
shape used by rendering and review coordinates. It classifies text, notebooks,
images, and unreadable blobs in one place so callers agree on bay identity. It
does not load workspace content, persist review state, or attach HTTP-only
fields.
"""

from dirdiff.formats.base import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
    IMAGE_METADATA_BAY_KEY,
    Bay,
    BayChange,
    BayContext,
    ComposeContext,
    ComposedFilePayload,
    FramePayload,
    ImageBay,
    MediaRef,
    MediaSide,
    TextBay,
    TextRejection,
    media_ref,
    try_decode_text,
)
from dirdiff.formats.composer import Composer

__all__ = [
    "BLOB_BAY_KEY",
    "FLATFILE_BAY_KEY",
    "IMAGE_BAY_KEY",
    "IMAGE_FACTS_BAY_KEY",
    "IMAGE_METADATA_BAY_KEY",
    "Bay",
    "BayChange",
    "BayContext",
    "ComposeContext",
    "ComposedFilePayload",
    "Composer",
    "FramePayload",
    "ImageBay",
    "MediaRef",
    "MediaSide",
    "TextBay",
    "TextRejection",
    "media_ref",
    "try_decode_text",
]
