# AGENTS

- Treat this as a browser-based UI project first.
- Use `uv` for Python commands and dependency changes.
- Please don't start the alternative server, it's hot-reloadable, just use what user uses.
- Prefer `uv --no-cache run ...` when invoking project commands through uv; this avoids uv cache permission issues in sandboxed agent sessions.
- `.venv/bin/...` entry points are also fine for repeated local commands that do not need dependency resolution.
- Run the app with `uv --no-cache run dirdiff ...`; prefer `uv --no-cache run dirdiff --headless` for local verification so Vite serves the frontend.
- When temporary local verification needs isolated server state, use `--db-path` with a disposable SQLite file.
- Keep in mind that user runs it as `dirdiff` via installed tool with `uv tool install -e .`

# Important rules
- Assert data inputs. Dont create optional parameters. If you need some field and it's null, throw the error.
TS has assert() and expect() in utils.ts.
Python has assert.
- Never edit test behavior, if expection changes, ask user and only after confirmation update the behaviour of the test.
- Never create compatibility shims. Interface must be updated on all sides, that includes tests.
- Never create helpers in tests.
- Please dont use ORM mess for database operations.
- Second Normal Form (or better) is mandatory.

# Module Architecture
- Package `__init__.py` files are public facades and must contain re-exports
only.
- Code outside a package must import that package's public items from the
package root, not from its submodules.
- Package-internal shared contracts, types, helpers, and invariants belong in
`base.py`. Sibling modules must import those shared internals from `base.py`,
not from the package `__init__.py`.
- Do not create new modules unless you envision them to be more than 1000 lines
long.

# Documentation & Structure
- Before adding a function shorter than five lines of code, inspect every use.
If it has one use, prefer inlining it at that use with a local inline comment
explaining the operation. If it has multiple uses that all belong to one owning
function, make it a nested function of that owner. Keep a separate short function
only when it represents a genuine reusable interface or named domain operation.
- Every added file must have throughough module-level docstring.
What is the interface of this file, why it exists, what this file should do and
what it should not do.
If this docstring is hard to write, it means that such file should not exist
and the code should just live elsewhere.
- Same about functions. Either docstings with reasoning, or no function at all.
- Every declared type, type alias, interface, class, and enum must have a
thorough docstring explaining the contract it represents, the meaning and
requirements of its fields or variants, and what it must not be used to
represent. Public types must document what callers may provide and rely on.
- Public function docstrings should explain what callers can expect and need to
comply with, not the internals.
- Modules, classes should explain both. Private functions if reasonable, but
public details are still non-negotiable.
- Docstrings should explain public API, not the internals.
- If docstring has title only, it's not a docstring, it must comply with
the rules above.
- Every Python module must define `__all__` with its exported items, only
if these items are exported **and used**.

# Linting & Checks
- Run `make format` afterwards
- For user-visible frontend/rendering changes, verify in the browser against a local app session.
- For ordinary frontend TypeScript verification, use `make tscheck`.
- Do not rebuild or commit generated frontend bundles for ordinary UI iteration; the app uses Vite by default. Only run `bun run --cwd frontend build` when explicitly requested by the user.
- Keep console entry points in `pyproject.toml`.

# Testing
- `uv --no-cache run pytest` runs the Python test suite, including real-git integration tests in `tests/integration`.
