# dirdiff

Local browser diff viewer for large, but most importantly pleasant code reviews.
I was tired of looking at Github/Gitlab UI, so here is my attempt to show how
much they suck.
While doing that, I realized that it's not such an easy problem to solve, but
I think that I have built something that works better.

## Features

- Handles large PRs: roughly 30k changed lines and 300 changed files is a
normal case, not a tragedy.
- Supports multiple diff engines: git, difftastic, gumtree, custom 
- Uses tree-sitter for syntax highlighting and semantic folds, to collapse
intelligently and not just hide random lines.
- Works pre-dominantly with git, but can be extended to support other backends.
- Works with Git and supports plain diff, custom refs and branch review.
- As a bonus, works with jupyter notebooks too.

## Dev Install

```bash
bun install --cwd frontend
uv tool install -e .
```

Then run it from a Git repo:

```bash
dirdiff
```

(Also has some CLI options, feel free to explore them.)

We use uvicorn and vite with every dev option enabled, so hot-reloading fully
works. If it doesn't that's a bug.
It means that cold-starts might be a bit slower, but that's the price we pay.

## Marks

We also support marking feature, so you can mark any repo and have it globally
available:

```bash
dirdiff mark
```

The database is in `$HOME/.local/share/dirdiff`, we don't do support platform
specific placements like xdg-directories (yet?).


## Third-party fixes

Additional fixes that have not yet landed upstream may be available in
[juliancoffeelab/difftastic](https://github.com/juliancoffeelab/difftastic).
I've found a crash during testing, and there you can find a workaround.

## Known issues
- Difftastic is a hog on files it doesn't like.
