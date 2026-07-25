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
tests/presets/<kind>/<group>/<case>/
```

The directory must contain:

- exactly one `old.*` file;
- exactly one `new.*` file with the same extension;
- the standard preset `Makefile`, copied from an adjacent preset.

Golden snapshots mirror the preset path under:

```text
tests/golden/<kind>/<group>/<case>/
```

Run `make snapshot` to verify the current snapshots. Run `make resnapshot` to
delete and regenerate all golden JSON after an intentional behavior change or
after adding a preset. Inspect the resulting JSON changes, then run
`make snapshot` again to verify them.
