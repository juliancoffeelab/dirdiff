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
file contents without decoding them, and prepares pull requests. Additional
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
present. `db.room` contains these relations, immutable records, and transactions
through `RoomStore`.

Its public interface is exported from `dirdiff.db`.

## `dirdiff.room_lord`

Exposes `RoomLord` and `Room`. `RoomLord.corresponding_room` applies a Tab's law
for manifest and returns the Room and current Snapshot key separately;
`RoomLord.find_room` recovers a Room from an existing Snapshot key for follow-up
operations. Room methods require that key explicitly while providing metadata,
manifested filepath iteration, and direct captured-file lookup. Capture and
publication stores remain private. The module calls `dirdiff.db` and does not
issue SQL.

The Room and Snapshot lifecycle is described in
[`rooms.md`](rooms.md).

## `dirdiff.server`

Defines FastAPI routes and request-level rendering orchestration.

It validates HTTP inputs and outputs, constructs the concrete workspace
backend, calls `RoomLord` for manifests and follow-up Snapshot lookup, selects
engines, routes notebooks, and assembles response payloads.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, builds runtime configuration, starts the server, opens
the browser, and exposes repository-mark commands.
