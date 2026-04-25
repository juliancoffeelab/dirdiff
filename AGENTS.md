# AGENTS

## Python Tooling

- Use `uv` for dependency management, locking, syncing, and running commands.
- The build backend is `hatchling`; do not switch back to `setuptools` unless there is a concrete packaging need.

## Running Things

- Run the app with `uv run dirdiff ...`.
- `uv run dirdiff` with no path should open a whole-repo Git diff when inside a repo.
- Run tests with `uv run pytest`.

## Testing Rules

- Prefer `pytest` for new tests.
- Use `tmp_path` and other pytest fixtures instead of `tempfile` + `unittest` boilerplate when possible.
- Do not rely on `PYTHONPATH=src` hacks. Commands should run through `uv run`.
- Sensible default checks for code changes:
  `uv run pytest`
  `uv build`
- If you change browser rendering, diff row DOM, syntax highlighting, or folding behavior, also run a Playwright smoke test against a local `uv run dirdiff` session.

## Dependency Changes

- Add project or test dependencies with `uv add` so `pyproject.toml` and `uv.lock` stay in sync.
- For test-only tools, prefer the `dev` dependency group.

## Packaging Notes

- Keep console entry points in `pyproject.toml`.
- Keep package data inside the package directory so Hatch can include it in the wheel.
