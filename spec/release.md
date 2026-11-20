# Installed release

Dirdiff keeps one console command across two installation forms. An editable
installation is the development application. A standard installation is the
release application and contains a compiled HUD. Both forms expose the same API
and Tab behavior, but their process topology and implicit persistent state are
separate.

The first verified release platform is macOS ARM64. The implementation does not
reject other platforms, but they need their own build and browser verification
before the project claims support.

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
- Preset files go to `dirdiff/tests/presets`. Standard wheels omit the
  `formats/symlinks` group and the `formats/invalid/broken-link` and
  `formats/invalid/link-loop` cases because wheel installation cannot preserve
  their required filesystem symlinks; the hook creates no symlinks.

The CLI resolves one absolute Preset root before constructing runtime
configuration (checkout `tests/presets` when editable, the installed resource
otherwise; an explicit `--presets-root` is authoritative). The server receives
the chosen path and performs no installation discovery.

Vite reads the output directory from `DIRDIFF_FRONTEND_OUT_DIR` for builds and
requires `VITE_DIRDIFF_BACKEND_ORIGIN` when serving, so a development HUD is
always paired with its backend.

`tree-sitter-clojure` is a public HTTPS direct dependency pinned to immutable
commit `86e2fe6dcdc973e4ca0e9e87dbbce0c34ad44f86`. That revision marks its native
wheel with the interpreter ABI and build platform. Hatch permits the direct
reference through `allow-direct-references`.

## Server composition

`create_app` has no frontend route or mode input. The release factory
(`release_uvicorn_entrypoint`) returns the entry page from `GET /` and mounts
only `/assets`; it adds no catch-all route, so unknown paths keep FastAPI's
404. Root query strings reach the same `index.html`, so the HUD can construct
its initial Tab from the URL. The entry page is served with
`Cache-Control: no-cache`, so a browser revalidates it on every load and a
reinstalled release never runs the previous release's script against the new
API.

## Launch behavior

Release is one server: it takes the first free port from `--port` through the
next 19, runs uvicorn without reload, and never starts Vite. It rejects
`--no-frontend-dev` and any non-default `--frontend-port`.

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
