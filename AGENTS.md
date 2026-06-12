# AGENTS

- Treat this as a browser-based UI project first.
- Use `uv` for Python commands and dependency changes.
- Run the app with `uv run dirdiff ...`; prefer `uv run dirdiff --headless` for local verification.
- For user-visible frontend/rendering changes, verify in the browser against a local app session.
- Do not add Playwright tests/docs unless explicitly asked.
- Keep package data inside the package directory; keep console entry points in `pyproject.toml`.
