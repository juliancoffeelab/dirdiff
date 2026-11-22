# Installed release

Dirdiff keeps one console command across two installation forms. An editable
installation is the development application. A standard installation is the
release application and contains a compiled HUD. Both forms expose the same API
and Tab behavior, but their process topology and implicit persistent state are
separate.

## Installation mode

Paths, the working directory, source files, and failed frontend startup never
change the selected mode. Only the installed distribution's `direct_url.json`
does; missing metadata, malformed JSON, and invalid direct-URL shapes stop the
command. Index installations normally have no `direct_url.json` and are release.

## Persistent state

The two modes keep separate implicit state under `~/.local/share/dirdiff/`
(development) and `~/.local/share/dirdiff/release/` (release). Dirdiff does not
copy or search state between modes. The database default applies to every
command, including `dirdiff mark`.

## Build contract

The wheel hook runs only for a standard wheel; editable wheels skip it. The
installed package carries everything it needs at runtime, so an installed
command never needs the original checkout:

- The compiled HUD goes to `dirdiff/frontend`, built into a fresh temporary
  directory that is deleted after Hatch consumes it. The hook never reads
  `frontend/dist` or writes generated files into `src/dirdiff`.
- Alembic configuration and migrations go to `dirdiff/db`. Development uses the
  canonical files in the checkout so migration edits take effect immediately.
  The installation mode selects the path before any persistent database is
  opened.
- The three agent workflow skills go to `dirdiff/skills`; development uses
  `.agents/skills`.
- Preset files go to `dirdiff/tests/presets`. Standard wheels omit the presets
  that need filesystem symlinks, because wheel installation cannot preserve
  them; the hook creates no symlinks.

The CLI resolves one absolute Preset root before constructing runtime
configuration (an explicit `--presets-root` is authoritative). The server
receives the chosen path and performs no installation discovery.

Vite reads the output directory from `DIRDIFF_FRONTEND_OUT_DIR` for builds and
requires `VITE_DIRDIFF_BACKEND_ORIGIN` when serving, so a development HUD is
always paired with its backend. A JavaScript module change triggers a full page
reload, because Solid contexts cannot span independently replaced module
graphs; CSS uses Vite's stylesheet replacement. The development backend
restarts on a Python change under `src/dirdiff` or `alembic/versions`; opening
the database on restart upgrades it, so a new migration applies to the running
server. Editing a revision the database already records does not rerun it. The
watch list is fixed when `dirdiff` launches.

A stop or a reload waits at most five seconds for open requests, then cancels
them, and uvicorn answers each cancelled request with HTTP 500. An agent `wait`
holds its request up to nine minutes, and without the limit it held the stop or
the reload for as long. The limit applies to every request: a slow one, such as
preparing a pull request (a forge call with a 20-second timeout and a `git
fetch`), also gets HTTP 500 after five seconds. Cancelling ends only the
request; a synchronous handler runs in a worker thread, and that thread
carries on with its work.

## Server composition

`create_app` has no frontend route or mode input. The release factory
(`release_uvicorn_entrypoint`) returns the entry page from `GET /` and mounts
only `/assets`; it adds no catch-all route, so unknown paths keep FastAPI's
404. Root query strings reach the same `index.html`, so the HUD can construct
its initial Tab from the URL.

Release is one server: it runs uvicorn without reload and never starts Vite.
The installed command does not require Bun, frontend source, `node_modules`, or
a repository checkout.
