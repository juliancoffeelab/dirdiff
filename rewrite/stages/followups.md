# Explicit follow-ups after the frontend rewrite

This file records work deliberately kept outside practical rewrite Chapters 1–9. These items are not hidden implementation tasks and must not be pulled into a frontend stage merely because that stage exposes the existing backend behavior. Each follow-up requires a separate user decision before implementation, especially where the current integration tests assert the existing contract.

## TODO — repository manifest cache lifetime

The process-local repository cache currently retains only the newest manifest `cache_id` for each marked repository. Requesting another manifest for the same repository deletes the previous cache entry.

The frontend handles this disposable backend lifetime through the complete ChangeSet snapshot replacement specified in `../spec/01_tanstack_query.md`. An unknown repository cache ID is an expected expiration indication rather than one localized file failure.

A separate follow-up may still reconsider process-lifetime retention, bounded retention, request-identity retention, or an explicit lease/release contract. The current integration test requires older repository cache IDs to fail, so changing the backend policy also requires explicit approval to change that tested behavior.

## TODO — structured repository cache expiration

The HTTP transport currently recognizes repository cache expiration by matching the human-readable `detail` prefix `"Unknown cache id: "`. The backend should return a stable machine-readable cache-expiration code, and `api.ts` should classify that code instead. Display text must not determine ChangeSet lifecycle behavior; the existing complete-snapshot restart remains unchanged.

## TODO — synthetic file admission

Re-evaluate whether correct snapshot disposal and sequential network-backed file loading make `schedulerYield`, `admittedFiles`, and the `admitted` FileCard contract unnecessary. Do not remove them during the current lifecycle correction.

## TODO — Difftastic subprocess lifecycle and resource isolation

The Difftastic graph limit is intentionally raised to `10_000_000` so structurally useful diffs do not prematurely fall back to line-oriented output. Preserve that limit unless a separate output-quality investigation explicitly approves changing it; resource isolation must not silently reduce Difftastic result quality.

The current synchronous `/api/file-diff` path waits in `subprocess.run()`. When a bounded frontend HTTP attempt times out and disconnects, its `difft` subprocess can continue consuming CPU and memory. The frontend then advances its sequential file lane, so later HTTP attempts can start additional Difftastic subprocesses even though the abandoned backend computations are still alive. Frontend sequencing therefore does not currently guarantee backend sequencing.

A separate backend follow-up must introduce a per-operation structure that stores the Difftastic child-process handle. Permit at most one active Difftastic subprocess, terminate and reap it when the corresponding HTTP operation disconnects or is cancelled, and release the capacity slot only after cleanup completes. An explicitly unbounded file `RetryButton` attempt remains connected and must not acquire a new wall-clock timeout through this work.

Prefer cross-platform process lifecycle management and capacity limiting through AnyIO. `psutil` may be evaluated for portable process-tree cleanup, priority reduction and resource observation. Hard CPU or memory quotas remain platform-specific and are optional defence in depth; they must not replace correct cancellation, cleanup or the single-process capacity invariant.

## FIXME — preset manifests have no snapshot identity

This is a backend snapshot-identity bug deferred until after the frontend rewrite.

Preset manifests currently return `cache_id: ""`, and preset lazy-info/file-diff requests reconstruct current fixture state. Reloading a changed preset can therefore produce a new manifest while canonical TanStack file keys still reuse results from the previous fixture contents.

The frontend rewrite must continue using the stable backend response and must not invent a frontend generation counter or routinely remove file queries to disguise the missing identity.

A separate follow-up must give preset manifests meaningful snapshot identity. Candidate backend designs include a content-derived ID with follow-up validation or a real cached preset snapshot. Existing integration tests assert the empty ID, so the chosen backend correction requires explicit approval to update that tested contract.
