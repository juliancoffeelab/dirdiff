# AGENTS

This is a browser-based review tool. Its goal is to enable humans to review the
code with ease, and having a pleasant experience while doing so. No matter
the size of the patch, or the shape of the files.
Alongside that, the goal of this project specifically, is to showcase existing
external tools and faithfully their options, even when these options have
their drawbacks.

When it comes to developing itself, every piece hot-reloads, be it the backend
or the frontend, including the migrations. Outdated state is highly
unlikely, and is a likely a bug.
This document is for agents, README.md is for humans and you are not allowed
to edit top-level README.md, ever.

This file holds what applies to every task. Procedures for one kind of task
live in skills under `.agents/skills/`.

## Permission and contradictions

- Approval covers the exact action and scope the user approved, once. Adjacent
  fixes, cleanup, tests, documentation and later turns each need their own.
  Discussion, investigation, a proposed design and approved documentation are
  not approval to edit code.
- A question is read-only. "Why X", "what about Y", "you think it should work
  like that": answer it and edit nothing.
- When user instructions, documentation, code, tests or tooling contradict one
  another, or the instructions have a gap, stop and report: the conflicting
  statements, their concrete consequence, and the reasonable choices. Wait for
  the user when the resolution changes behavior, architecture, scope or an
  invariant.

## Contracts and failures

- Fallbacks, compatibility shims, silent recovery, invented defaults and
  substitute data need the user's explicit approval before they are written,
  not after. When an interface changes, update every in-scope caller and test.
- Errors surface. JavaScript throws or shows a Toast; Python raises, or logs at
  an explicitly valid damage boundary. Damage stops at the smallest UI piece
  that cannot produce a valid result; the nearest `ErrorBoundary` contains
  unexpected throws.
- Required inputs stay required. Assert invalid data at the boundary where it
  becomes invalid: `assert()` and `expect()` from `utils.ts`, `assert` in
  Python.

## Simplicity

- One explicit action that changes the authoritative data or DOM directly, in
  place of effects, observers, implicit repair, duplicated state or several
  paths to one operation.
- One authoritative representation per piece of data. Backend query results
  stay in the query cache.
- An optional input, provider, signal, cache, queue, callback or helper layer
  exists only where a concrete contract requires it.
- Before adding a helper, inspect every use, across modules: a function with
  one use is a helper even when another package exports it. One use: inline it
  with a local comment. All uses in one function: nest it there. If a separate
  helper still looks better, explain why to the user and add it only if they
  agree.
- Composition over inheritance.

## Invariants and documentation

- Read the relevant document under `spec/` before changing that subsystem.
  Specs describe the code as it is and hold only what the code cannot show:
  invariants, ownership, lifetimes, ordering, and the reason for a choice. Code
  and specs change together; when they disagree, investigate the disagreement.
  - [`rooms.md`](spec/rooms.md): Room correspondence, change sources, Snapshot
    capture, equality and publication.
  - [`formats.md`](spec/formats.md): composition of Files into frames and bays,
    engines and enrichment.
  - [`comments.md`](spec/comments.md): comment Thread placement, excerpts,
    paging and the review HUD.
  - [`tabs.md`](spec/tabs.md): Tab selections and preferences.
  - [`file-lane.md`](spec/file-lane.md): loading of one mounted snapshot.
  - [`file-meat.md`](spec/file-meat.md): FileCard, bays, TextDiffGrid,
    rich/virtual rendering.
  - [`navigation.md`](spec/navigation.md): hunk selection, scroll-follow,
    FileTree and line pins.
  - [`release.md`](spec/release.md): installation modes, the wheel and the
    release server.
- A change to the captured Snapshot directory (layout, side names, byte
  meaning, path interpretation, the capture manifest) updates
  `.agents/skills/review-patch/references/snapshot_structure.md` and the
  `round-review` and `babysit-patch` skills in the same change; agents read
  that directory directly.
- `selectHunk()` has a fixed set of direct callers, enforced by lint. Initial
  hunk selection belongs to the mounted snapshot. FileTree name navigation
  selects hunks; line-pin navigation does not.
- A comment or docstring states a fact the code cannot: why, an invariant, a
  lifetime, an ordering constraint, or a quirk of an external tool. Everything
  else is said by names, types and the code itself, and gets no comment, unless
  written or requested by a human.
- CLI help text and HTTP endpoint descriptions are user-facing output, written
  for the person reading `--help` or the API schema.

## Module architecture

- Module boundaries stay as they are unless the user approves an architectural
  change. Suggest a module to the user when you think one is due.
- Code lives with its purpose, not with its mechanism. A package or directory
  holds the concern its spec names; sharing a library, a data shape or a
  similar-looking function is not a reason to put another concern there. When
  new code has no evident home, say so and ask before placing it.

### Frontend

- Domain-independent controls live in `frontend/src/comp/`; dirdiff UI lives in
  `frontend/src/hud/`; backend communication and TanStack definitions live in
  `frontend/src/api/`. `utils.ts` holds small, genuinely shared
  domain-independent operations.
- A new module needs a coherent implementation expected to exceed 500 lines; a
  component boundary is not a file boundary.

### Python packages

- Package `__init__.py` files are public facades containing re-exports only.
  The sole exception is a private `_x/__init__.py`, which is empty.
- Code outside a package imports its public items from the package root, never
  from its submodules.
- A top-level package's shared contracts, types, helpers, and invariants belong
  in its `base.py`. Modules anywhere inside that top-level package import those
  internals from its `base.py`, not from the package facade.
