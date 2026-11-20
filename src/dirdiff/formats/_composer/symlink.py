from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from dirdiff.change_source import CapturedLink
from dirdiff.engines import DiffSide
from dirdiff.formats.base import (
    Bay,
    BayContext,
    TextBay,
    try_decode_text,
    whole_file_change,
)
from dirdiff.util import Nothing, Ok, Option, Some

__all__ = [
    "nested_symlink_target_document",
    "read_captured_link",
    "symlink_composition",
    "symlink_target_bay",
    "write_captured_link",
]


SYMLINK_BAY_KEY = "symlink"


SYMLINK_TARGET_BAY_KEY = "symlink-target"


def write_captured_link(
    link: CapturedLink,
    *,
    metadata_path: Path,
    target_capture_path: Option[Path],
) -> tuple[bytes, Option[bytes]]:
    assert metadata_path.is_absolute(), (
        f"Snapshot link metadata path must be absolute: {metadata_path}"
    )
    assert Some.present(target_capture_path) == Some.present(
        link.target_data
    ), "Snapshot link target path and bytes must have equal presence"
    if Some.present(target_capture_path):
        assert target_capture_path.value.is_absolute(), (
            "Snapshot link target capture path must be absolute: "
            f"{target_capture_path.value}"
        )
    metadata_data = json.dumps(
        {
            "nested_links": link.nested_links,
            "diagnosis": link.diagnosis.get(),
            "target_path": link.target_path.get(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    metadata_path.write_bytes(metadata_data)
    if Some.present(link.target_data):
        target_capture_path.must().write_bytes(link.target_data.value)
    return (
        hashlib.sha256(metadata_data).digest(),
        link.target_data.map(lambda data: hashlib.sha256(data).digest()),
    )


def read_captured_link(
    *,
    metadata_path: Path,
    metadata_hash: bytes,
    target_capture_path: Option[Path],
    target_hash: Option[bytes],
) -> CapturedLink:
    assert metadata_path.is_absolute(), (
        f"Snapshot link metadata path must be absolute: {metadata_path}"
    )
    assert len(metadata_hash) == 32, (
        "Snapshot link metadata hash must have length 32"
    )
    assert Some.present(target_capture_path) == Some.present(target_hash), (
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
        assert diagnosis is not None and not Some.present(
            target_capture_path
        ), f"failed Snapshot link carries target bytes: {metadata_path}"
        return CapturedLink(
            nested_links=tuple((item[0], item[1]) for item in nested_links),
            diagnosis=Some(diagnosis),
            target_path=Nothing(),
            target_data=Nothing(),
        )
    assert diagnosis is None, (
        f"successful Snapshot link carries a diagnosis: {metadata_path}"
    )
    assert isinstance(captured_path, str) and captured_path != "", (
        f"invalid Snapshot link target path: {metadata_path}"
    )
    assert Some.present(target_capture_path) and Some.present(target_hash), (
        f"successful Snapshot link has no target bytes: {metadata_path}"
    )
    assert target_capture_path.value.is_absolute(), (
        "Snapshot link target capture path must be absolute: "
        f"{target_capture_path.value}"
    )
    assert len(target_hash.value) == 32, (
        "Snapshot link target hash must have length 32"
    )
    target_data = target_capture_path.value.read_bytes()
    assert hashlib.sha256(target_data).digest() == target_hash.value, (
        f"Snapshot link target hash mismatch: {target_capture_path.value}"
    )
    return CapturedLink(
        nested_links=tuple((item[0], item[1]) for item in nested_links),
        diagnosis=Nothing(),
        target_path=Some(captured_path),
        target_data=Some(target_data),
    )


@dataclass(frozen=True)
class SymlinkComposition:
    link_bay: TextBay

    left_target: Option[bytes]

    right_target: Option[bytes]

    target_context: BayContext


def symlink_composition(
    left: Option[bytes],
    right: Option[bytes],
    context: BayContext,
) -> SymlinkComposition:
    assert Some.present(context.left_link) or Some.present(
        context.right_link
    ), "link composition requires captured link facts"

    def link_payload(data: bytes) -> str:
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

    if not Some.present(context.left_link):
        left_payload: Option[str] = Nothing()
    else:
        assert Some.present(left)
        left_payload = left.map(link_payload)
    if not Some.present(context.right_link):
        right_payload: Option[str] = Nothing()
    else:
        assert Some.present(right)
        right_payload = right.map(link_payload)
    link_bay = TextBay(
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
            exists=Some.present(left_payload),
            text=left_payload.get(),
            path_hint="link",
        ),
        right=DiffSide(
            exists=Some.present(right_payload),
            text=right_payload.get(),
            path_hint="link",
        ),
    )

    def stopped_target(link: Option[CapturedLink]) -> Option[bytes]:
        if not Some.present(link) or Some.present(link.value.target_data):
            return Nothing()
        assert Some.present(link.value.diagnosis)
        blocks = [
            f"# %% {path}\n{payload}"
            for path, payload in link.value.nested_links
        ]
        blocks.append(f"# {link.value.diagnosis.must()}")
        return Some(("\n\n".join(blocks) + "\n").encode())

    left_stopped = stopped_target(context.left_link)
    right_stopped = stopped_target(context.right_link)
    left_target = (
        left
        if not Some.present(context.left_link)
        else (
            context.left_link.value.target_data
            if Some.present(context.left_link.value.target_data)
            else left_stopped
        )
    )
    right_target = (
        right
        if not Some.present(context.right_link)
        else (
            context.right_link.value.target_data
            if Some.present(context.right_link.value.target_data)
            else right_stopped
        )
    )
    left_target_path = (
        context.left_path
        if not Some.present(context.left_link)
        else Some(
            context.left_link.value.target_path.get_or("symlink-target.py")
        )
    )
    right_target_path = (
        context.right_path
        if not Some.present(context.right_link)
        else Some(
            context.right_link.value.target_path.get_or("symlink-target.py")
        )
    )
    assert Some.present(left_target) or Some.present(right_target), (
        "a captured link always yields target content or a diagnosis"
    )
    return SymlinkComposition(
        link_bay=link_bay,
        left_target=left_target,
        right_target=right_target,
        target_context=BayContext(
            left_path=left_target_path,
            right_path=right_target_path,
            left_label=context.left_label,
            right_label=context.right_label,
        ),
    )


def symlink_target_bay(bay: Bay) -> Bay:
    return replace(
        bay,
        frame_key="file",
        heading=None,
        bay_key=SYMLINK_TARGET_BAY_KEY,
        label="Target content",
        collapsible=True,
        default_expanded=False,
    )


def nested_symlink_target_document(
    data: Option[bytes],
    link: Option[CapturedLink],
    final_path: Option[str],
) -> Option[bytes]:
    if (
        not Some.present(data)
        or not Some.present(link)
        or not Some.present(link.value.target_data)
        or link.value.nested_links == ()
    ):
        return data
    decoded = try_decode_text(data.value)
    if not Ok.good(decoded):
        return data
    assert Some.present(final_path)
    blocks = [
        f"# %% {path}\n{payload}" for path, payload in link.value.nested_links
    ]
    blocks.append(f"# %% {final_path.value}\n{decoded.value}")
    return Some(("\n\n".join(blocks).rstrip("\n") + "\n").encode())
