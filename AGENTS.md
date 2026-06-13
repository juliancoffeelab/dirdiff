# AGENTS

- Treat this as a browser-based UI project first.
- Use `uv` for Python commands and dependency changes.
- Prefer `uv --no-cache run ...` when invoking project commands through uv; this avoids uv cache permission issues in sandboxed agent sessions.
- `.venv/bin/...` entry points are also fine for repeated local commands that do not need dependency resolution.
- Run the app with `uv --no-cache run dirdiff ...`; prefer `uv --no-cache run dirdiff --headless` for local verification so Vite serves the frontend.
- Keep in mind that user runs it as `dirdiff` via installed tool with `uv tool install -e .`
- Run `make format` afterwards
- For user-visible frontend/rendering changes, verify in the browser against a local app session.
- Do not rebuild or commit generated frontend bundles for ordinary UI iteration; the app uses Vite by default. Only run `npm --prefix frontend run build` when explicitly checking production/package behavior.
- Do not add Playwright tests/docs unless explicitly asked.
- Keep console entry points in `pyproject.toml`.

# Testing
- `uv --no-cache run pytest` runs the default non-git suite: pure diff logic plus fake-service CLI/API contract tests.
- `uv --no-cache run pytest -m git` runs the slower real-git integration suite in `tests/integration`; run it when touching git/ref handling, repo manifest generation, lazy git-backed file loading, or git-backed API behavior.
- `uv --no-cache run pytest -m "git or not git"` runs every Python test despite the default marker filter.
