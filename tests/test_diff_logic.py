from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from file_diff_viewer.diff_logic import TextDiffService, build_loaded_diff


class BuildLoadedDiffTests(unittest.TestCase):
    def test_counts_whitespace_only_changes_as_modified(self) -> None:
        diff = build_loaded_diff(
            display_name="demo.txt",
            mode="files",
            left_label="left",
            right_label="right",
            left_exists=True,
            right_exists=True,
            left_text="    value = 1\n",
            right_text="\tvalue = 1\n",
        )

        self.assertEqual(diff["summary"]["changed_lines"], 1)
        self.assertEqual(diff["summary"]["modified_lines"], 1)
        self.assertEqual(diff["rows"][0]["status"], "equal")
        self.assertTrue(diff["rows"][0]["left_tokens"])


class FileModeServiceTests(unittest.TestCase):
    def test_builds_direct_file_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            left_file = tmp_path / "left.txt"
            right_file = tmp_path / "right.txt"
            left_file.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
            right_file.write_text("alpha\nbeta changed\ngamma\n", encoding="utf-8")

            service = TextDiffService(repo_root=None, cwd=tmp_path)
            diff = service.build_diff(
                path=None,
                left="index",
                right="worktree",
                left_file="left.txt",
                right_file="right.txt",
            )

        self.assertEqual(diff["display_name"], "left.txt vs right.txt")
        self.assertEqual(diff["summary"]["changed_lines"], 1)
        self.assertEqual(diff["summary"]["modified_lines"], 1)
        self.assertEqual(diff["summary"]["added_lines"], 0)
        self.assertEqual(diff["summary"]["removed_lines"], 0)


if __name__ == "__main__":
    unittest.main()
