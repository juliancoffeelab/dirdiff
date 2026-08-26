"""The formats subsystem: every `/api/file-diff` response is one composed diff.

A composed diff is File-level metadata plus an ordered list of frames, each
holding an ordered list of bays. Bay kinds, not file formats, are the
extension axis: adding a format adds a classification step and a bay builder;
adding a kind of thing a reviewer can look at adds a bay kind and its widget.

Code outside this package imports its public items from here, never from a
submodule:

- `Composer` composes two captured byte sides into a composed diff. It has two
  entry points — `bays()` for the engine-free lookups (review validation and
  the media endpoint) and `compose()` for `/api/file-diff` — so "this consumer
  runs no engine" is a type-level fact rather than a convention.
- `ComposeContext` is the input `compose()` reads, built by its named
  constructor from plain facts (both paths, both side labels, the renderer).
- `ComposedFilePayload` is everything `compose()` produces: the composed-diff
  envelope minus the `display_name` and `file_kind` the HTTP boundary attaches.
- `Bay`, `TextBay`, and `ImageBay` are what `bays()` yields, split by what a bay
  is made of. Its two consumers act on that split: review reconstructs an
  excerpt from decoded text or from an image's own facts, and the media endpoint
  serves only an `ImageBay`'s bytes.
- `MediaRef` and `media_ref()` describe one captured media side without its
  bytes, so the digest on the wire and the digest review reads are one
  computation.

Package-internal contracts (`ComposeContext`'s renderer, the two shared kind
renderers, the facts text both media classifications state, and the serialized
frame/bay shapes) live in `base.py`; sibling modules import them from there.
`composer.py` is the one module that owns the ordered classification;
`flatfile.py`, `notebook.py`, `image.py`, and `blob.py` own the bays each
format composes into.
"""

from dirdiff.formats.base import (
    BLOB_BAY_KEY,
    FLATFILE_BAY_KEY,
    IMAGE_BAY_KEY,
    IMAGE_FACTS_BAY_KEY,
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
    media_ref,
)
from dirdiff.formats.composer import Composer

__all__ = [
    "BLOB_BAY_KEY",
    "FLATFILE_BAY_KEY",
    "IMAGE_BAY_KEY",
    "IMAGE_FACTS_BAY_KEY",
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
    "media_ref",
]
