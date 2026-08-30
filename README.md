# dirdiff

Local browser diff viewer for large, but most importantly pleasant code reviews.
I was tired of looking at Github/Gitlab UI, so here is my attempt to show how
much they suck.
While doing that, I realized that it's not such an easy problem to solve, but
I think that I have built something that works better.

*Caveats: if you want to use difftastic, you'll probably need a few patches.
Read the end of this document.*

## Features

- Handles large PRs: roughly 30k changed lines and 300 changed files is a
normal case, not a tragedy.
- Supports multiple diff engines: git, difftastic, gumtree, custom 
- Uses tree-sitter for syntax highlighting and semantic folds, to collapse
intelligently and not just hide random lines.
- Works pre-dominantly with git, but can be extended to support other backends.
- Works with Git and supports plain diff, custom refs and branch review.
- As a bonus, works with jupyter notebooks too.

## How to run in dev mode
```bash
make dev
# or, if you prefer explicit command
uv run dirdiff
```

This would build the project into editable form with `uv` and run it.
Under the hood this would run `uvicorn` and `vite` both with full hot-reloading.
If hot-reloading doesn't work, that's a bug.

First time, it may ask you to mark the repo using:
```bash
dirdiff mark
# or in dev mode
uv run dirdiff mark
```
By default it marks the main repo, but this also allows you to mark any
repository on your PC to review it from dev folder.

## Dev install
If you want a proper release build without hot-reloading, but with better
cold starts (which includes page reload via F5), you can do just do a standard
install.
```bash
make install
# or
uv tool install .
```

If you don't have the project cloned, you can install it from github as well:
```bash
uv tool install git+https://github.com/juliancoffeelab/dirdiff
```

Or as one-off command:
```bash
uvx --from git+https://github.com/juliancoffeelab/dirdiff dirdiff
```

Then you can just run it normally:
```bash
dirdiff
```
And explore other CLI options using `--help`.

## Marks

We support marking feature, so you can mark any repo and have it globally
available:

```bash
dirdiff mark
```

The database is in `$HOME/.local/share/dirdiff/release`, we don't do support
platform specific placements like xdg-directories (yet?).

(For editable installs, it's `$HOME/.local/share/dirdiff`).


## Third-party fixes

Additional fixes that have not yet landed upstream may be available in
[juliancoffeelab/difftastic](https://github.com/juliancoffeelab/difftastic).
I've found a crash during testing, and there you can find a workaround.

## Known issues
- Difftastic is a hog on files it doesn't like.
