from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_cli_cram_transcripts() -> None:
    env = os.environ.copy()
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    subprocess.run(
        [
            sys.executable,
            "-m",
            "cram",
            str(Path(__file__).parent / "cli-cram" / "mark.t"),
        ],
        check=True,
        env=env,
    )
