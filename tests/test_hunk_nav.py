from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS_TEST_FILES = sorted(str(path) for path in (ROOT / "tests" / "js").glob("*.cjs"))


def test_hunk_nav_javascript_regressions() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run hunk navigation regression tests")
    if not JS_TEST_FILES:
        pytest.skip("no JavaScript hunk navigation tests found")

    result = subprocess.run(
        [node, "--test", *JS_TEST_FILES],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
