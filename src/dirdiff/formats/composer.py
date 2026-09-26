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

import json
from collections.abc import Iterator
from typing import Literal

from dirdiff.formats._composer.blob import blob_bays, blob_media_type
from dirdiff.formats._composer.flatfile import flatfile_bays
from dirdiff.formats._composer.image import image_bays, image_media_type
from dirdiff.formats._composer.notebook import (
    NotebookCell,
    NotebookDocument,
    RejectedNotebookPart,
    notebook_bays,
    try_load_notebook_document,
)
from dirdiff.formats._composer.symlink import (
    nested_symlink_target_document,
    read_captured_link,
    symlink_composition,
    symlink_target_bay,
    write_captured_link,
)
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
    ImageBay,
    TextBay,
    image_kind_payload,
    text_kind_payload,
)
from dirdiff.util import Nothing, Option, Some

__all__ = ["Composer", "read_captured_link", "write_captured_link"]


FileFormat = Literal["notebook", "image", "blob", "text"]
"""Select the format builder for one captured File pair.

- `notebook` uses notebook structure.
- `image` produces picture, metadata, and facts bays.
- `text` produces one decoded flatfile bay.
- `blob` is the terminal for content dirdiff cannot read as text.

Composer derives this value from paths and bytes. It is not a bay kind, VCS
tracking, or caller-selectable rendering option.
"""


class Composer:
    """Compose two captured byte sides into one composed diff.

    The class is stateless and pure: construct one and reuse it. For the same
    captured sides and context, engine-free bay lookup and composed rendering
    use the same bay identities.

    *Implementation detail:* `compose()` consumes `bays()` as the single
    enumeration of a File's bays; review replay uses that enumeration too.

    # Usage

    Construct directly. Use `bays` for engine-free bay lookup and `compose` for
    the serialized diff. The object keeps no state between calls.
    """

    def bays(
        self,
        left: Option[bytes],
        right: Option[bytes],
        context: BayContext,
    ) -> Iterator[Bay]:
        """Yield every bay this File composes into, in document order.

        The result contains nothing an engine produces: a text bay carries its
        identity, its two side labels, and its two decoded sides; an image bay
        carries its identity and its two sides of exact bytes. Callers that only
        need to know whether a bay key exists, to read one bay's decoded
        content, or to serve one bay's bytes, get that without rendering any bay
        and without an engine in reach.

        Paths select notebook, image, blob, or presumed text. Captured link
        facts select link composition before ordinary path classification.
        Notebook damage is preserved as raw text or byte facts, and presumed
        text that cannot decode is stated as blob facts. Both attach visible
        warnings. A parser failure never retries another format.

        # Parameters

        - `left`: Captured old bytes, or `Nothing` when that side is absent.
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

        - Missing File paths raise `AssertionError` during iteration.
        - Side facts contradicting the selected format raise `AssertionError`
          during iteration.
        - Unexpected parser failures propagate during iteration.
        """

        if Some.present(context.left_link) or Some.present(context.right_link):
            yield from symlink_bays(left, right, context)
            return

        # Classification belongs to this one dispatch. Keeping it local makes
        # the `match` below the only top-level format-selection operation.
        def path_claim(path: str) -> tuple[FileFormat, Option[str]]:
            """Return one path's strongest format claim and media type.

            # Returns

            - First, the builder classification claimed by the path.
            - Second, its declared media type when image/blob needs one.
            """
            if path.lower().endswith(".ipynb"):
                return "notebook", Nothing()
            image_type = image_media_type(path)
            if Some.present(image_type):
                return "image", image_type
            blob_type = blob_media_type(path)
            if Some.present(blob_type):
                return "blob", blob_type
            return "text", Nothing()

        left_claim = context.left_path.map(path_claim)
        right_claim = context.right_path.map(path_claim)
        present_claims = [
            claim.value
            for claim in (left_claim, right_claim)
            if Some.present(claim)
        ]
        assert len(present_claims) > 0, (
            "a File pair always has at least one path"
        )
        format_name: FileFormat = (
            present_claims[0][0]
            if all(claim[0] == present_claims[0][0] for claim in present_claims)
            else "text"
        )
        left_media_type = left_claim.and_then(lambda claim: claim[1])
        right_media_type = right_claim.and_then(lambda claim: claim[1])
        match format_name:
            case "notebook":
                yield from notebook_bays(left, right, context)
                return
            case "image":
                if not Some.present(left) and not Some.present(right):
                    yield from flatfile_bays(Nothing(), Nothing(), context)
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
        left: Option[bytes],
        right: Option[bytes],
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

        - `left`: Captured old bytes, or `Nothing` when absent.
        - `right`: Captured new bytes under the same convention.
        - `context`: Bay inputs plus the selected text renderer.

        # Usage

        Call after loading one captured File pair and constructing its context
        with `ComposeContext.build`. Attach manifest `display_name` and
        `file_kind` to the returned value at the HTTP boundary.

        # Failures

        - Classification and builder invariant errors propagate from `bays`.
        - Engine and display-enrichment failures propagate from text bays.

        Either failure aborts the call without returning a partial envelope.
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
            "left_exists": Some.present(left),
            "right_exists": Some.present(right),
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
            "left_path": context.bays.left_path.get(),
            "right_path": context.bays.right_path.get(),
            "summary": summary,
            "default_expanded": True,
            "frames": frames,
        }


