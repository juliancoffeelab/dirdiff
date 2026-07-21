# Engine and navigation contract TODOs

This document records the GumTree and Lazy reason contract cleanup identified
by the fallback audit. It does not authorize changes to
implementation files. In particular, it does not change Difftastic's deliberate
degraded-result contract: that engine may return unified line-diff rows together
with an explicit warning when the third-party structural diff fails.

## Remove the stale GumTree fallback warning contract

GumTree currently has no equivalent degraded-result path. Invalid GumTree JSON
raises an engine error rather than returning unified fallback rows, but the
unused `"gumtree_invalid_json"` warning variant remains declared across the
backend and frontend:

- [`src/dirdiff/engines/base.py:334`](../../src/dirdiff/engines/base.py#L334)
  still includes it in `EngineWarning`.
- [`src/dirdiff/server.py:525`](../../src/dirdiff/server.py#L525) still includes
  it in the file-response warning contract.
- [`frontend/src/new/api/api.ts:617`](../../frontend/src/new/api/api.ts#L617)
  still accepts it at the frontend API boundary.
- [`frontend/src/new/hud/FileCard.tsx:1069`](../../frontend/src/new/hud/FileCard.tsx#L1069)
  still supplies the unreachable label “GumTree failed: unified fallback.”

A later contract cleanup should remove this dead variant on every side in one
change, including affected tests. It must not manufacture a GumTree fallback
payload merely to make the stale warning reachable, and it must not remove
Difftastic's separate warning-backed degraded-result behavior.

## F55 — Make `/api/lazy-info` reasons non-null

The general manifest and file-response contracts use a nullable Lazy reason
because `null` means the file is eager. The narrower `/api/lazy-info` response
contains only entries selected for delayed loading, so every entry necessarily
has a concrete reason. Its current backend response model nevertheless reuses
the nullable type in
[`src/dirdiff/server.py:618`](../../src/dirdiff/server.py#L618), and the frontend
schema repeats that ambiguity in
[`frontend/src/new/api/api.ts:462`](../../frontend/src/new/api/api.ts#L462).

FileCard currently encounters the impossible `null` only after parsing and
throws while choosing deferred-plank copy in
[`frontend/src/new/hud/FileCard.tsx:1205`](../../frontend/src/new/hud/FileCard.tsx#L1205).
That is too late: accepting `null` at the API boundary permits an invalid
deferred file to enter application state before presentation rejects it.

A later cross-layer contract change should give `LazyInfoFileResponse.lazy` a
required non-null reason, make `LazyInfoFileSchema.lazy` non-null, and remove the
unreachable FileCard `null` branches. Keep the broader manifest and full-file
metadata reason nullable, because `null` retains its distinct eager-file meaning
there. Update backend and frontend tests in the same change; do not add a default
reason or compatibility parser.
