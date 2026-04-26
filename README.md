# dirdiff

Standalone browser-based diff viewer for generic text files.

It keeps the strongest part of the notebook diff tool from `DRAISS`:
a line-oriented, side-by-side view with inline token highlighting and hunk
navigation.

What changed for portability:

- It works on plain text files, not notebook cells.
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

Git-backed diff for a repo file:

```bash
uv run dirdiff --path src/dirdiff/cli.py --left head --right worktree
```

Whole-repo diff for the current Git repo:

```bash
uv run dirdiff
```

Headless local server for tests or Playwright checks:

```bash
uv run dirdiff --headless
```

Direct file-to-file diff:

```bash
uv run dirdiff --left-file old.txt --right-file new.txt
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
