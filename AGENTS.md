# AGENTS

This is a browser-based review tool. Its goal is to enable humans to review the
code with ease, and having a pleasant experience while doing so. No matter
the size of the patch, or the shape of the files.

When it comes to developing itself, every piece hot-reloads, be it the backend
or the frontend. Outdated state is highly unlikely, and is a likely a bug.

## Non-negotiable rules

### Do not make unauthorized changes

- Discussion, investigation, a proposed design, and approval of documentation
  are not permission to edit code.
- Permission applies once, to the exact action and scope the user approved. It
  does not silently extend to adjacent fixes, cleanup, tests, documentation, or
  later turns.
- Questions are read-only. "Why X", "what about Y", "you think it should work
  like that" and others *forbid* editing code.

### Do not hide broken contracts

- Do not add fallbacks, compatibility shims, silent recovery, invented defaults,
  or substitute data without explicit user approval.
  You can't violate that rule and then ask if it's ok. You must get an explicit
  approval before you do that.
- Never swallow errors. JavaScript may throw or show a Toast; Python may throw or
  log at an explicitly valid damage boundary.
- Damage stops at the smallest UI piece that cannot produce a valid result.
  Unexpected thrown failures are contained by the nearest `ErrorBoundary`.
- Required inputs are required. Do not make a parameter optional merely to avoid
  handling an invariant.
- Assert invalid data at the boundary where it becomes invalid. TypeScript uses
  `assert()` and `expect()` from `utils.ts`; Python uses `assert`.
- When an interface changes, update every in-scope caller and test. Do not create
  a compatibility path.

### Do not create accidental complexity

- Prefer one explicit action over effects, observers, implicit repair, duplicated
  state, or several paths to the same operation.
- Do not introduce reconciliation, healing, regulation, or similar machinery to
  compensate for an unclear architecture. Create an explicit action that changes
  the authoritative data or DOM directly.
- Do not copy backend query results into another client store. Keep one
  authoritative representation for each piece of data.
- Do not add optional inputs, providers, signals, caches, queues, callbacks, or
  helper layers without a concrete contract that requires them.
- Before adding a helper, inspect every use. If it has one use, inline it with a
  local comment. If all uses belong to one function, nest it in that function.
  If a separate helper still appears beneficial, explain that to the user before
  adding it, and add it only if user agrees.
- Strongly prefer composition over inheritance.

### Preserve explicit invariants

- Read `spec/frontend_index.md` or `spec/backend_index.md` and the relevant
  subsystem document before changing that subsystem.
- Do not add another `selectHunk()` caller. A deterministic lint enforces its
  five direct callers.
- Initial hunk selection belongs to the mounted snapshot. FileTree name
  navigation selects hunks; line-pin navigation does not.
- Preserve other documented ownership, lifetime, ordering, and DOM invariants.
  If the code and documentation disagree, investigate the disagreement instead
  of silently forcing either side to match.

### Document the actual design

- Code must expose its contract, ownership, lifecycle, and intent as docstrings
  and comments. Do not leave them only in the author’s head.
- Docstrings describe real interfaces and enduring behavior. They do not excuse
  confusing implementation, narrate migration history, or claim incomplete work
  is acceptable.
- Non-obvious mutable state, effects, observers, listeners, and cancellation
  require local comments explaining their purpose, inputs, lifetime, and
  disposal.

## Authority and contradictions

- `spec/goal.md` defines the role of the architecture documents. They are living
  descriptions of the implementation, not frozen requirements for a future
  implementation.
- Code and documentation evolve together. Neither silently overrides stale text
  in the other.
- If user instructions, documentation, implementation, tests, or tooling
  contradict one another, stop before choosing a resolution.
  If user instructions have gaps, ask, don't assume.
- Report the conflicting statements, their concrete consequence, and the
  reasonable choices. Wait for the user when the resolution changes
  behavior, architecture, scope, or an explicit invariant.
- Do not use an unusual workaround to make contradictory requirements appear
  compatible.

## Module architecture

### Frontend

- `spec/frontend_index.md` maps the current frontend modules and their
  interfaces.
- Existing documented module boundaries remain stable unless the user approves
  an architectural change.
- Do not create a new module unless its coherent implementation is reasonably
  expected to exceed 500 lines. A component boundary is not automatically a
  file boundary. If you think something should have a module, suggest it to the
  user.
- Domain-independent controls live in `frontend/src/comp/`; dirdiff UI lives in
  `frontend/src/hud/`; backend communication and TanStack definitions live in
  `frontend/src/api/`.
- `utils.ts` is the explicit exception for small, genuinely shared
  domain-independent operations.

### Python packages

- Package `__init__.py` files are public facades containing re-exports only,
  unless the user explicitly authorizes an exception.
