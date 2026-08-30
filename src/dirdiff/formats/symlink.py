"""Composition of captured symbolic-link Files.

## Public interface

`write_captured_link` and `read_captured_link` persist and authenticate the
exact private Snapshot sidecars named by relational capture facts.
`symlink_bays` composes a File with
at least one symbolic-link side into its raw payload bay and one collapsed
target bay containing safely reached content or a resolution diagnosis.

## Purpose and boundaries

Room capture has already walked each link inside the repository and stopped at
damage or a loop. This module writes and reads that immutable result beside the
ordinary Snapshot side, then turns it into bays. It never reads the repository,
follows links, or retries failed resolution. A target is deliberately flattened
to one bay so nested formats cannot introduce frames inside the link File.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    Bay,
    BayContext,
    CapturedLink,
    ImageBay,
    TextBay,
    TextRejection,
    try_decode_text,
    whole_file_change,
)
from dirdiff.formats.blob import blob_bays, blob_media_type
from dirdiff.formats.flatfile import flatfile_bays
from dirdiff.formats.image import image_bays, image_media_type
from dirdiff.formats.notebook import (
    NotebookCell,
    NotebookDocument,
    RejectedNotebookPart,
    try_load_notebook_document,
)

__all__ = ["read_captured_link", "symlink_bays", "write_captured_link"]


SYMLINK_BAY_KEY = "symlink"
"""Bay key for the exact payload stored by the outer symbolic link.

The bay contains only the target spelling itself, matching the bytes Git stores
for the link. Repository paths, arrows, nested links, and diagnoses do not
belong in it.
"""


SYMLINK_TARGET_BAY_KEY = "symlink-target"
"""Bay key for collapsed content or a stopped-walk diagnosis.

The bay compares safely resolved target content with ordinary File content on
a side that changed between link and non-link. A link that reaches no content
uses the same bay for its synthetic nested walk and terminal diagnosis.
"""


TargetFormat = Literal["notebook", "image", "blob", "text"]
"""Format used to flatten one safely reached target pair.