- A module `x.py` may split its private implementation across `_x/`. Only
  `x.py` imports modules beneath `_x`, and it imports them directly rather than
  through `_x/__init__.py`. The implementation modules remain independent
  leaves. No other module, including one inside `_x`, imports `_x` or anything
  beneath it.
- Top-level modules directly under `dirdiff` are peer packages represented by
  one file. Their module interface is their package interface, so other
  top-level packages import them directly.
- A Python module defines `__all__`, listing the items actually used outside
  that module. Empty private `_x/__init__.py` files are the sole exception.
- Database access goes through Python DSL operations: no ORM, no raw SQL.
  Database design satisfies Second Normal Form or better.

## Terminology

Use established domain language consistently:

- **folded** describes folded lines or rows;
- **collapsed** describes collapsed files or directories;
- **HUD** describes the application UI in its entirety;
- **Tab** describes the top-level review mode;
- **diff** describes a file-local difference;
- **frame** descibes a logical part of a file, like a notebook cell;
- **bay** describes the physical part, composed two-sided unit a frame contains.

Use the following words only for their defined meaning, avoid for anything else:

- **owner** means actual ownership of data or a resource, as in Rust ownership.
  Files, features, and projects do not “own” things merely because code is placed
  there.
- **commit** and **committed** mean VCS commits or database transaction commits.
- **signal** means a Solid signal. Preserve exact external API names such as
  `AbortSignal`, but do not turn the unqualified API term into project language.
- **request** as a noun means an HTTP entity. It may also be used as a verb and
  in established external names such as `requestAnimationFrame` and
  `pull_request`. Parameters to an HTTP request are parameters or options.
- **draft** means potentially persisted, unfinished material intended to be
  revised, accepted, or rejected. Input is input, it's not a draft.
- **comparison** means a logical comparison (=, <, >). The UI entity is a Tab; a
  file-local entity is a diff.
- **region** means a span of source content: a reattachment origin, a fold
  hint, or an engine's internal span. It never means the composed unit, which
  is a bay.
- **projection** is ambiguous and often signals an unclear design. Highlight it
  to the user when it is genuinely the best available term.

Names that commonly expose bad patterns require architectural scrutiny:

- **fallback** usually indicates a badly defined contract or implicit behavior.
  If a fallback appears necessary, ask whether the user accepts that behavior.
- **reconciliation**, **healing**, and **regulation** usually indicate implicit
  repair. Replace them with an explicit action against the authoritative state
  or DOM.
- **ensure\*** often hides nullable global state or creation. Prefer a constructor
  whose caller stores the result, or a blocking operation such as
  `waitToEnrich()` that names the actual behavior.
- **resolve\*** often hides a vague helper. Give the operation its domain name,
  inline it, or nest it where it is used.

## Frontend and visual work

- This is a desktop browser UI. Mobile and narrow layouts are out of scope
  unless requested.
- Visible behavior and styling stay as they are unless the user asks for a
  change. Architectural cleanup is not a redesign.
- Use the user's running hot-reload dirdiff/Vite session; start another server
  only when asked.
- A user-visible change is verified in the browser with screenshots and direct
  interaction. Type checks, DOM structure and computed measurements do not
  prove a visual change.
- Generated frontend bundles are built (`bun run --cwd frontend build`) and
  committed only on request.

## Tests and checks

- Pick checks that can verify the requested change, and run `make format`
  after code changes. Ordinary verification is `make tscheck` and
  `make eslint` for the frontend, `make mypy`, `make ruff` and `make pyflake`
  for the backend.
- Run Python tests with `make pytest`, the fast subset. A bare
  `uv run pytest` also runs the real-git integration tests; those and
  `make pytest-slow` take tens of minutes. `make fullcheck` runs everything
  and is for when the user asks.
- Test behavior and expectations change only with the user's explicit approval.
  A test broken by a change to an interface it uses (an import, a mock's shape,
  a signature) is updated to the new interface, with what it checks left intact.
- A permanent test is added only when its value exceeds its total cost:
  runtime, maintenance, brittleness and duplication. A missing test alone is
  not a reason to add one. A test that is added goes after edge cases and
  plausible bugs, not the happy path.
- A test shows its inputs, the call and the expectation in its own body.
  Shared test infrastructure lives in `tests/helpers.py`; adding to it needs
  the user's agreement.
- Monkey-patching and internal state manipulation are for investigation.
  Final confirmation exercises the real implementation and user-visible
  behavior, and permanent Python tests use no patching.
- Scratch checks go in `tests/e2e_temp`, which must remain.

## Review and completion

- Report completion only after reading the actual diff and verifying the
  requested outcome. Formatting, type checks and `git diff --check` are
  mechanical checks, not a review: look at the affected implementation, its
  callers, lifecycle, cancellation, errors and user-visible behavior in
  proportion to the change.
- As you work, keep what a reviewer will need: the user's messages verbatim,
  your test plan, what you actually ran, and screenshots of visible changes.

## Project commands and environment

- Use `uv` for Python commands and dependency changes, as
  `uv --no-cache run ...` to avoid cache-permission problems in sandboxed
  sessions. `.venv/bin/...` entry points suit repeated local commands that need
  no dependency resolution.
- Start a server, when requested, with `uv --no-cache run dirdiff --headless`.
  For isolated server state pass `--db-path` with a disposable SQLite file.
  Mark a repository with `uv --no-cache run dirdiff mark [--db-path ...]`.
- The user normally runs the editable installed tool through
  `uv tool install -e .`. Console entry points remain in `pyproject.toml`.
