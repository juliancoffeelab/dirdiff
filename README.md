# dirdiff

Local browser diff viewer for large, but most importantly pleasant code reviews.
I was tired of looking at Github/Gitlab UI, so here is my attempt to show how
much they suck.

## Features

- Handles large PRs: roughly 30k changed lines and 300 changed files.
- Supports three diff engines: simple custom algorith over sequence matching,
plain Git-style diffs, and difftastic (structural diffs based on tree-sitter).
- Uses tree-sitter for syntax highlighting and semantic folds, to collapse
intelligently and not just hide random lines.
- Works with Git and supports plain diff, custom refs and branch review.
- As a bonus, works with jupyter notebooks too.

## Dev Install

```bash
npm --prefix frontend install
uv tool install -e .
```

Then run it from a Git repo:

```bash
dirdiff
```

(Also has some CLI options, feel free to explore them.)

We use uvicorn and vite with every dev option enabled, so hot-reloading fully
works. If it doesn't that's a bug.
