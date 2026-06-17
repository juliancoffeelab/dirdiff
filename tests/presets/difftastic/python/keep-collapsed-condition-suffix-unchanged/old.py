from dataclasses import dataclass

LARGE_CHANGED_LINES_LAZY_THRESHOLD = 500


@dataclass
class Entry:
    left_path: str
    right_path: str
    change_type: str
    changed_lines: int | None
    display_name: str


def to_lazy_repo_manifest_file_entry(entry: Entry) -> dict[str, object]:
    payload: dict[str, object] = {
        "left_path": entry.left_path,
        "right_path": entry.right_path,
        "change_type": entry.change_type,
        "lazy": True,
    }
    if (
        entry.changed_lines is not None
        and entry.changed_lines > LARGE_CHANGED_LINES_LAZY_THRESHOLD
    ):
        payload["lazy_reason"] = (
            f"{entry.display_name} has {entry.changed_lines} changed lines, "
            "so it is folded by default. Click to fetch and open it."
        )
    return payload
