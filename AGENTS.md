# AGENTS

## Context Handling

- Treat user questions as being about this `dirdiff` project by default.
- For ambiguous tooling questions, answer in project context first: dependencies, libraries, architecture, implementation choices, tests, or local workflow.
- Do not redirect an ambiguous question into editor, plugin, IDE, or personal-environment advice unless the user explicitly asks for that context.
- If a question is short but appears conversational, still anchor the answer to the most likely project-relevant interpretation before widening scope.

## Python Tooling

- Use `uv` for dependency management, locking, syncing, and running commands.
- The build backend is `hatchling`; do not switch back to `setuptools` unless there is a concrete packaging need.

## Product Shape

- Treat `dirdiff` as a browser-based UI first. The Python entry point exists to launch the local web app, not to define a full user-facing CLI product.
- Do not steer feature work toward standalone CLI UX unless the user explicitly asks for it. In particular, do not invent or prioritize CLI-first workflows, flags, completions, or terminal-only interaction patterns as if they are the main product surface.
- When a request is ambiguous between web UI behavior and CLI behavior, prefer the web UI interpretation.

## Running Things

- Run the app with `uv run dirdiff ...`.
- For local verification or browser automation, prefer `uv run dirdiff --headless`.
- `uv run dirdiff` with no path should open a whole-repo Git diff when inside a repo.
- Run tests with `uv run pytest`.
- Manual verification is required for user-visible changes. Prefer checking the actual browser UI against a local `uv run dirdiff` session.

## Testing Rules

- Prefer `pytest` for new tests.
- Use `tmp_path` and other pytest fixtures instead of `tempfile` + `unittest` boilerplate when possible.
- Do not rely on `PYTHONPATH=src` hacks. Commands should run through `uv run`.
- Sensible default checks for code changes:
  `uv run pytest`
  `uv build`
- Do not relabel a behavior change or product decision as a "bug". If the user is changing intended behavior rather than fixing a defect, treat it as a behavior change and choose test coverage accordingly.
- Manual verification is required before calling user-visible work done.
- If you change browser rendering, diff row DOM, syntax highlighting, folding behavior, defaults, or other UX flows, verify them in the browser against a local `uv run dirdiff` session.
- Do not add or require Playwright tests unless the user explicitly asks for them.
- Do not create or maintain Playwright test docs unless the user explicitly asks for them.

## Dependency Changes

- Add project dependencies with `uv add` so `pyproject.toml` and `uv.lock` stay in sync.

## Packaging Notes

- Keep console entry points in `pyproject.toml`.
- Keep package data inside the package directory so Hatch can include it in the wheel.
