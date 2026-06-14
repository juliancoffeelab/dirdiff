# Test presets

Preset directories contain paired files for regression tests:

- `old.<ext>` is the left side.
- `new.<ext>` is the right side.
- Both files in a preset use the same extension.

`difftastic/` mirrors the difftastic adapter cases. These fixtures are intended
to become the source files for golden row-output tests.
