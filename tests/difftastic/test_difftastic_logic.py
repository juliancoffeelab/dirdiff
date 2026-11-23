from dirdiff.engines import DiffSide
from dirdiff.engines.difftastic import DifftasticDiffEngine


def test_difftastic_engine_keeps_identical_source_rows() -> None:
    source = """value = 1
print(value)
"""

    payload = DifftasticDiffEngine(ignore_comments=False).render_diff(
        old=DiffSide(exists=True, text=source, path_hint="cell.py"),
        new=DiffSide(exists=True, text=source, path_hint="cell.py"),
    )

    assert payload["rows"] == [
        {
            "status": "equal",
            "left_no": 1,
            "right_no": 1,
            "left_text": "value = 1",
            "right_text": "value = 1",
        },
        {
            "status": "equal",
            "left_no": 2,
            "right_no": 2,
            "left_text": "print(value)",
            "right_text": "print(value)",
        },
    ]
    assert payload["summary"] == {
        "changed_lines": 0,
        "modified_lines": 0,
        "added_lines": 0,
        "removed_lines": 0,
        "moved_lines": 0,
    }
