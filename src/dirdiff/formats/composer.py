from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Literal

from dirdiff.formats._composer.blob import blob_bays, blob_media_type
from dirdiff.formats._composer.flatfile import flatfile_bays
from dirdiff.formats._composer.image import displayable_image_type, image_bays
from dirdiff.formats._composer.notebook import (
    NotebookDocument,
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
from dirdiff.util import Nothing, Ok, Option, Some

__all__ = ["Composer", "read_captured_link", "write_captured_link"]


FileFormat = Literal["notebook", "image", "blob", "text"]


class Composer:
    def bays(
        self,
        left: Option[bytes],
        right: Option[bytes],
        context: BayContext,
    ) -> Iterator[Bay]:
        if Some.present(context.left_link) or Some.present(context.right_link):
            yield from symlink_bays(left, right, context)
            return

        # A notebook or a blob is named by its path, an image by its bytes.
        def claim(
            path: str, data: Option[bytes]
        ) -> tuple[FileFormat, Option[str]]:
            if path.lower().endswith(".ipynb"):
                return "notebook", Nothing()
            image_type = data.and_then(displayable_image_type)
            if Some.present(image_type):
                return "image", image_type
            blob_type = blob_media_type(path)
            if Some.present(blob_type):
                return "blob", blob_type
            return "text", Nothing()

        left_claim = context.left_path.map(lambda path: claim(path, left))
        right_claim = context.right_path.map(lambda path: claim(path, right))
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
# builder. Only composer.py may import modules under _composer; the leaves
# cannot
# import each other.
def symlink_bays(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
) -> Iterator[Bay]:
    composition = symlink_composition(left, right, context)
    yield composition.link_bay
    left_target = composition.left_target
    right_target = composition.right_target
    target_context = composition.target_context
    left_target_path = target_context.left_path
    right_target_path = target_context.right_path

    def claim(path: str, data: Option[bytes]) -> tuple[FileFormat, Option[str]]:
        if path.lower().endswith(".ipynb"):
            return "notebook", Nothing()
        image_type = data.and_then(displayable_image_type)
        if Some.present(image_type):
            return "image", image_type
        blob_type = blob_media_type(path)
        if Some.present(blob_type):
            return "blob", blob_type
        return "text", Nothing()

    left_claim = left_target_path.map(lambda path: claim(path, left_target))
    right_claim = right_target_path.map(lambda path: claim(path, right_target))
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
                blocks: list[str] = []
                for entry in document.cells:
                    if Ok.good(entry):
                        cell = entry.value
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
                    raw = json.dumps(
                        entry.error.raw,
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
