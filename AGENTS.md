# AGENTS

- Treat this as a browser-based UI project first.
- Use `uv` for Python commands and dependency changes.
- Run the app with `uv run dirdiff ...`; prefer `uv run dirdiff --headless` for local verification.
- Keep in mind that user runs it as `dirdiff` via installed tool with `uv tool install -e .`
- Run `make format` afterwards
- For user-visible frontend/rendering changes, verify in the browser against a local app session.
- Do not add Playwright tests/docs unless explicitly asked.
- Keep package data inside the package directory; keep console entry points in `pyproject.toml`.

# Testing
- `uv run pytest` runs the default non-git suite: pure diff logic plus fake-service CLI/API contract tests.
- `uv run pytest -m git` runs the slower real-git integration suite in `tests/integration`; run it when touching git/ref handling, repo manifest generation, lazy git-backed file loading, or git-backed API behavior.
- `uv run pytest -m "git or not git"` runs every Python test despite the default marker filter.
