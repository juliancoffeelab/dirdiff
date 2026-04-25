# file-diff-viewer

Standalone browser-based diff viewer for generic text files.

It keeps the strongest part of the notebook diff tool from `DRAISS`:
a line-oriented, side-by-side view with inline token highlighting and hunk
navigation.

What changed for portability:

- It works on plain text files, not notebook cells.
- It can compare a repo-relative path across Git sides like `head`, `index`,
  `worktree`, or a custom ref.
- It can also compare any two filesystem paths directly, even outside Git.
- It renders the whole file; there are no folds.

## Install

```bash
cd ~/Workspace/lab/file-diff-viewer
uv tool install -e .
```

## Run

Git-backed diff for a repo file:

```bash
file-diff-viewer --path src/app.py --left head --right worktree
```

Direct file-to-file diff:

```bash
file-diff-viewer --left-file old.txt --right-file new.txt
```

You can also run it without installing:

```bash
uv run file-diff-viewer --path src/app.py
```
