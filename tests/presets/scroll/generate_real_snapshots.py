"""Regenerate readable Scroll presets from one stable real comparison.

This script is the construction interface for the Scroll preset catalog. It
extracts selected old/new blobs from repository history, assigns readable
ordered fixture names, and applies explicit lazy classifications where a
scenario needs them. Most fixtures share one stable comparison; explicit
per-fixture ref pairs and empty sides preserve focused historical additions and
deletions alongside the compact aggressively-folded middle of the sandwich
preset. It must not alter application code, invent source contents, or retain
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
    "many-files": [
        (
            "01-frontend-src-new-api-queryClient",
            "frontend/src/new/api/queryClient.tsx",
            None,
        ),
        (
            "02-frontend-src-new-comp-Select",
            "frontend/src/new/comp/Select.tsx",
            None,
        ),
        (
            "03-frontend-src-new-comp-AutocompleteInput",
            "frontend/src/new/comp/AutocompleteInput.tsx",
            None,
        ),
        (
            "04-frontend-src-new-comp-Toasts",
            "frontend/src/new/comp/Toasts.tsx",
            None,
        ),
        (
            "05-frontend-src-new-hud-NotebookFile",
            "frontend/src/new/hud/NotebookFile.tsx",
            None,
        ),
        (
            "06-frontend-src-new-hud-folds",
            "frontend/src/new/hud/folds.ts",
            None,
        ),
        (
            "07-frontend-src-new-hud-AppHeader",
            "frontend/src/new/hud/AppHeader.tsx",
            None,
        ),
        (
            "08-frontend-src-new-hud-navigation",
            "frontend/src/new/hud/navigation.tsx",
            None,
        ),
        (
            "09-frontend-src-new-hud-App",
            "frontend/src/new/hud/App.tsx",
            None,
        ),
        (
            "10-frontend-src-new-hud-Profile",
            "frontend/src/new/hud/Profile.tsx",
            None,
        ),
        (
            "11-added-profile-snapshot",
            "tests/presets/scroll/many-files/10-frontend-src-new-hud-Profile/old.tsx",
            None,
        ),
        (
            "12-lazy-deleted-server",
            "tests/presets/scroll/long-context/01-src-dirdiff-server/new.py",
            "deleted",
        ),
        (
            "13-trailing-query-client",
            "frontend/src/new/api/queryClient.tsx",
            None,
        ),
    ],
    "sandwich": [
        ("01-frontend-src-App", "frontend/src/App.tsx", None),
        ("02-src-dirdiff-rendering-fold", "src/dirdiff/rendering/fold.py", None),
        ("03-src-dirdiff-server", "src/dirdiff/server.py", None),
    ],
}

REF_OVERRIDES = {
    (
        "sandwich",
        "02-src-dirdiff-rendering-fold",
    ): (
        "f9727e6ba7a4e97836717dca540e1092fd4c88c1",
        "1caf2662bb2c54919bb8c235025a01f9017f6636",
    ),
    (
        "many-files",
        "11-added-profile-snapshot",
    ): (
        "75f2953d270d280f5fbabacdd6bcbb33a10ac394",
        "eee27276d387b2e2d3b2219cc81fd4c47602a2ba",
    ),
    (
        "many-files",
        "12-lazy-deleted-server",
    ): (
        "75f2953d270d280f5fbabacdd6bcbb33a10ac394",
        "eee27276d387b2e2d3b2219cc81fd4c47602a2ba",
    ),
}

# These exact historical changes added or deleted their source paths.
# PresetBackend requires both files, so generation keeps the missing side empty.
EMPTY_OLD_SIDES = {
    ("many-files", "11-added-profile-snapshot"),
}
EMPTY_NEW_SIDES = {
    ("many-files", "12-lazy-deleted-server"),
}

GROUP_REF_OVERRIDES = {
    "many-files": (
        "ee08619",
        "75f2953",
    ),
}

SUPERSEDED_GROUPS = {
    "alternating",
    "dense-hunks",
    "direction-reversal",
    "folded-height",
    "lazy-arrival",
    "lazy-placement",
    "long-context",
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
            override = REF_OVERRIDES.get((group_name, fixture_name))
            if override is None:
                group_override = GROUP_REF_OVERRIDES.get(group_name)
                if group_override is None:
                    left_ref = LEFT_REF
                    right_ref = RIGHT_REF
                else:
                    left_ref, right_ref = group_override
            else:
                left_ref, right_ref = override
            fixture_dir = SCROLL_ROOT / group_name / fixture_name
            fixture_dir.mkdir(parents=True)
            suffix = Path(source_path).suffix
            (fixture_dir / f"old{suffix}").write_bytes(
                b""
                if (group_name, fixture_name) in EMPTY_OLD_SIDES
                else git_blob(left_ref, source_path)
            )
            (fixture_dir / f"new{suffix}").write_bytes(
                b""
                if (group_name, fixture_name) in EMPTY_NEW_SIDES
                else git_blob(right_ref, source_path)
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
