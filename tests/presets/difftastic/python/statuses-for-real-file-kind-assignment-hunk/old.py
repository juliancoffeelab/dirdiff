def build_rows_payload(**kwargs):
    return kwargs


def build_payload(normalized_right, change_type, normalized_left):
    payload = build_rows_payload(
        left_path_hint="left.py",
        right_path_hint=normalized_right,
    )
    payload["change_type"] = change_type
    payload["left_path"] = normalized_left
    return payload
