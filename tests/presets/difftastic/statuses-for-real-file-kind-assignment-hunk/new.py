def build_rows_payload(**kwargs):
    return kwargs


def _file_kind_for_change_type(change_type, *, file_kind=None):
    return {"type": "git", "status": file_kind or change_type}


def build_payload(normalized_right, change_type, file_kind, normalized_left):
    payload = build_rows_payload(
        left_path_hint="left.py",
        right_path_hint=normalized_right,
    )
    payload["file_kind"] = _file_kind_for_change_type(
        change_type,
        file_kind=file_kind,
    )
    payload["left_path"] = normalized_left
    return payload
