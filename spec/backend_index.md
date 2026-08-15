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
operations. Room methods require that key explicitly while providing metadata,
manifested filepath iteration, direct captured-file lookup, and the three
Thread operations `threads`, `get_thread`, and `create_thread`. The agent
boundary also uses explicit activity reads, an atomic review batch,
persisted-Tab recapture, and read-only access to the already published Snapshot
directory. Capture and
publication stores remain private. The module calls `dirdiff.db` and does not
issue SQL.

The Room and Snapshot lifecycle is described in
[`rooms.md`](rooms.md).

## `dirdiff.review`

Implements persistent review discussions. Its public `Thread` is bound to one
exact `(snapshot_id, thread_id)` pair and performs Comment and lifecycle
operations through that placement. It selects the latest persisted lifecycle
and attention outcome, folds Comment content, and reconstructs original code
context as a bounded selected-side excerpt from immutable captured Files,
independently of every rendering engine.

The module privately derives missing placements only for a genuinely new
Snapshot; selecting an equal retained Snapshot performs no derivation. Private
source coordinates do not cross Room, HTTP, frontend, draft, or cache
boundaries. Public matching outcomes are limited to `region_changed`,
`region_not_found`, and `file_missing`. The review model is described in
[`reviews.md`](reviews.md).

## `dirdiff.server`

Defines FastAPI routes and request-level rendering orchestration.

It validates HTTP inputs and outputs, constructs the concrete workspace backend,
calls `RoomLord` for manifests and follow-up Snapshot lookup, selects engines,
routes notebooks, and assembles response payloads. Snapshot-keyed browser review
routes read one bounded Thread page and apply Profile-authored Thread and
Comment actions directly to stored action sequences. Existing-Thread writes
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