It is local to link composition: it cannot classify the outer File or select a
top-level format builder. Conflicting target suffix claims become text so one
specialized representation never lies about the other side.
"""


def write_captured_link(
    link: CapturedLink,
    *,
    metadata_path: Path,
    target_capture_path: Path | None,
) -> tuple[bytes, bytes | None]:
    """Persist one link capture at publication-selected sidecar paths.

    Metadata holds nested links, any diagnosis, the final repository path, and
    no physical-path or duplicate-digest facts. Exact target bytes use a
    separate sidecar so SQLite does not become a content store. The returned
    digests and the caller-selected final paths become the authoritative
    relational link record before the staging directory is published.

    # Parameters

    - `link`: Complete immutable chain and optional final target to persist.
    - `metadata_path`: Exact unpublished path receiving the JSON metadata.
    - `target_capture_path`: Exact unpublished path receiving reached target
      bytes, or `None` for a stopped walk. Its presence must match target bytes.

    # Returns

    - First, the SHA-256 digest of the exact metadata bytes written.
    - Second, the SHA-256 digest of target bytes, or `None` when no target was
      reached. The caller persists both beside their final physical paths.
    """
    assert metadata_path.is_absolute(), (
        f"Snapshot link metadata path must be absolute: {metadata_path}"
    )
    assert (target_capture_path is None) == (link.target_data is None), (
        "Snapshot link target path and bytes must have equal presence"
    )
    if target_capture_path is not None:
        assert target_capture_path.is_absolute(), (
            "Snapshot link target capture path must be absolute: "
            f"{target_capture_path}"
        )
    metadata_data = json.dumps(
        {
            "nested_links": link.nested_links,
            "diagnosis": link.diagnosis,
            "target_path": link.target_path,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    metadata_path.write_bytes(metadata_data)
    if link.target_data is not None:
        assert target_capture_path is not None
        target_capture_path.write_bytes(link.target_data)
    return (
        hashlib.sha256(metadata_data).digest(),
        (
            hashlib.sha256(link.target_data).digest()
            if link.target_data is not None
            else None
        ),
    )


def read_captured_link(
    *,
    metadata_path: Path,
    metadata_hash: bytes,
    target_capture_path: Path | None,
    target_hash: bytes | None,
) -> CapturedLink:
    """Read one link capture from its authoritative relational paths.

    Callers invoke this only when the database says the side is a link. Both
    sidecar byte sequences must match their database digests before metadata is
    interpreted. Successful resolution requires target bytes and a final
    repository path; a stopped walk forbids both. Invalid state raises or
    asserts instead of degrading into an ordinary File.

    # Parameters

    - `metadata_path`: Exact absolute JSON sidecar path stored in the database.
    - `metadata_hash`: Stored SHA-256 digest of that JSON byte sequence.
    - `target_capture_path`: Exact absolute target-content path stored in the
      database, or `None` for a stopped walk.
    - `target_hash`: Stored SHA-256 target digest with the same presence.

    # Returns

    - Authenticated chain and optional final target facts. Ordinary captured
      sides never call this function.
    """
    assert metadata_path.is_absolute(), (
        f"Snapshot link metadata path must be absolute: {metadata_path}"
    )
    assert len(metadata_hash) == 32, (
        "Snapshot link metadata hash must have length 32"
    )
    assert (target_capture_path is None) == (target_hash is None), (
        "Snapshot link target path and hash must have equal presence"
    )
    metadata_data = metadata_path.read_bytes()
    assert hashlib.sha256(metadata_data).digest() == metadata_hash, (
        f"Snapshot link metadata hash mismatch: {metadata_path}"
    )
    parsed = json.loads(metadata_data.decode("utf-8"))
    assert isinstance(parsed, dict) and set(parsed) == {
        "nested_links",
        "diagnosis",
        "target_path",
    }, f"invalid Snapshot link metadata: {metadata_path}"
    nested_links = parsed["nested_links"]
    diagnosis = parsed["diagnosis"]
    captured_path = parsed["target_path"]
    assert isinstance(nested_links, list) and all(
        isinstance(item, list)
        and len(item) == 2
        and all(isinstance(value, str) for value in item)
        and item[0] != ""
        for item in nested_links
    ), f"invalid Snapshot nested links: {metadata_path}"
    assert diagnosis is None or isinstance(diagnosis, str), (
        f"invalid Snapshot link diagnosis: {metadata_path}"
    )
    if captured_path is None:
        assert diagnosis is not None and target_capture_path is None, (
            f"failed Snapshot link carries target bytes: {metadata_path}"
        )
        return CapturedLink(
            nested_links=tuple((item[0], item[1]) for item in nested_links),
            diagnosis=diagnosis,
            target_path=None,
            target_data=None,
        )
    assert diagnosis is None, (
        f"successful Snapshot link carries a diagnosis: {metadata_path}"
    )
    assert isinstance(captured_path, str) and captured_path != "", (
        f"invalid Snapshot link target path: {metadata_path}"
    )
    assert target_capture_path is not None and target_hash is not None, (
        f"successful Snapshot link has no target bytes: {metadata_path}"
    )
    assert target_capture_path.is_absolute(), (
        "Snapshot link target capture path must be absolute: "
        f"{target_capture_path}"
    )
    assert len(target_hash) == 32, (
        "Snapshot link target hash must have length 32"
    )
    target_data = target_capture_path.read_bytes()
    assert hashlib.sha256(target_data).digest() == target_hash, (
        f"Snapshot link target hash mismatch: {target_capture_path}"
    )
    return CapturedLink(
        nested_links=tuple((item[0], item[1]) for item in nested_links),
        diagnosis=None,
        target_path=captured_path,
        target_data=target_data,
    )


def symlink_bays(
    left: bytes | None,
    right: bytes | None,
    context: BayContext,
) -> Iterator[Bay]:
    """Yield the raw outer-link payload and one collapsed target bay.

    At least one context side must carry captured link facts. The first bay
    mirrors Git by showing only the exact outer link payload; a regular side in
    a link-to-File transition is absent from that bay. The second bay compares
    safely resolved target bytes with the ordinary bytes of a non-link side, or
    presents the stopped walk as a synthetic text document.

    Notebook targets flatten to one script-like text bay with `# %%` walls,
    markdown and raw source commented, and outputs omitted. Image targets keep
    one image bay. Opaque or rejected text targets become one facts bay through
    the existing builders. No target can introduce nested frames.

    # Parameters

    - `left`: Raw old-side bytes, or `None` when that side is absent.
    - `right`: Raw new-side bytes under the same convention.
    - `context`: File paths, labels, and captured link facts for both sides.

    # Returns

    - First, the required visible outer-link payload bay.
    - Then, one collapsed target bay with reached content or a diagnosis.
    """
    assert context.left_link is not None or context.right_link is not None, (
        "link composition requires captured link facts"
    )

    def link_payload(data: bytes) -> str:
        """Return one raw link payload as one safe reviewer-facing string."""
        try:
            target = data.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps(
                data.decode("utf-8", errors="backslashreplace"),
                ensure_ascii=False,
            )
        return (
            target
            if target.isprintable()
            else json.dumps(target, ensure_ascii=False)
        )

    if context.left_link is None:
        left_payload = None
    else:
        assert left is not None
        left_payload = link_payload(left)
    if context.right_link is None:
        right_payload = None
    else:
        assert right is not None
        right_payload = link_payload(right)
    yield TextBay(
        frame_key="file",
        heading=None,
        bay_key=SYMLINK_BAY_KEY,
        label="Link",
        detail=None,
        collapsible=False,
        default_expanded=True,
        change=whole_file_change(left_payload, right_payload),
        left_label=context.left_label,
        right_label=context.right_label,
        left=DiffSide(
            exists=left_payload is not None,
            text=left_payload,
            path_hint="link",
        ),
        right=DiffSide(
            exists=right_payload is not None,
            text=right_payload,
            path_hint="link",
        ),
    )

    def stopped_target(link: CapturedLink | None) -> bytes | None:
        """Build the synthetic target document for one stopped link walk.

        # Parameters

        - `link`: Captured link facts, or `None` for an ordinary side.

        # Returns

        - `bytes`: Comment-walled nested links and the terminal diagnosis.
        - `None`: The side is ordinary or the link reached final content.
        """
        if link is None or link.target_data is not None:
            return None
        assert link.diagnosis is not None
        blocks = [
            f"# %% {path}\n{payload}" for path, payload in link.nested_links
        ]
        blocks.append(f"# {link.diagnosis}")
        return ("\n\n".join(blocks) + "\n").encode()

    left_stopped = stopped_target(context.left_link)
    right_stopped = stopped_target(context.right_link)
    left_target = (
        left
        if context.left_link is None
        else (
            context.left_link.target_data
            if context.left_link.target_data is not None
            else left_stopped
        )
    )
    right_target = (
        right
        if context.right_link is None
        else (
            context.right_link.target_data
            if context.right_link.target_data is not None
            else right_stopped
        )
    )
    left_target_path = (
        context.left_path
        if context.left_link is None
        else context.left_link.target_path or "symlink-target.py"
    )
    right_target_path = (
        context.right_path
        if context.right_link is None
        else context.right_link.target_path or "symlink-target.py"
    )
    assert left_target is not None or right_target is not None, (
        "a captured link always yields target content or a diagnosis"
    )
    target_context = BayContext(
        left_path=left_target_path,
        right_path=right_target_path,
        left_label=context.left_label,
        right_label=context.right_label,
    )

    def target_bay(bay: Bay) -> Bay:
        """Apply the link target's single-frame contract to one built bay."""
        return replace(
            bay,
            frame_key="file",
            heading=None,
            bay_key=SYMLINK_TARGET_BAY_KEY,
            label="Target content",
            collapsible=True,
            default_expanded=False,
        )

    def nested_target_document(
        data: bytes | None,
        link: CapturedLink | None,
        final_path: str | None,
    ) -> bytes | None:
        """Prepend nested link payloads to one synthetic textual target.

        Direct links need no wall: their target is already an ordinary File.
        A nested walk becomes one script-like document where each `# %% path`
        wall names the object whose exact text follows it. Binary content stays
        in its specialized representation and therefore receives no text wall.

        # Parameters

        - `data`: Reached or already-flattened target bytes, if present.
        - `link`: Captured link facts, or `None` for an ordinary side.
        - `final_path`: Repository path naming reached final content.

        # Returns

        - `bytes`: Original bytes, or the comment-walled synthetic document.
        - `None`: No target bytes exist on this side.
        """
        if (
            data is None
            or link is None
            or link.target_data is None
            or link.nested_links == ()
        ):
            return data
        decoded = try_decode_text(data)
        if isinstance(decoded, TextRejection):
            return data
        assert final_path is not None
        blocks = [
            f"# %% {path}\n{payload}" for path, payload in link.nested_links
        ]
        blocks.append(f"# %% {final_path}\n{decoded}")
        return ("\n\n".join(blocks).rstrip("\n") + "\n").encode()

    def path_claim(path: str) -> tuple[TargetFormat, str | None]:
        """Return one final target path's format and declared media type.

        # Returns

        - First, the target builder classification claimed by the path.
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
        None if left_target_path is None else path_claim(left_target_path)
    )
    right_claim = (
        None if right_target_path is None else path_claim(right_target_path)
    )
    present_claims = [
        claim for claim in (left_claim, right_claim) if claim is not None
    ]
    assert len(present_claims) > 0, "reached content always has a target path"
    format_name: TargetFormat = (
        present_claims[0][0]
        if all(claim[0] == present_claims[0][0] for claim in present_claims)
        else "text"
    )
    left_media_type = None if left_claim is None else left_claim[1]
    right_media_type = None if right_claim is None else right_claim[1]

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
        yield target_bay(picture)
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
            yield target_bay(bay)
        return
    if format_name == "notebook":
        left_document = (
            None
            if left_target is None
            else try_load_notebook_document(left_target)
        )
        right_document = (
            None
            if right_target is None
            else try_load_notebook_document(right_target)
        )
        if (left_target is None or left_document is not None) and (
            right_target is None or right_document is not None
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

            left_script = (
                None
                if left_document is None
                else notebook_script(left_document)
            )
            right_script = (
                None
                if right_document is None
                else notebook_script(right_document)
            )
            left_script = nested_target_document(
                left_script,
                context.left_link,
                left_target_path,
            )
            right_script = nested_target_document(
                right_script,
                context.right_link,
                right_target_path,
            )
            script_context = BayContext(
                left_path=None if left_script is None else "target.py",
                right_path=None if right_script is None else "target.py",
                left_label=context.left_label,
                right_label=context.right_label,
            )
            for bay in flatfile_bays(
                left_script,
                right_script,
                script_context,
            ):
                yield target_bay(bay)
            return
    for bay in flatfile_bays(
        nested_target_document(
            left_target,
            context.left_link,
            left_target_path,
        ),
        nested_target_document(
            right_target,
            context.right_link,
            right_target_path,
        ),
        target_context,
    ):
        yield target_bay(bay)
