"""Composition: the one class that turns two byte sides into a composed diff.

`Composer` is not a protocol with an implementation per format. The
format-specific part is only *which bays get built*, and that lives in the
ordered classification below and the sibling builders it calls. Composition
itself is one class with two entry points, because two of its three consumers
must not touch a diff engine:

- `bays()` yields every bay a File composes into, in document order, with
  nothing an engine produces. Review validation and the (future) blob endpoint
  call it: both are lookups that must answer about one bay without rendering
  every bay, and neither may reach an engine.
- `compose()` consumes that stream, renders each text bay through the shared
  text-bay renderer, aggregates the summary, and returns the composed-diff
  envelope, with row hunk boundaries left bay-local for the frontend to
  number. `/api/file-diff` calls it.

Neither method has a "not my format" outcome. Classification always reaches an
answer: in this stage a File that is not a notebook is composed as a flatfile,
and non-text content raises at the decode boundary exactly as it does today.
(The terminal `binary` bay that makes classification total for every input is
a later stage.)

Purity: the same two byte sides and the same context produce the same bays,
keys, and order. Composition reads no clock, database, Room, or outside file.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.formats.base import (
    BayContext,
    ComposeContext,
    ComposedFilePayload,
    ComposedSummary,
    FramePayload,
    TextBay,
    render_text_bay,
)
from dirdiff.formats.flatfile import flatfile_bays
from dirdiff.formats.notebook import (
    notebook_bays,
    try_load_notebook_document,
)

__all__ = ["Composer"]


class Composer:
    """Compose two captured byte sides into one composed diff.

    The class is stateless and pure: construct one and reuse it. Its two methods
    share `bays()` as the single enumeration of a File's bays, so the
    bay identity review replay recomputes and the identity `compose()` renders
    can never disagree.
    """

    def bays(
        self,
        left: bytes | None,
        right: bytes | None,
        context: BayContext,
    ) -> Iterator[TextBay]:
        """Yield every bay this File composes into, in document order.

        The result contains nothing an engine produces: each text bay carries
        its identity, its two side labels, and its two decoded sides. Callers
        that only need to know whether a bay key exists, or to read one
        bay's decoded content, get that without rendering any bay and
        without an engine in reach.

        Classification is an explicit ordered check, written here in one place:

        1. notebook, when a path suffix says `.ipynb` and every present side
           loads as notebook JSON;
        2. flatfile, the terminal every other File reaches.

        A `.ipynb` whose bytes do not load is not a notebook; it falls through to
        the flatfile terminal. A binary or non-UTF-8 text side raises
        `DirdiffError` at that builder's decode boundary, which the request
        handler reports as an unsupported file diff. Each step hands its builder
        the value it already validated, so a builder cannot be handed the wrong
        format. (The terminal `binary` bay and the `image` check are later
        stages.)
        """
        if any(
            path is not None and path.endswith(".ipynb")
            for path in (context.left_path, context.right_path)
        ):
            left_document = (
                None if left is None else try_load_notebook_document(left)
            )
            right_document = (
                None if right is None else try_load_notebook_document(right)
            )
            # A present side that did not load is a malformed notebook and
            # falls through to the flatfile terminal. An absent side is not
            # malformed: the file was added or removed, and the notebook
            # builder reports that side as absent.
            left_is_notebook = left is None or left_document is not None
            right_is_notebook = right is None or right_document is not None
            if left_is_notebook and right_is_notebook:
                yield from notebook_bays(left_document, right_document, context)
                return

        # Flatfile: the terminal every File that no other format claims
        # reaches, including a `.ipynb` whose bytes did not load above.
        yield from flatfile_bays(left, right, context)

    def compose(
        self,
        left: bytes | None,
        right: bytes | None,
        context: ComposeContext,
    ) -> ComposedFilePayload:
        """Compose the whole envelope except its two attached fields.

        Renders each text bay `bays()` yields, groups the rendered bays
        into contiguous frames, aggregates the summary, and re-emits the paths
        and labels the context carried. Row hunk boundaries stay bay-local;
        the frontend numbers them.
        `display_name` and `file_kind` are attached by the HTTP boundary.
        """
        frames: list[FramePayload] = []
        for bay in self.bays(left, right, context.bays):
            rendered = render_text_bay(bay, context.renderer)
            if (
                len(frames) > 0
                and frames[-1]["frame_key"] == bay.frame_key
                and frames[-1]["heading"] == bay.heading
            ):
                frames[-1]["bays"].append(rendered)
            else:
                frames.append(
                    {
                        "frame_key": bay.frame_key,
                        "heading": bay.heading,
                        "bays": [rendered],
                    }
                )

        summary: ComposedSummary = {
            "changed_lines": 0,
            "modified_lines": 0,
            "added_lines": 0,
            "removed_lines": 0,
            "moved_lines": 0,
            "left_exists": left is not None,
            "right_exists": right is not None,
        }
        for frame in frames:
            for rendered_bay in frame["bays"]:
                stats = rendered_bay["stats"]
                summary["changed_lines"] += stats["changed_lines"]
                summary["modified_lines"] += stats["modified_lines"]
                summary["added_lines"] += stats["added_lines"]
                summary["removed_lines"] += stats["removed_lines"]
                summary["moved_lines"] += stats["moved_lines"]

        return {
            "left_label": context.bays.left_label,
            "right_label": context.bays.right_label,
            "left_path": context.bays.left_path,
            "right_path": context.bays.right_path,
            "summary": summary,
            "default_expanded": True,
            "frames": frames,
        }
