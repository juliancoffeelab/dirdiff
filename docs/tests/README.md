# Test Docs

This folder explains the automated test layers in `dirdiff`.

Each subfolder documents:

- what the tests cover
- how the tests exercise that behavior
- why that coverage exists

Current layout:

- [Playwright hunk navigation](./playwright/hunk-nav.md)
- [JavaScript hunk navigation](./js/hunk-nav.md)
- [Pytest diff logic](./pytest/diff-logic.md)
- [Pytest hunk navigation wrapper](./pytest/hunk-nav.md)

The browser suite is the source of truth for real DOM, scrolling, viewport, and timing behavior.
The JavaScript suite is the cheap logic guardrail for the hunk navigation controller.
The pytest layer covers Python-side diff behavior and also makes the JS hunk-nav tests part of the normal `uv run pytest` workflow.
