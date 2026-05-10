# dirdiff

Standalone browser-based diff viewer for real-world files.

It keeps the strongest part of the notebook diff tool from `DRAISS`:
a line-oriented, side-by-side view with inline token highlighting and hunk
navigation.

What it supports:

- It renders `.ipynb` files as notebook-aware diffs with cell source,
  metadata, and outputs.
- It renders supported text formats with syntax-aware line diffs and structural
  folding.
- It falls back to whole-file text diffs for anything it does not understand
  structurally.
- It can compare a repo-relative path across Git sides like `head`, `index`,
  `worktree`, or a custom ref.
- It can also compare any two filesystem paths directly, even outside Git.
- It auto-collapses unchanged structural regions like functions, methods, and
  multiline containers using tree-sitter fold hints.
- Markdown diffs fold only unchanged section bodies under headings; the heading
  line stays visible and non-heading Markdown stays expanded.

## Install

```bash
cd ~/Workspace/lab/dirdiff
uv sync
```

## Run

Whole-repo diff for the current Git repo:

```bash
uv run dirdiff
```

Branch review against `master` or your detected default branch:

```bash
uv run dirdiff --branch feature/my-change --base-branch master
```

Headless local server for tests or Playwright checks:

```bash
uv run dirdiff --headless
```

If you want a globally available CLI:

```bash
uv tool install -e .
```

## Development Workflow

This project uses:

- `uv` for project environments, locking, and running commands
- `hatchling` as the build backend

## Test

```bash
uv run pytest
```
