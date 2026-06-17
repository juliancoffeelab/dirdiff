from dataclasses import dataclass


@dataclass
class Entry:
    left_path: str
    right_path: str
    change_type: str
    changed_lines: int | None
    display_name: str


def _file_kind_for_repo_entry(entry: Entry) -> dict[str, str]:
    return {"type": "git", "status": entry.change_type}


def _lazy_reason_for_repo_entry(entry: Entry) -> str | None:
    if entry.changed_lines is None:
        return None
    return f"{entry.display_name} has {entry.changed_lines} changed lines"


def to_lazy_repo_manifest_file_entry(entry: Entry) -> dict[str, object]:
    payload: dict[str, object] = {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "file_kind": _file_kind_for_repo_entry(entry),
    }
    lazy = _lazy_reason_for_repo_entry(entry)
    if lazy is not None:
        payload["lazy"] = lazy
    return payload
