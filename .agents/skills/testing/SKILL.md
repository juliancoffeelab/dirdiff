---
name: testing
description: Use when adding, changing or running dirdiff tests, presets, golden snapshots, or cached third-party engine JSON.
---

# What this document is about

The document must answer how to add new tests, where to add new tests, and what
different kinds of tests test, etc.

It intentionally doesn't list all the tests, since the implementation is
irrelevant.

# Kinds of tests

Tests are split largely into three categories at the moment: snapshot tests,
property tests, and unit tests.

Snapshot tests, also known as golden tests or approval tests, are here to freeze
the current behavior for things we care about or, in rare cases, observe
behavior changes and judge whether they are intentional or not. Usually not,
though.

These use `tests/presets/` directories and are governed by
`test_preset_hygiene.py`, outputting their JSON into `tests/golden/`.

See [Appendix A](#appendix-a-how-to-create-a-preset) for how to create a preset
and update its golden snapshot.

The naming scheme is `test_*_golden.py`.

Property tests are high-level tests that observe that our algorithm preserves
important invariants. For example, for Difftastic, it checks that you can replay
the diff to get from an old file to a new file.

Usually, they operate on presets from the category above, since when they fail,
it's useful to see where exactly they fail, not just random data.

In rare cases, these can use Hypothesis-based QuickCheck-style generated data,
but human-readable presets are highly preferred.

The naming scheme is `test_*_proptest.py`.

Logic tests, the lowest caste of tests, operate on the basis of TDD and are
intended to exploit a local edge case to fight against when fixing the bug.

The naming scheme is `test_*_logic.py`.

# Third-party engine JSON

Executable-backed Difftastic, GumTree, and Diffsitter engine tests obtain
JSON through `tool_json()` in `tests/helpers.py`, which runs the real
executable once and caches its output under `.test_cache/tool/`.

Keys contain slash-separated lowercase kebab-case segments, with digits
allowed. Preset checks share `presets/<group>/<case>` across golden, focused,
and property tests. Inline cases use `focused/<case>`. Normalized replay inputs
use a separate `/normalized` key because they remove ignored trailing commas.

A hash mismatch is a code error. Investigate conflicting key reuse or changed
test inputs and fix the calling code. Deleting the entry alone does not fix
that error. Only changing worktree cases pass `allow_mismatches=True`.
Deliberate invalidation after updating the tools remains
`rm -rf .test_cache/tool`.

Use existing presets unless exact whitespace or surrounding context is the
subject of the check. Handwritten external JSON must stay below ten lines.
Larger inputs require a deliberate synthetic case that the executable would
not produce, with a nearby `# tool-json: synthetic <reason>` comment.
`make flake-tjc`, included in `make pyflake`, checks these rules.

# Other kinds of tests

Python docstring examples are tested with `make doctest`. It collects doctests
from `src/dirdiff`, so examples belong beside the interface they demonstrate.
`make pytest` runs these before the fast tests; `make fullcheck` includes them
through that command. Explicit slow and integration commands keep their own
test scope.

CLI is tested with Cram presets. The goal of these is to be proper doctests, so
making them human-readable is the key.

Temporary e2e tests are throw-away TDD-like tests. Ideally, they should use a
proper JS-based Playwright setup, are intentionally not included in any Makefile
rules, and are run when the need arises.

Frontend tests live in `frontend/tests` and run with `make tstest`.

# Appendix A: how to create a preset

Presets must be understandable from their source pairs and runnable Makefiles.
`README.md` is forbidden anywhere under `tests/presets/`, regardless of filename
casing. Separate guides add reading overhead and duplicate information that can
drift from the actual inputs and commands. Agents must not circumvent this rule
in any way: do not rename or relocate the guide, disguise it as fixture content,
use symlinks, add exceptions, or weaken the hygiene check. Remove the guide.

Create the preset under:

```text
tests/presets/<catalog>/<group>/<case>/
```

The catalog directory is a directory under the presets root holding a
`preset.toml` that states the `name` the Preset Tab shows; its own directory
name is the catalog id. Creating a catalog is creating that directory and that
one line. A catalog with additional inspection commands also has a
`preset.Makefile`. `/api/presets` lists the catalogs from their metadata.

The case directory must contain:

- exactly one `old.*` file;
- exactly one `new.*` file with the same extension;
- a standalone `Makefile` containing the hardcoded defaults from
  `test_preset_hygiene.py`, followed by the catalog's `preset.Makefile` contents.

The shared commands are `difft`, `git`, `gum`, `difft-json`, `bat` for both
sides, and `diffs` for Diffsitter. Catalogs add commands without replacing shared
behavior. Git adds `git-word`, `git-word-lang`, `git-moved`, and `git-moved-indent`. There are no Makefile
includes: a fixture contains its complete recipes, and catalog additions must
be copied to its fixtures when they change. Recipe commands must not use Make's
`@` prefix; the invoked command remains visible in terminal output.

Hygiene checks require `preset.toml`, verify each fixture's complete Makefile,
and dry-run the shared commands with its actual source filenames.
Their expansion must match the defaults, which detects catalog overrides that
change shared behavior. Make's rule database supplies additional targets for
dry-runs; Git's move targets also have their detection flags checked. These
checks inspect command construction without launching the external tools.

A case describing an addition or a deletion holds only its one side and has no
`Makefile`, since there is no pair to run. Golden snapshot tests cover the
two-sided cases.

Golden snapshots mirror the group and case path under the golden root the
snapshot extension names, such as `tests/golden/difftastic/<group>/<case>/`.

Run `make snapshot` to verify the current snapshots. Run `make resnapshot` to
delete and regenerate all golden JSON after an intentional behavior change or
after adding a preset. Inspect the resulting JSON changes, then run
`make snapshot` again to verify them.
