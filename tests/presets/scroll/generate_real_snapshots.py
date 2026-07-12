"""Regenerate readable Scroll presets from one stable real comparison.

This script is the construction interface for the Scroll preset catalog. It
extracts selected old/new blobs from the repository, assigns readable ordered
fixture names, and applies explicit lazy classifications where a scenario needs
them. It must not alter application code, invent source contents, or retain the
superseded generated-row fixture groups.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LEFT_REF = "ca11eb2d4970c6d2a04a2648b7ac768d0dc9dfec"
RIGHT_REF = "ebc9ef432de81de07f951d727861a5f8777cd383"
SCROLL_ROOT = Path(__file__).parent
PRESET_MAKEFILE = """OLD := $(firstword $(wildcard old.*))
NEW := $(firstword $(wildcard new.*))

.PHONY: diff full gum json show

diff:
\tdifft $(OLD) $(NEW)

full:
\tdiff $(OLD) $(NEW)

gum:
\tgumtree webdiff --port $${GUM_PORT:-4567} $(OLD) $(NEW) & \\
\tpid=$$!; \\
\ttrap 'kill $$pid 2>/dev/null || true' INT TERM EXIT; \\
\tsleep 1; \\
\turl=http://127.0.0.1:$${GUM_PORT:-4567}/; \\
\tif command -v open >/dev/null 2>&1; then open $$url; \\
\telif command -v xdg-open >/dev/null 2>&1; then xdg-open $$url; \\
\telse printf 'GumTree webdiff: %s\\n' $$url; fi; \\
\twait $$pid

json:
\t@DFT_UNSTABLE=yes difft --display json --context 100000000 $(OLD) $(NEW)

show:
\tbat $(OLD) $(NEW)
"""

GROUPS = {
    "mixed-file-sizes": [
        ("01-frontend-src-App", "frontend/src/App.tsx", None),
        ("02-src-dirdiff-cli-base", "src/dirdiff/cli/base.py", None),
        (
            "03-src-dirdiff-backend-pull-request",
            "src/dirdiff/backend/pull_request.py",
            None,
        ),
        ("04-frontend-src-Controls", "frontend/src/Controls.tsx", None),
        ("05-pyproject", "pyproject.toml", None),
        ("06-frontend-src-RepoPicker", "frontend/src/RepoPicker.tsx", None),
        ("07-src-dirdiff-db-base", "src/dirdiff/db/base.py", None),
        (
            "08-frontend-src-createRepoResources",
            "frontend/src/app/createRepoResources.ts",
            None,
        ),
    ],
    "many-hunks": [
        ("01-frontend-src-Controls", "frontend/src/Controls.tsx", None),
        (
            "02-frontend-src-createRepoResources",
            "frontend/src/app/createRepoResources.ts",
            None,
        ),
        ("03-src-dirdiff-server", "src/dirdiff/server.py", None),
        (
            "04-src-dirdiff-db-repo-registry",
            "src/dirdiff/db/repo_registry.py",
            None,
        ),
    ],
    "lazy-files": [
        ("01-frontend-src-App", "frontend/src/App.tsx", None),
        ("02-uv-lock", "uv.lock", "generated"),
        (
            "03-src-dirdiff-backend-pull-request",
            "src/dirdiff/backend/pull_request.py",
            None,
        ),
        (
            "04-frontend-src-RepoPicker",
            "frontend/src/RepoPicker.tsx",
            "too_big",
        ),
        ("05-frontend-src-Controls", "frontend/src/Controls.tsx", None),
    ],
    "long-context": [
        ("01-src-dirdiff-server", "src/dirdiff/server.py", None),
    ],
}

SUPERSEDED_GROUPS = {
    "alternating",
    "dense-hunks",
    "direction-reversal",
    "folded-height",
    "lazy-arrival",
    "lazy-placement",
    "sandwich",
    "scroll-follow-invariants",
    "viewport-run",
}


def git_blob(ref: str, path: str) -> bytes:
    """Return one exact repository blob or fail generation immediately."""
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        stdout=subprocess.PIPE,
    ).stdout


def main() -> None:
    """Replace generated Scroll fixtures with the configured real snapshots."""
    for group_name in SUPERSEDED_GROUPS | set(GROUPS):
        shutil.rmtree(SCROLL_ROOT / group_name, ignore_errors=True)

    for group_name, files in GROUPS.items():
        for fixture_name, source_path, lazy_reason in files:
            fixture_dir = SCROLL_ROOT / group_name / fixture_name
            fixture_dir.mkdir(parents=True)
            suffix = Path(source_path).suffix
            (fixture_dir / f"old{suffix}").write_bytes(
                git_blob(LEFT_REF, source_path)
            )
            (fixture_dir / f"new{suffix}").write_bytes(
                git_blob(RIGHT_REF, source_path)
            )
            (fixture_dir / "Makefile").write_text(
                PRESET_MAKEFILE, encoding="utf-8"
            )
            if lazy_reason is not None:
                (fixture_dir / "preset.toml").write_text(
                    f'lazy_reason = "{lazy_reason}"\n', encoding="utf-8"
                )


if __name__ == "__main__":
    main()
