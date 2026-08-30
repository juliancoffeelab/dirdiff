# Test presets

This directory is the presets root. Each subdirectory is one catalog, and its
name is the catalog id the HUD carries in `preset_type`. A catalog holds a
`preset.toml` stating exactly the `name` the Preset Tab shows:

```toml
name = "Diff Presets"
```

That file is the whole registration. Adding a catalog is creating the directory
and writing that line; no code names the set.

Below a catalog, each group directory holds preset directories of paired files:

- `old.<ext>` is the left side.
- `new.<ext>` is the right side.
- Ordinary pairs use the same extension. A pair that changes between a
  symbolic link and a regular File may use different extensions because its
  mode selects link composition.

A symbolic-link preset may also contain plainly named target Files and link
hops. Only `old.*` and `new.*` are comparison sides; the other entries are the
repository-local objects those sides reach.

A preset may hold only one side. One with just `new.*` is an addition and one
with just `old.*` is a deletion, which is how the format catalog covers an added
and a removed image.

`diff/` mirrors the difftastic adapter cases. These fixtures are intended
to become the source files for golden row-output tests.

A `borked/` directory holds known-bug cases: inputs that reproduce a defect the
renderer should handle but does not yet. Their output is wrong on purpose, so
snapshot tests skip `borked/` and the case stands as a runnable backlog item. A
set has a `borked/` directory only when it has such a bug to pin; it is not
required.

Malformed or unrenderable-as-intended input is a different thing. A file we
cannot parse as its declared format but still render correctly another way — an
invalid `.ipynb` shown as a text diff, a broken symlink shown as its recorded
target — is expected behaviour, not a bug. Those cases go in an ordinary
category such as `invalid/` and are tested like any other case.