# NOTE: This coordinator lives here because it must import every private format
# builder. Only composer.py may import modules under _composer; the leaves cannot
# import each other.
def symlink_bays(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
) -> Iterator[Bay]:
    """Compose one link File through independent private format leaves.

    Link-specific mechanics produce the visible payload and normalized target
    pair. This coordinator then selects exactly one ordinary target builder and
    applies the link target's collapsed coordinate to its result. Notebook
    targets flatten to script-like text so nested formats introduce no frames.

    # Parameters

    - `left`: Raw old-side bytes, or `Nothing` when that side is absent.
    - `right`: Raw new-side bytes under the same convention.
    - `context`: File paths, labels, and captured link facts for both sides.

    # Returns

    - First, the required visible outer-link payload bay.
    - Then, one collapsed target bay with reached content or a diagnosis.
    """
    composition = symlink_composition(left, right, context)
    yield composition.link_bay
    left_target = composition.left_target
    right_target = composition.right_target
    target_context = composition.target_context
    left_target_path = target_context.left_path
    right_target_path = target_context.right_path

    def path_claim(path: str) -> tuple[FileFormat, Option[str]]:
        """Return one final target path's format and declared media type.

        # Returns

        - First, the target builder classification claimed by the path.
        - Second, its declared media type when image/blob needs one.
        """
        if path.lower().endswith(".ipynb"):
            return "notebook", Nothing()
        image_type = image_media_type(path)
        if Some.present(image_type):
            return "image", image_type
        blob_type = blob_media_type(path)
        if Some.present(blob_type):
            return "blob", blob_type
        return "text", Nothing()

    left_claim = left_target_path.map(path_claim)
    right_claim = right_target_path.map(path_claim)
    present_claims = [
        claim.value
        for claim in (left_claim, right_claim)
        if Some.present(claim)
    ]
    assert len(present_claims) > 0, "reached content always has a target path"
    format_name: FileFormat = (
        present_claims[0][0]
        if all(claim[0] == present_claims[0][0] for claim in present_claims)
        else "text"
    )
    left_media_type = left_claim.and_then(lambda claim: claim[1])
    right_media_type = right_claim.and_then(lambda claim: claim[1])

    if format_name == "image":
        picture = next(
            image_bays(
                left_target,
                right_target,
                target_context,
                left_media_type=left_media_type,
                right_media_type=right_media_type,
            )
        )
        assert isinstance(picture, ImageBay), (
            "an image builder yields its picture before text attachments"
        )
        yield symlink_target_bay(picture)
        return
    if format_name == "blob":
        for bay in blob_bays(
            left_target,
            right_target,
            target_context,
            left_media_type=left_media_type,
            right_media_type=right_media_type,
            warnings=(),
        ):
            yield symlink_target_bay(bay)
        return
    if format_name == "notebook":
        left_document = left_target.and_then(try_load_notebook_document)
        right_document = right_target.and_then(try_load_notebook_document)
        if (not Some.present(left_target) or Some.present(left_document)) and (
            not Some.present(right_target) or Some.present(right_document)
        ):

            def notebook_script(document: NotebookDocument) -> bytes:
                """Flatten one accepted notebook without its output records."""
                blocks: list[str] = []
                for cell in document.cells:
                    if isinstance(cell, NotebookCell):
                        marker = "# %%"
                        if cell.cell_type != "code":
                            marker = f"# %% [{cell.cell_type}]"
                        if cell.cell_type == "code":
                            body = cell.source
                        else:
                            body = "\n".join(
                                "#" if line == "" else f"# {line}"
                                for line in cell.source.splitlines()
                            )
                        blocks.append(
                            marker if body == "" else f"{marker}\n{body}"
                        )
                        continue
                    assert isinstance(cell, RejectedNotebookPart)
                    raw = json.dumps(
                        cell.raw,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    body = "\n".join(f"# {line}" for line in raw.splitlines())
                    blocks.append(f"# %% [invalid]\n{body}")
                return (("\n\n".join(blocks) + "\n") if blocks else "").encode()

            left_script = left_document.map(notebook_script)
            right_script = right_document.map(notebook_script)
            left_script = nested_symlink_target_document(
                left_script,
                context.left_link,
                left_target_path,
            )
            right_script = nested_symlink_target_document(
                right_script,
                context.right_link,
                right_target_path,
            )
            script_context = BayContext(
                left_path=left_script.map(lambda _: "target.py"),
                right_path=right_script.map(lambda _: "target.py"),
                left_label=context.left_label,
                right_label=context.right_label,
            )
            for bay in flatfile_bays(
                left_script,
                right_script,
                script_context,
            ):
                yield symlink_target_bay(bay)
            return
    for bay in flatfile_bays(
        nested_symlink_target_document(
            left_target,
            context.left_link,
            left_target_path,
        ),
        nested_symlink_target_document(
            right_target,
            context.right_link,
            right_target_path,
        ),
        target_context,
    ):
        yield symlink_target_bay(bay)
