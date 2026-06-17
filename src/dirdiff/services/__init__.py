from dirdiff.services.base import (
    DiffServiceProtocol,
    TextDiffService,
    build_loaded_diff,
)
from dirdiff.services.difftastic import DifftasticDiffService
from dirdiff.services.git import GitDiffService

__all__ = [
    "DiffServiceProtocol",
    "DifftasticDiffService",
    "GitDiffService",
    "TextDiffService",
    "build_loaded_diff",
]
