"""The formats subsystem: every `/api/file-diff` response is one composed diff.

A composed diff is File-level metadata plus an ordered list of frames, each
holding an ordered list of bays. Bay kinds, not file formats, are the
extension axis: adding a format adds a classification step and a bay builder;
adding a kind of thing a reviewer can look at adds a bay kind and its widget.

Code outside this package imports its public items from here, never from a
submodule:

- `Composer` composes two captured byte sides into a composed diff. It has two
  entry points — `bays()` for the engine-free lookups (review validation and
  the blob endpoint) and `compose()` for `/api/file-diff` — so "this consumer
  runs no engine" is a type-level fact rather than a convention.
- `ComposeContext` is the input `compose()` reads, built by its named
  constructor from plain facts (both paths, both side labels, the renderer).
- `ComposedFilePayload` is everything `compose()` produces: the composed-diff
  envelope minus the `display_name` and `file_kind` the HTTP boundary attaches.

Package-internal contracts (`BayContext`, `TextBay`, the shared
text-bay renderer, the hunk allocator, and the serialized frame/bay
shapes) live in `base.py`; sibling modules import them from there. `composer.py`
is the one module that owns the ordered classification; `flatfile.py` and
`notebook.py` own the bays each format composes into.
"""

from dirdiff.formats.base import (
    FLATFILE_BAY_KEY,
    BayChange,
    BayContext,
    ComposeContext,
    ComposedFilePayload,
    FramePayload,
)
from dirdiff.formats.composer import Composer

__all__ = [
    "FLATFILE_BAY_KEY",
    "BayChange",
    "BayContext",
    "ComposeContext",
    "ComposedFilePayload",
    "Composer",
    "FramePayload",
]
