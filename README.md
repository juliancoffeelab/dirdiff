# dirdiff

Local browser diff viewer for large, but most importantly pleasant code reviews.
I was tired of looking at Github/Gitlab UI, so here is my attempt to show how
much they suck.
While doing that, I realized that it's not such an easy problem to solve, but
I think that I have built something that works better.

*Caveats: if you want to use difftastic, you'll probably need a few patches.
Read the end of this document.*

AI disclaimer: this was built over three months with different LLM agents,
and I have wrote maybe 50 lines or so, but the code was sort of reviewed; after
all it would be ironic to build the project otherwise.
This README.md is written by me, though.

## Features

- Handles large PRs: won't die on 30k changed lines or 300 changed files. Not that you would need that ... until you need that.
- Supports multiple diff engines: git, difftastic, gumtree, and two custom (vibed) algorithms 
- Uses tree-sitter for syntax highlighting and semantic folds; to collapse
intelligently and not just hide random lines.
- Works predominantly with git, but can be extended to support other backends.
- Works with Git and supports plain diff, custom refs and branch review.
- As a bonus, works with Jupyter notebooks too.

## How to run in dev mode
```bash
make dev
# or, if you prefer an explicit command
uv run dirdiff
```

This would build the project into editable form with `uv` and run it.
Under the hood, this would run `uvicorn` and `vite` both with full hot-reloading.
If hot-reloading doesn't work, that's a bug.

The first time, it may ask you to mark the repo using:
```bash
dirdiff mark
# or in dev mode
uv run dirdiff mark
```
By default, it marks the main repo, but this also allows you to mark any
repository on your PC to review it from the dev folder.

## Install
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

We support a marking feature, so you can mark any repo and have it globally
available:

```bash
dirdiff mark
```

The database is in `$HOME/.local/share/dirdiff/release`, we don't do support
platform specific placements like xdg-directories (yet?).

(For editable installs, it's `$HOME/.local/share/dirdiff`).

# More screenshots

<img width="1432" height="690" alt="зображення" src="https://github.com/user-attachments/assets/fb5b93e4-f2bc-473a-9c50-3c236a2fd615" />
<img width="1435" height="696" alt="зображення" src="https://github.com/user-attachments/assets/0d972379-9bb2-429b-a4c7-d5956ade8023" />
<img width="1434" height="652" alt="зображення" src="https://github.com/user-attachments/assets/eb3119b2-d88d-4fc6-adb9-c166a395276b" />
<img width="1436" height="685" alt="зображення" src="https://github.com/user-attachments/assets/4550825e-3b9f-4a13-b83c-1e802635210a" />

## Third-party fixes

Additional fixes that have not yet landed upstream may be available in
[juliancoffeelab/difftastic](https://github.com/juliancoffeelab/difftastic).
I've found a crash during testing, and there you can find a few patches for that and some other stuff.

## Known issues
- Difftastic is a hog on files it doesn't like.
