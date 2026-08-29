"""Cram transcript test for the installed CLI command surface.

This module runs the checked-in terminal transcript through `cram` using the
current Python environment.  It verifies user-facing CLI behavior and output,
not server internals or browser rendering.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__: list[str] = []


def test_cli_cram_transcripts() -> None:
    """Run the Mark CLI transcript with the current environment's executables.

    Prepending the active Python executable directory makes Cram invoke the same
    editable dirdiff installation as pytest. Any command failure or output drift
    fails the transcript directly.
    """
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cram",
            str(Path(__file__).parents[1] / "cli-cram" / "mark.t"),
        ],
        check=True,
        env=env,
    )
