# Pytest Hunk Navigation Wrapper Test

Source:

- [`tests/test_hunk_nav.py`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/test_hunk_nav.py)

## Why This Layer Exists

This file is intentionally small.

Its job is not to duplicate the JavaScript assertions.
Its job is to make the JavaScript hunk-navigation suite part of the normal Python test workflow, so `uv run pytest` still catches frontend navigation regressions.

## How This Test Works

- It discovers all `.cjs` files under `tests/js`.
- It skips cleanly if Node is unavailable.
- It runs `node --test` against those files from the repo root.
- It fails with combined stdout/stderr if any JS hunk-nav test fails.

## Covered Test

`test_hunk_nav_javascript_regressions`

- What it tests: all JavaScript hunk navigation unit tests pass.
- How it tests it: shells out to Node’s built-in test runner with every `.cjs` file in `tests/js`.
- Why it exists: keeps the fast JS controller coverage on the default `pytest` path instead of making it a separate opt-in check.
