# Backend modules

## Purpose

This document maps the Python modules and the responsibilities between them.

## Application flow

```text
CLI
 └─ server
     ├─ backend
     ├─ db
     ├─ engines
     ├─ notebooks
     └─ rendering

notebooks ──► backend contracts
          ├─► engines
          └─► rendering

rendering ──► engines
backend ──► engine contracts
```

Dependencies do not point from engines into rendering.

## `dirdiff.backend`

Provides repository and preset data.

It resolves refs, lists changed paths and stable File metadata, reports
backend-reported aggregate added/removed line counts when available, loads exact
file contents without decoding them, and prepares pull requests. Pull Request
preparation parses the URL, identifies the marked repository and forge remote,
fetches the required refs, and returns the canonical URL with the merge-base and
Pull Request head commits. Additional
untracked Files need not participate in those backend totals. Loading a listed
File either returns its complete contents or raises `DirdiffError` with the
backend failure reason. Workspace backends do not select Rooms, retain state
between HTTP requests, classify contents for renderers, or invent
renderer-dependent line counts.

Its public interface is exported from `dirdiff.backend`.

## `dirdiff.engines`

Compares two already-loaded text sides.

The package defines `DirdiffError`, the shared failure contract used when
dirdiff cannot safely produce a requested result.

An engine produces neutral aligned rows, inline diff tokens, summary counts,
and an engine warning when applicable. It does not load files or attach syntax
highlighting, folds, or hunk indexes. Difftastic keeps its degraded textual
diff construction inside its own implementation.

The concrete engines are difftastic, Git no-index, GumTree, the native
line-first text engine (`textdiff`, engine param `dirdiff`), and the
token-first text engine (`tokendiff`, engine param `tokendiff`). Tokendiff
diffs whole token streams anchored on lstrip-equal lines, so content moving
across line boundaries diffs at word granularity, whitespace is always
diffed, and oversized regions degrade to one-sided rows with an explicit
`tokendiff_region_limit` warning. Its row guarantees are asserted by the
corpus property tests in `tests/tokendiff/`.

The package also owns engine selection. `EngineKind` is the complete set of
engine names, and `engine()` maps one to its renderer; both are exported from
the package root. `engine()` is defined in `__init__.py` rather than `base.py`
because selection must reach the concrete engine classes, whose modules import
their contracts from `base.py`.

Its public interface is exported from `dirdiff.engines`.

## `dirdiff.rendering`

Enriches neutral engine rows for display.

It defines `DiffRow`, `SyntaxClass`, `SyntaxSpan`, `DecoratedPart`, and
`FoldHint`. It combines engine inline tokens with syntax spans into one lossless
part sequence per row side, then attaches fold hints and hunk indexes without
changing the engine's alignment or summary.

Its public interface is exported from `dirdiff.rendering`.

## `dirdiff.notebooks`

Builds notebook-shaped file payloads.

It parses notebook structure, uses the selected engine for cell source diffs,
uses the native text engine for metadata and output text, and sends resulting
rows through rendering enrichment.

## `dirdiff.db`

Persists repository marks, profiles, preferences, repository defaults, Rooms,
Snapshots, Snapshot metadata, Files, and present left/right File sides in
SQLite. Snapshot metadata retains human labels and nullable aggregate
added/removed counts. A File records its absolute capture-directory path,
tracked provenance, backend change classification, and an optional capture
error. Separate non-null side rows record repository paths and content digests;
optional relations record a lazy override and its complete source metadata when
present. Review persistence adds one placement row per Thread/Snapshot pair and
append-only actions authored through one existing ordinary Profile. The
shared internal Profile table contract lets Profile and Room persistence
reference the same identity. Profile usernames are globally unique and select
one existing local identity without credentials. Agent registration adds a one-to-one UUID binding
without changing that Profile shape. `db.room` contains these relations,
immutable records, and transactions through `RoomStore`.

Its public interface is exported from `dirdiff.db`.

## `dirdiff.room_lord`

Exposes `RoomLord` and `Room`. `RoomLord.corresponding_room` applies a Tab's law
for the shared manifest/agent-review capture and returns the Room and current
Snapshot key separately;
`RoomLord.find_room` recovers a Room from an existing Snapshot key for follow-up
operations. RoomLord does nothing beyond handing out Rooms. Room methods
require that key explicitly while providing metadata, manifested filepath
iteration, direct captured-file lookup, and Thread access through `threads`,
`get_thread`, `thread_for_comment`, and `create_thread`; Comment and lifecycle
writes belong to the returned bound Threads. The agent boundary also uses the
Room's explicit activity reads, atomic review batch, persisted-Tab
`capture_context` and `recapture`, and read-only `path_for_snapshot` access to the
already published Snapshot directory. Capture and
publication stores remain private. The module calls `dirdiff.db` and does not
issue SQL.

The Room and Snapshot lifecycle is described in
[`rooms.md`](rooms.md).

## `dirdiff.review`

Implements persistent review discussions. Its public `Thread` is bound to one
exact `(snapshot_id, thread_id)` pair and performs Comment and lifecycle
operations through that placement, returning each write's bounded update view.
The Thread is a lightweight handle: it loads its referenced Snapshot Files only
when a read interprets placement, so writes never pay for File hydration. It
selects the latest persisted lifecycle and attention outcome, folds Comment
content, and reconstructs original code context as a bounded selected-side
excerpt from immutable captured Files, independently of every rendering engine.

The module privately derives missing placements only for a genuinely new
Snapshot; selecting an equal retained Snapshot performs no derivation. Private
source coordinates do not cross Room, HTTP, frontend, draft, or cache
boundaries. Public matching outcomes are limited to `region_changed`,
`region_not_found`, and `file_missing`. The review model is described in
[`reviews.md`](reviews.md).

## `dirdiff.server`

Defines FastAPI routes and request-level rendering orchestration.

It validates HTTP inputs and outputs, constructs the concrete workspace backend,
calls `RoomLord` for manifests and follow-up Snapshot lookup, asks
`dirdiff.engines` for the renderer a request names, routes notebooks, and
assembles response payloads. Snapshot-keyed browser review
routes read one bounded Thread page and apply Profile-authored Thread and
Comment actions through the Room's bound Threads. Existing-Thread writes
return only current state and the changed Comment.
Agent review routes register an ordinary Profile with its agent UUID, capture a
logical Tab into the same Snapshot identity, expose captured changed Files on
disk, page open Threads, recapture the persisted Tab with File and authored
Thread deltas, role-filtered attention inboxes, and one atomic batch of the
role-specific review instruments.
The agent-visible Snapshot filesystem contract is documented in
[`reviews.md`](reviews.md) and mirrored by both project-local agent skills;
changes to capture layout must update those operational references together.
Browser review failures expose stable structured codes and messages.
Profile routes explicitly select an existing exact username, create a unique
username, or rename one Profile to another unique username.
Unexpected HTTP failures are logged with their method, path, and traceback at
the application boundary before the generic internal-error response is sent.
`/api/manifest` receives the
complete selected Tab parameters, shows that state, and provides Snapshot/File
keys; it performs no Pull Request preparation. `/api/pull-request/prepare` is the
sole HTTP boundary for Pull Request preparation.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, builds runtime configuration, starts the server, opens
the browser, and exposes repository-mark commands.
