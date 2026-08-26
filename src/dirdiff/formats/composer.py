"""Composition: the one class that turns two byte sides into a composed diff.

`Composer` is not a protocol with an implementation per format. The
format-specific part is only *which bays get built*, and that lives in the
ordered classification below and the sibling builders it calls. Composition
itself is one class with two entry points, because two of its three consumers
must not touch a diff engine:

- `bays()` yields every bay a File composes into, in document order, with
  nothing an engine produces. Review validation and the media endpoint call it:
  both are lookups that must answer about one bay without rendering every bay,
  and neither may reach an engine.
- `compose()` consumes that stream, renders each text bay through the shared
  text-bay renderer and reduces each image bay to its references, wraps each
  result in the bay envelope, aggregates the summary, and returns the
  composed-diff envelope, with row hunk boundaries left bay-local for the
  frontend to number. `/api/file-diff` calls it.

Neither method has a "not my format" outcome. Classification always reaches an
answer, because the blob step is terminal: whatever the two byte sides are, some
bay holds them, and no File reaches the frontend as an error where a diff was
expected.

Purity: the same two byte sides and the same context produce the same bays,
keys, and order. Composition reads no clock, database, Room, or outside file.
"""

from __future__ import annotations

from collections.abc import Iterator

from dirdiff.formats.base import (
    Bay,
    BayContext,
    BayKindPayload,
    BayPayload,
    ComposeContext,
    ComposedFilePayload,
    ComposedSummary,
    FramePayload,
    TextBay,
    image_kind_payload,
    text_content_or_none,
    text_kind_payload,
)
from dirdiff.formats.blob import blob_bays
from dirdiff.formats.flatfile import flatfile_bays
from dirdiff.formats.image import image_bays, image_media_type
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
    ) -> Iterator[Bay]:
        """Yield every bay this File composes into, in document order.

        The result contains nothing an engine produces: a text bay carries its
        identity, its two side labels, and its two decoded sides; an image bay
        carries its identity and its two sides of exact bytes. Callers that only
        need to know whether a bay key exists, to read one bay's decoded
        content, or to serve one bay's bytes, get that without rendering any bay
        and without an engine in reach.

        Classification is an explicit ordered check, written here in one place:

        1. notebook, when a path suffix says `.ipynb` and every present side
           loads as notebook JSON;
        2. image, when every present side's path names an image type;
        3. flatfile, when every present side decodes as text;
        4. blob, the terminal every other File reaches.

        Each step hands its builder the value it already validated — parsed
        notebooks, media types, decoded text, raw bytes — so a builder cannot be
        handed the wrong format, because its parameters do not admit one.

        A step that any present side fails falls through to the next, and the
        terminal accepts everything, so classification always reaches an answer:
        a `.ipynb` whose bytes do not load is diffed as the text it is, a `.png`
        renamed to a `.txt` is neither an image on both sides nor text on both
        sides and lands on blob, and no input raises here at all.
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
            # falls through to the next check. An absent side is not
            # malformed: the file was added or removed, and the notebook
            # builder reports that side as absent.
            left_is_notebook = left is None or left_document is not None
            right_is_notebook = right is None or right_document is not None
            if left_is_notebook and right_is_notebook:
                yield from notebook_bays(left_document, right_document, context)
                return

        # Both sides must claim an image type, so a File that was an image on
        # one side and something else on the other is not composed as one. It
        # reaches the blob terminal below, where the two digests still report
        # honestly that the content changed.
        left_media_type = image_media_type(context.left_path)
        right_media_type = image_media_type(context.right_path)
        # A File captured on neither side has no picture to show, so it is not
        # an image whatever its paths claim, and it takes the same route it
        # always has.
        captured = left is not None or right is not None
        left_is_image = left is None or left_media_type is not None
        right_is_image = right is None or right_media_type is not None
        if captured and left_is_image and right_is_image:
            yield from image_bays(
                left,
                right,
                context,
                left_media_type=left_media_type,
                right_media_type=right_media_type,
            )
            return

        # Decoding is the classification test, so it happens here rather than
        # inside the flatfile builder, and its result is handed down. Nothing
        # is decoded twice and nothing raises: content that is not text has
        # the blob terminal to go to.
        left_text = None if left is None else text_content_or_none(left)
        right_text = None if right is None else text_content_or_none(right)
        left_is_text = left is None or left_text is not None
        right_is_text = right is None or right_text is not None
        if left_is_text and right_is_text:
            yield from flatfile_bays(left_text, right_text, context)
            return

        yield from blob_bays(left, right, context)

    def compose(
        self,
        left: bytes | None,
        right: bytes | None,
        context: ComposeContext,
    ) -> ComposedFilePayload:
        """Compose the whole envelope except its two attached fields.

        Renders each bay `bays()` yields — a text bay through the engine, an
        image bay by reducing its sides to references — wraps each in the bay
        envelope, groups the rendered bays into contiguous frames, aggregates
        the summary, and re-emits the paths and labels the context carried. Row
        hunk boundaries stay bay-local; the frontend numbers them.
        `display_name` and `file_kind` are attached by the HTTP boundary.
        """
        frames: list[FramePayload] = []
        for bay in self.bays(left, right, context.bays):
            # Rendering decides only what the bay holds. The envelope around it
            # is the same record whatever the kind, and is written once here
            # rather than by each renderer, so a new kind adds an arm to
            # `BayKindPayload` and nothing else.
            kind_data: BayKindPayload = (
                text_kind_payload(bay, context.renderer)
                if isinstance(bay, TextBay)
                else image_kind_payload(bay)
            )
            rendered: BayPayload = {
                "bay_key": bay.bay_key,
                "label": bay.label,
                "detail": bay.detail,
                "collapsible": bay.collapsible,
                "default_expanded": bay.default_expanded,
                "change": bay.change,
                "kind_data": kind_data,
            }
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
        # Only text bays hold line counts. An image bay contributes nothing to a
        # line summary because it has no lines, and inventing a count for it
        # would misreport the File's size of change.
        for frame in frames:
            for rendered_bay in frame["bays"]:
                bay_content = rendered_bay["kind_data"]
                if bay_content["kind"] != "text":
                    continue
                stats = bay_content["stats"]
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
