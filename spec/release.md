# Installed release

## Purpose

Dirdiff keeps one console command across two installation forms. An editable
installation is the development application. A standard installation is the
release application and contains a compiled HUD. Both forms expose the same API
and Tab behavior, but their process topology and implicit persistent state are
separate.

The first verified release platform is macOS ARM64. The implementation does not
reject other platforms, but they need their own build and browser verification
before the project claims support.

## Installation mode

The root CLI callback reads the installed `dirdiff` distribution and its
standard `direct_url.json` once. A `dir_info.editable` value of `true` selects
development. Valid non-editable directory, VCS, archive, wheel, source, and
ordinary index installations select release. Index installations normally have
no `direct_url.json`.

Missing distribution metadata, malformed JSON, and invalid direct-URL shapes
stop the command. Paths, the working directory, source files, and failed
frontend startup never change the selected mode.

## Persistent state

Explicit `--db-path`, `DIRDIFF_DB_PATH`, and `--store-path` values are
authoritative in both modes. With no explicit path, development uses:

```text
~/.local/share/dirdiff/dirdiff.sqlite
~/.local/share/dirdiff/store
```

Release uses:

```text
~/.local/share/dirdiff/release/dirdiff.sqlite
~/.local/share/dirdiff/release/store
```

The database default applies to every command, including `dirdiff mark`. When
only the database is explicit, the Snapshot store remains its sibling `store`
directory. Dirdiff does not copy or search state between modes.

## Build contract

The wheel target's custom Hatch hook runs only for a standard wheel. It creates
fresh temporary output, runs `bun install --frozen-lockfile`, and runs the
frontend `build` script. Vite receives that temporary output path through
`DIRDIFF_FRONTEND_OUT_DIR`; while serving development it requires
`VITE_DIRDIFF_BACKEND_ORIGIN` for both API proxying and copied agent-onboarding
links. The release HUD uses its own origin for those links.

The hook requires a regular `index.html` and an `assets` directory, includes the
complete output at `dirdiff/frontend`, and deletes the temporary directory after
Hatch consumes it. It does not read `frontend/dist`, write generated files into
`src/dirdiff`, or run ESLint and frontend tests. Editable wheels skip the hook's
frontend work.

The source distribution contains the build hook, frontend source and config,
`package.json`, `bun.lock`, and the three agent workflow skill directories. It
excludes `node_modules` and compiled HUD output. A wheel built from an extracted
source distribution uses only those files.

The wheel also maps the root Alembic configuration and migration history into
`dirdiff/db`. Development bootstrap uses the canonical files in the editable
checkout so migration edits take effect immediately. Release bootstrap uses the
fixed installed resource, so an installed command never needs the original
checkout to create or upgrade its schema. The validated installation mode
selects this path before any persistent database is opened.

The wheel maps the complete `review-patch`, `round-review`, and `babysit-patch`
skill directories into `dirdiff/skills`. Editable startup exposes their
canonical `.agents/skills` directories instead. Runtime configuration carries
the selected root into application construction, which requires all three
`SKILL.md` entry files before the onboarding route can be served. The route
returns their resolved absolute paths; it does not extract, copy, or search for
skills at request time.

`tree-sitter-clojure` is a public HTTPS direct dependency pinned to immutable
commit `86e2fe6dcdc973e4ca0e9e87dbbce0c34ad44f86`. That revision marks its native
wheel with the interpreter ABI and build platform. Hatch permits this direct
reference in project metadata; no uv source override is required.

## Server composition

`create_app` constructs the API, documentation, stores, route handlers, and Room
service. It has no frontend route or mode input.

The two uvicorn factories share runtime configuration decoding and dependency
construction:

- `development_uvicorn_entrypoint` adds the fixed root diagnostic used when the
  separate Vite HUD is unavailable.
- `release_uvicorn_entrypoint` requires installed `index.html` and `assets`,
  returns the entry page from `GET /`, and mounts only `/assets` as static files.

The release factory adds no catch-all route. Unknown browser and API paths keep
FastAPI's normal 404 response. Root query strings reach the same `index.html`,
so the HUD can construct its initial Tab from the URL. Relative `/api` calls stay
on the release server's origin.

## Launch behavior

Development selects a free backend and frontend port pair, starts Vite with the
backend proxy origin, opens the Vite URL unless headless, and runs uvicorn with
Python reload. `--no-frontend-dev` keeps the reloadable API and diagnostic root.
Missing Bun retains the same diagnostic path. The CLI terminates a started Vite
child after uvicorn exits.

Release probes only `--port`, opens that server URL unless headless, and runs
uvicorn without reload. It never starts Vite. Release rejects
`--no-frontend-dev` and any non-default `--frontend-port` value because neither
option describes its one-server topology.

Public source installation uses:

```sh
uv tool install git+https://github.com/juliancoffeelab/dirdiff
```

An ephemeral invocation may use:

```sh
uvx --from git+https://github.com/juliancoffeelab/dirdiff dirdiff
```

Source installation may require Bun, Git, a C compiler, Python headers, and
public network access. The installed command does not require Bun, frontend
source, `node_modules`, or a repository checkout. Git and optional diff engines
remain external requirements for the features that invoke them.
