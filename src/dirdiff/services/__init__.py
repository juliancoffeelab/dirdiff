from dirdiff.services.base import (
    DiffServiceProtocol,
)
from dirdiff.services.difftastic import DifftasticDiffService
from dirdiff.services.git import GitDiffService
from dirdiff.services.textdiff import TextDiffService, build_loaded_diff

__all__ = [
    "DiffServiceProtocol",
    "DifftasticDiffService",
    "GitDiffService",
    "TextDiffService",
    "build_loaded_diff",
]
