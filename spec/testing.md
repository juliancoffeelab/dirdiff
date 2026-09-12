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
JSON through `tool_json()` in `tests/helpers.py`. Pass the engine, a stable
cache key, and two `ToolSide` inputs. A `Path` supplies exact UTF-8 text and
its basename; `ToolText(text, name)` supplies inline text and a required
filename hint. Line endings and whitespace are part of the input.

Local results live under `.test_cache/tool/<engine>/<key>.json`. Each entry
contains `left_hash`, `right_hash`, and `output`. Side hashes cover text and
filename hints, never absolute fixture paths. Missing entries run the real
executable and use atomic replacement to publish JSON. Concurrent misses may
run the tool independently. Hits validate the cached output without inspecting
the installed executable. Filesystem errors, malformed entries and tool
failures propagate.

Keys contain slash-separated lowercase kebab-case segments, with digits
allowed. Preset checks share `presets/<group>/<case>` across golden, focused,
and property tests. Inline cases use `focused/<case>`. Normalized replay inputs
use a separate `/normalized` key because they remove ignored trailing commas.

A hash mismatch is a code error. Investigate conflicting key reuse or changed
test inputs and fix the calling code. Deleting the entry alone does not fix
that error. Only changing worktree cases pass `allow_mismatches=True`, which
reruns the tool and replaces stale input pairs. Even then, malformed entries
fail instead of being replaced. Worktree keys use the display-name digest so
filename punctuation cannot produce invalid or colliding slugs. Deliberate
invalidation after updating the tools remains `rm -rf .test_cache/tool`.

Preset and worktree collectors use separate required-field `TypedDict` cases.
Both carry `cache_key` and `allow_mismatches`; presets supply source paths,
while worktree cases supply captured text, parser names and a display name.

Tests of complete rendering or composition use a scoped patch at the engine's
JSON subprocess boundary, supplying real cached output for that exact source
pair. Row construction, summaries and display enrichment still run normally.
The patch ends before the test returns; it never installs a global cache in
production rendering.

Use existing presets unless exact whitespace or surrounding context is the
subject of the check. Handwritten external JSON must stay below ten lines.
Larger inputs require a deliberate synthetic case that the executable would
not produce, with a nearby `# tool-json: synthetic <reason>` comment.

`make flake-tjc`, also included in `make pyflake`, checks key spelling,
conflicting literal `ToolText` inputs across test modules, direct uncached JSON
calls, and large unexplained Difftastic literals. It does not execute source
or infer dynamic inputs. Runtime hashes check cases the lint cannot establish.

# Other kinds of tests

CLI is tested with Cram presets. The goal of these is to be proper doctests, so
making them human-readable is the key.

Temporary e2e tests are throw-away TDD-like tests. Ideally, they should use a
proper JS-based Playwright setup, are intentionally not included in any Makefile
rules, and are run when the need arises.

We don't have tests for the frontend yet.

# Appendix A: how to create a preset

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
behavior. Git adds `git-moved` and `git-moved-indent`. There are no Makefile
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
