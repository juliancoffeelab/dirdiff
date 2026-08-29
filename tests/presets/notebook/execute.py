#!/usr/bin/env python3
"""Execute the notebook presets that should contain fresh kernel output.

Run this file from the repository root with
``uv --no-cache run tests/presets/notebook/execute.py``. It discovers notebook
fixtures below this directory, executes each included file once with the Python
kernel, and writes the kernel result back to that file. Notebook source remains
hand-written; this script does not create cells or change their source.

``EXCLUDED_NOTEBOOKS`` is the complete exception list. Each entry explains why
the file must not execute. The traceback fixture captures its expected
``IndexError``; every other execution error stops the script and remains visible
to the caller.
"""

from pathlib import Path

import nbformat
from nbclient import NotebookClient


__all__: list[str] = []

ROOT = Path(__file__).parent

EXCLUDED_NOTEBOOKS = {
    # these we dont execute cause this is hand-coded-ish fixture of
    # legacy version of notebooks
    Path("invalid/idless-degraded/new.ipynb"):
        "its malformed output and missing ids are deliberate",
    Path("invalid/idless-degraded/old.ipynb"):
        "its malformed output and missing ids are deliberate",
    # these we dont execute cause like, it's malformed
    Path("invalid/not-valid-notebook-json/new.ipynb"):
        "it is deliberately not notebook JSON",
    Path("invalid/not-valid-notebook-json/old.ipynb"):
        "it is deliberately not notebook JSON",
    # these two we dont execute one side of them cause we want to see
    # inserts and deletes
    Path("unchanged/plot-output-added/old.ipynb"):
        "the plot cell must remain unexecuted",
    Path("unchanged/plot-output-removed/new.ipynb"):
        "the plot cell must remain unexecuted",
}


def main() -> None:
    """Execute every ordinary fixture and report each explicit exclusion."""
    notebook_paths = sorted(ROOT.glob("*/*/*.ipynb"))
    assert notebook_paths, f"no notebook presets found below {ROOT}"

    for notebook_path in notebook_paths:
        relative_path = notebook_path.relative_to(ROOT)
        reason = EXCLUDED_NOTEBOOKS.get(relative_path)
        if reason is not None:
            print(f"skip    {relative_path}: {reason}")
            continue

        notebook = nbformat.read(notebook_path, as_version=4)
        NotebookClient(
            notebook,
            timeout=60,
            kernel_name="python3",
            record_timing=False,
            allow_error_names=(
                ["IndexError"]
                if relative_path
                == Path("basic/error-traceback-appears/new.ipynb")
                else []
            ),
        ).execute()
        nbformat.write(notebook, notebook_path)
        print(f"execute {relative_path}")


if __name__ == "__main__":
    main()
