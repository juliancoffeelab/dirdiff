from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

RUNTIME_CONFIG_ENV = "DIRDIFF_RUNTIME_CONFIG"
DEFAULT_DB_PATH = (
    Path.home() / ".local" / "share" / "dirdiff" / "dirdiff.sqlite"
)


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: str
    mode: Literal["head", "refs", "branch-review"] = "head"
    left: str = "head"
    right: str = "worktree"
    base_branch: str | None = None
    review_branch: str | None = None
    presets_root: str | None = None
