# Test presets

Preset directories contain paired files for regression tests:

- `old.<ext>` is the left side.
- `new.<ext>` is the right side.
- Both files in a preset use the same extension.

`difftastic/` mirrors the difftastic adapter cases. These fixtures are intended
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