- Code outside a package imports its public items from the package root, not its
  submodules. Never from its submodules.
- Package-internal shared contracts, types, helpers, and invariants belong in
  `base.py`. Sibling modules import those internals from `base.py`, not from the
  package facade.
- A Python module must define `__all__`. Items can be listed there when they are
  actually used outside that module.
- Do not use ORM, but dont throw raw SQL around, use Python DSL operations.
- Database design must satisfy Second Normal Form or better.

## Documentation and code structure

- Every added module requires a thorough module-level docstring or equivalent
  comment explaining:
  - its public interface;
  - why the module exists;
  - what data or resources its structures own;
  - what guarantees it provides;
  - what it must not own or do.
- If that module contract is difficult to state, state it to the user, it
  probably belongs in an existing module.
- Every declared function requires a real docstring explaining its purpose and
  caller contract. Public functions document required inputs, observable
  results, and caller obligations rather than internal steps.
- Every declared type alias, interface, class, and enum requires a docstring
  explaining its contract, fields or variants, valid inputs, guarantees, and
  what it must not represent.
- A title or restatement of the name is not a docstring. It's a violation
  of this document, and a potential hint that an item shouldn't exist.
- Use plain inline comments for local mechanics, sequencing, and surprising
  implementation facts. Keep those comments beside the relevant statements.

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
- **comparison** means a logical comparison. The UI entity is a Tab; a
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

- Treat the application as a browser UI first.
- Preserve current visible behavior and styling unless the user explicitly asks
  for a change. Architectural cleanup does not authorize visual redesign.
- This is a desktop application. Do not spend project scope on mobile or narrow
  responsive layouts unless requested.
- Use the user’s existing hot-reloadable dirdiff/Vite session. Do not start an
  alternative server, unless asked.
- For user-visible frontend or rendering changes, verify the actual behavior in
  the browser at a supported desktop viewport using screenshots.
- Use screenshots and direct interaction for visual verification. DOM structure,
  type checks, or computed measurements alone do not prove a visual change.
- Do not rebuild or commit generated frontend bundles during ordinary UI work.
  Run `bun run --cwd frontend build` only when explicitly requested.

## Testing and checks

- Choose checks that can verify the requested change. Do not run unrelated
  suites as a substitute for examining behavior.
- Run `make format` after code changes for which the formatter applies.
- Use `make tscheck` & `make eslint` for ordinary frontend TypeScript
  verification.
- Use `make mypy` & `make ruff` & `make pyflake` for ordinary backend Python
  verification.
- A CSS or interaction change requires browser verification and screenshots;
  TypeScript checks do not verify it.
- Never change test behavior or expectations without explicit user approval.
- Never create helpers in tests.
- Tests should be fast and adversarial. They should exercise edge cases and
  reproduce plausible bugs rather than merely execute the happy path.
- Monkey-patching or internal state manipulation is acceptable for finding an
  edge case. Final confirmation must exercise the real implementation and
  user-visible behavior.
- `uv --no-cache run pytest` runs the Python suite, including real-git
  integration tests under `tests/integration`.
  Dont run it, use `make pytest`, which runs relatively fast tests.
  There is `make pytest-slow` and `make pytest-integration` which can take
  tens of minutes.
- If you need to do scratch checks against the project, there's `tests/e2e_temp`
  directory. Use it if it is reasonable.
- When user asks, there's `make fullcheck` command. But it takes a long time,
  since it includes everything.

## Review and completion

- Before repository work, read the prelude in `reviewer.md`. Independent
  reviewers read the whole file.
- Never report completion without reading the actual diff and verifying the
  requested outcome.
- Formatting, type checks, and `git diff --check` are mechanical checks, not a
  code review.
- Review affected implementation, adjacent callers, documentation, lifecycle,
  cancellation, errors, and user-visible behavior in proportion to the change.
- When the user requires a zero-objection review, the existing reviewer reviews
  every accepted fix and continues reviewing until that reviewer explicitly
  reports zero remaining objections. Start a fresh independent reviewer only
  after the existing reviewer has no objections. Give each fresh reviewer fresh
  context and the required input from `reviewer.md`, then repeat the same
  sequential process until the required independent review is complete.

## Project commands and environment

- Use `uv` for Python commands and dependency changes.
- Prefer `uv --no-cache run ...` to avoid cache-permission problems in sandboxed
  sessions.
- `.venv/bin/...` entry points are suitable for repeated local commands that do
  not need dependency resolution.
- When requested to start a server, use `uv --no-cache run dirdiff --headless`.
- When verification needs isolated server state, pass `--db-path` with a
  disposable SQLite file.
- The user normally runs the editable installed tool through
  `uv tool install -e .`.
- Console entry points remain in `pyproject.toml`.
- You can mark new repos with `uv --no-cache run dirdiff mark [--db-path ...]`
