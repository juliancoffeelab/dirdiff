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

It resolves refs, lists changed paths, builds manifests and lazy metadata,
loads file sides, prepares pull requests, and stores the temporary cache handle
used by follow-up file loads.

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

It defines `DiffRow`, `SyntaxClass`, `SyntaxSpan`, and `FoldHint`. It attaches
syntax spans, fold hints, and hunk indexes without changing the engine's
alignment or summary.

Its public interface is exported from `dirdiff.rendering`.

## `dirdiff.notebooks`

Builds notebook-shaped file payloads.

It parses notebook structure, uses the selected engine for cell source diffs,
uses the native text engine for metadata and output text, and sends resulting
rows through rendering enrichment.

## `dirdiff.db`

Persists repository marks, profiles, preferences, and repository defaults in
SQLite.

Its public interface is exported from `dirdiff.db`.

## `dirdiff.server`

Defines FastAPI routes and application orchestration.

It validates HTTP inputs and outputs, selects backends and engines, routes
notebooks, and assembles response payloads from backend, engine, and rendering
results.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, builds runtime configuration, starts the server, opens
the browser, and exposes repository-mark commands.
