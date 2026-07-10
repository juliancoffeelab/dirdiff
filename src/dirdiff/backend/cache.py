"""Process-local cache for backend request context.

`/api/manifest` is the request that discovers changed paths and resolves the
left/right backend sides.  Follow-up endpoints such as `/api/file-diff` must
reuse that operational cache entry instead of reconstructing it from query
parameters.  This module owns that short-lived cache-id storage.

The cache is intentionally in-process and app-scoped.  It is cleared by a
server restart, stores at most one active cache entry per repo, and does not
know how manifests are serialized or how files are rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, override
from uuid import uuid4

from dirdiff.backend.base import RepoDiffPath, SideName

__all__ = [
    "CacheBackendProtocol",
    "MemoryCacheBackend",
    "RepoInfo",
]


@dataclass(frozen=True)
class RepoInfo:
    """Resolved backend state reused by follow-up file requests.

    The paths and side names are computed once while building the manifest.
    Lazy-info and file-diff requests then address this object by opaque id so
    they cannot drift if refs, worktree state, or preset params change between
    requests.
    """

    left_side: SideName
    right_side: SideName
    left_label: str
    right_label: str
    paths: tuple[RepoDiffPath, ...]


class CacheBackendProtocol(Protocol):
    """Process-local storage boundary for backend request cache entries."""

    def store_repo_info(self, *, project_id: int, repo_info: RepoInfo) -> str:
        """Store a new repo cache entry and invalidate the previous one for that repo."""
        ...

    def repo_info(
        self,
        *,
        project_id: int,
        cache_id: str,
    ) -> RepoInfo | None:
        """Return repo cache entry only when the id still belongs to that repo."""
        ...


class MemoryCacheBackend(CacheBackendProtocol):
    """Thread-safe in-memory cache cleared naturally when the process exits."""

    def __init__(self) -> None:
        """Keep cache state per FastAPI app instance.

        Uvicorn reloads import a fresh app, so this object naturally loses all
        cache entries on restart.  Tests can also inject a separate instance
        instead of sharing global state.
        """
        self._lock: Lock = Lock()
        self._repo_info_by_key: dict[tuple[int, str], RepoInfo] = {}
        self._latest_cache_id_by_repo: dict[int, str] = {}

    @override
    def store_repo_info(self, *, project_id: int, repo_info: RepoInfo) -> str:
        """Create the only live cache id for a repo.

        A new manifest load means later file fetches must use the newly resolved
        sides and path list.  Pruning the previous id prevents stale lazy-file
        hydration from silently reading repo info from an older load.
        """
        cache_id = uuid4().hex
        with self._lock:
            previous_id = self._latest_cache_id_by_repo.get(project_id)
            if previous_id is not None:
                _ = self._repo_info_by_key.pop((project_id, previous_id), None)
            self._latest_cache_id_by_repo[project_id] = cache_id
            self._repo_info_by_key[(project_id, cache_id)] = repo_info
        return cache_id

    @override
    def repo_info(
        self,
        *,
        project_id: int,
        cache_id: str,
    ) -> RepoInfo | None:
        """Look up repo info by both repo and cache id.

        The project id is part of the key so an opaque id from one marked repo
        cannot be replayed against another repo's follow-up endpoints.
        """
        with self._lock:
            return self._repo_info_by_key.get((project_id, cache_id))
