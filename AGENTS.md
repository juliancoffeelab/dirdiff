# AGENTS

## Context Handling

- Treat user questions as being about this `dirdiff` project by default.
- For ambiguous tooling questions, answer in project context first: dependencies, libraries, architecture, implementation choices, tests, or local workflow.
- Do not redirect an ambiguous question into editor, plugin, IDE, or personal-environment advice unless the user explicitly asks for that context.
- If a question is short but appears conversational, still anchor the answer to the most likely project-relevant interpretation before widening scope.

## Python Tooling

- Use `uv` for dependency management, locking, syncing, and running commands.
- The build backend is `hatchling`; do not switch back to `setuptools` unless there is a concrete packaging need.

## Running Things

- Run the app with `uv run dirdiff ...`.
- For local verification or browser automation, prefer `uv run dirdiff --headless`.
- `uv run dirdiff` with no path should open a whole-repo Git diff when inside a repo.
- Run tests with `uv run pytest`.

## Testing Rules

- Prefer `pytest` for new tests.
- Use `tmp_path` and other pytest fixtures instead of `tempfile` + `unittest` boilerplate when possible.
- Do not rely on `PYTHONPATH=src` hacks. Commands should run through `uv run`.
- Sensible default checks for code changes:
  `uv run pytest`
  `uv build`
- Do not fix a bug until you have added an automated regression test that fails on the current buggy behavior. The test should demonstrate the bug first, then the code change should make it pass.
- This rule applies to actual bug fixes and regressions. It does not automatically apply to behavior changes, default changes, product decisions, refactors, cleanups, or performance work unless they are explicitly addressing a concrete buggy behavior.
- Do not relabel a behavior change or product decision as a "bug". If the user is changing intended behavior rather than fixing a defect, treat it as a behavior change and choose test coverage accordingly.
- If you change browser rendering, diff row DOM, syntax highlighting, or folding behavior, also run a Playwright smoke test against a local `uv run dirdiff` session.
- For browser-only regressions, add an automated repro first, then implement the fix against that repro. Prefer Playwright when the bug depends on real scrolling, layout, or DOM timing.
- Only document significant Playwright coverage in `docs/tests/playwright/`.
- Do not create or maintain doc pages for unit tests or small helper-level tests.
- When Playwright docs are needed, write actual documentation: describe the behavior under test, the shape of the scenario, and why that coverage matters. Do not add formal one-line stubs just to satisfy a process rule.

## Dependency Changes

- Add project or test dependencies with `uv add` so `pyproject.toml` and `uv.lock` stay in sync.
- For test-only tools, prefer the `dev` dependency group.

## Packaging Notes

- Keep console entry points in `pyproject.toml`.
- Keep package data inside the package directory so Hatch can include it in the wheel.
