"""Classification and composition of one captured File pair.

## Public interface

`Composer.bays` classifies the supplied paths and yields the ordered bays built
by the flatfile, notebook, image, or blob module. `Composer.compose` renders
that same stream, groups bays into frames, aggregates their summary, and
returns the composed-diff envelope.

## Purpose and boundaries

Classification is total and depends only on supplied paths and bytes. The
same bay enumeration serves full rendering, review-coordinate validation, and
media lookup, so those consumers cannot disagree about which bays a File has.
File loading and the HTTP-only display name and File kind remain outside this
module.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal

from dirdiff.formats.base import (
    Bay,
    BayContext,
    BayKindPayload,
    BayPayload,
    BayWarning,
    ComposeContext,
    ComposedFilePayload,
    ComposedSummary,
    FramePayload,
    TextBay,
    image_kind_payload,
    text_kind_payload,
)
from dirdiff.formats.blob import blob_bays, blob_media_type
from dirdiff.formats.flatfile import flatfile_bays
from dirdiff.formats.image import image_bays, image_media_type
from dirdiff.formats.notebook import (
    notebook_bays,
)
from dirdiff.formats.symlink import symlink_bays

__all__ = ["Composer"]


FileFormat = Literal["notebook", "image", "blob", "text"]
"""Select the format builder for one captured File pair.

- `notebook` uses notebook structure.
- `image` produces picture, metadata, and facts bays.
- `text` produces one decoded flatfile bay.
- `blob` is the terminal for content dirdiff cannot read as text.

Composer derives this value from paths and bytes. It is not a bay kind, File
provenance, or caller-selectable rendering option.
"""


class Composer:
    """Compose two captured byte sides into one composed diff.

    The class is stateless and pure: construct one and reuse it. Its two methods
    share `bays()` as the single enumeration of a File's bays, so the
    bay identity review replay recomputes and the identity `compose()` renders
    can never disagree.

    # Usage

    Construct directly. Use `bays` for engine-free bay lookup and `compose` for
    the serialized diff. The object keeps no state between calls.
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

        Local path classification decides one of notebook, image, blob, or
        presumed text. The resulting `match` calls one builder; a parser failure
        never retries another format arm. Notebook damage is preserved as raw
        text or byte facts, and presumed text that cannot decode is stated as
        blob facts. Both attach visible warnings.

        # Parameters

        - `left`: Captured old bytes, or `None` when that side is absent.
        - `right`: Captured new bytes under the same convention.
        - `context`: Paths and labels used for classification and bay content.

        # Usage

        Review validation and media serving iterate this method when they need
        bay identity or content without rendering. Full rendering should call
        `compose`, which consumes the same iterator.

        # Returns

        - `Yielded bays`: Engine-free bay identity and exact side content later
          consumed by `compose` or review lookup.
        - `Order`: Format builders define document order; an iterator may contain
          one whole-File bay or several format-specific bays.

        # Failures

        Iteration raises `AssertionError` when neither path identifies a File or
        when a format builder receives side facts that contradict its path
        classification. Unexpected parser failures propagate.
        """

        if context.left_link is not None or context.right_link is not None:
            yield from symlink_bays(left, right, context)
            return

        # Classification belongs to this one dispatch. Keeping it local makes
        # the `match` below the only top-level format-selection operation.
        def path_claim(path: str) -> tuple[FileFormat, str | None]:
            """Return one path's strongest format claim and media type.

            # Returns

            - First, the builder classification claimed by the path.
            - Second, its declared media type when image/blob needs one.
            """
            if path.lower().endswith(".ipynb"):
                return "notebook", None
            image_type = image_media_type(path)
            if image_type is not None:
                return "image", image_type
            blob_type = blob_media_type(path)
            if blob_type is not None:
                return "blob", blob_type
            return "text", None

        left_claim = (
            None if context.left_path is None else path_claim(context.left_path)
        )
        right_claim = (
            None
            if context.right_path is None
            else path_claim(context.right_path)
        )
        present_claims = [
            claim for claim in (left_claim, right_claim) if claim is not None
        ]
        assert len(present_claims) > 0, (
            "a File pair always has at least one path"
        )
        format_name: FileFormat = (
            present_claims[0][0]
            if all(claim[0] == present_claims[0][0] for claim in present_claims)
            else "text"
        )
        left_media_type = None if left_claim is None else left_claim[1]
        right_media_type = None if right_claim is None else right_claim[1]
        match format_name:
            case "notebook":
                yield from notebook_bays(left, right, context)
                return
            case "image":
                if left is None and right is None:
                    yield from flatfile_bays(None, None, context)
                    return
                yield from image_bays(
                    left,
                    right,
                    context,
                    left_media_type=left_media_type,
                    right_media_type=right_media_type,
                )
                return
            case "blob":
                yield from blob_bays(
                    left,
                    right,
                    context,
                    left_media_type=left_media_type,
                    right_media_type=right_media_type,
                    warnings=(),
                )
                return
            case "text":
                yield from flatfile_bays(left, right, context)
                return

    def compose(
        self,
        left: bytes | None,
        right: bytes | None,
        context: ComposeContext,
    ) -> ComposedFilePayload:
        """Compose the whole envelope except its two attached fields.

        Renders each bay yielded by `bays()`. It sends text bays through the
        engine and reduces image bay sides to references. It wraps each result
        in the bay envelope, groups rendered bays into contiguous frames,
        aggregates the summary, and re-emits the paths and labels carried by the
        context. Row hunk boundaries stay bay-local; the frontend numbers them.
        `display_name` and `file_kind` are attached by the HTTP boundary.

        # Parameters

        - `left`: Captured old bytes, or `None` when absent.
        - `right`: Captured new bytes under the same convention.
        - `context`: Bay inputs plus the selected text renderer.

        # Usage

        Call after loading one captured File pair and constructing its context
        with `ComposeContext.build`. Attach manifest `display_name` and
        `file_kind` to the returned value at the HTTP boundary.

        # Failures

        Propagates classification and builder invariant errors from `bays`,
        plus any engine or display-enrichment failure from a text bay. No
        partial envelope is returned.
        """
        frames: list[FramePayload] = []
        for bay in self.bays(left, right, context.bays):
            # Rendering decides only what the bay holds. The envelope around it
            # is the same record whatever the kind, and is written once here
            # rather than by each renderer, so a new kind adds an arm to
            # `BayKindPayload` and nothing else.
            kind_data: BayKindPayload
            engine_warning = None
            if isinstance(bay, TextBay):
                kind_data, engine_warning = text_kind_payload(
                    bay, context.renderer
                )
            else:
                kind_data = image_kind_payload(bay)
            warnings = list(bay.warnings)
            if engine_warning is not None:
                warnings.append(
                    BayWarning(
                        type=engine_warning["type"],
                        message=engine_warning["message"],
                    )
                )
            rendered: BayPayload = {
                "bay_key": bay.bay_key,
                "label": bay.label,
                "detail": bay.detail,
                "collapsible": bay.collapsible,
                "default_expanded": bay.default_expanded,
                "change": bay.change,
                "warnings": warnings,
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
