from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_hunk_nav_javascript_regressions() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to run hunk navigation regression tests")

    result = subprocess.run(
        [node, "--test", "tests/js/hunk_nav.test.cjs"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
