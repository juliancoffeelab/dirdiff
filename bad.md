# Difftastic Converter Investigation Notes

This file was requested while debugging `_difftastic_rows_from_json`. The
important conclusion is that the failing golden snapshots should not be treated
as bad tests by default. They are useful evidence about difftastic's display
semantics.

## Removed workaround

The converter used to promote right-only residual context lines to inserted
rows after seeing matching deleted-tail atoms. The
`typescript-wrap-arg-change-should-not-reconstruct-tail` fixture showed why this
was wrong:

- difftastic renders `baz,` and `);` as unchanged right-side context;
- the converter marks those rows as inserted when it manufactures whole-line
  insert tokens from prior left-tail atoms.

This was a converter bug, not a bad test. The converter now keeps the right-side
line one-sided because `aligned_lines` says the left side is `null`, and it no
longer invents whole-line changed tokens when difftastic did not report changed
spans for that right line.

The `force_insert_tokens`, `force_empty_tokens`, and deleted-tail token
manufacturing paths were removed from `logic.py`.

## Synthetic JSON shapes converted to real difftastic JSON

Some unit tests used intentionally tiny `DifftasticJson` values instead of raw
CLI output captured from `difft`. They were useful as renderer contract probes,
but they did not always carry enough context to distinguish "right-only changed
content" from "right-only unchanged context".

Examples:

- the old `test_difftastic_rows_status_is_equal_for_right_only_line_without_changed_tokens`
  used `{"aligned_lines": [[0, 0], [None, 1]], "chunks": [[]]}`;
- the old `test_difftastic_rows_status_is_replace_for_mixed_unchanged_and_insert_tokens`
  supplied only a handcrafted `rhs` comma span for a right-only row;
- the old full-line insert/delete/replace status tests also supplied handcrafted
  `aligned_lines` and `chunks`.

These tests now call the real difftastic service on tiny source snippets via
`_text_rows(...)`, so converter behavior is exercised through real
`difft --display json` output.

## Punctuation context repair

After removing the synthetic-shape workarounds, the strict property invariant
flagged punctuation-only one-sided context in a few real presets.

The TypeScript `/>`, `}`, and `>` reports were converter bugs. They happened
when dirdiff split or flushed a difftastic-aligned structural line too early.
Those are now fixed by preserving explicit one-sided difftastic change pairs
and by letting an unfinished split-left line consume a following aligned right
line before a new left line forces out its residual.

The last report was
`python/create-app-runtime-config-collapses-service-block`, where difftastic
renders right-only `            )` as context. Because punctuation context is
still semantic identity, the converter now pairs single punctuation context
rows with a trailing structural fragment from the nearest preceding left-only
row. The repair is intentionally narrow: it handles one punctuation atom at a
time and does not reconstruct multi-character tails such as `);`, `})`, or
`])`.

## Split-fragment display caveat

Some fixtures split one source line across several display rows, for example
the Clojure wrapper and Python split-argument cases. Difftastic's JSON reports
`null` on the left for many of those right rows; dirdiff currently reconstructs
left fragments from the pending old line so the UI can show semantic context on
both sides.

That reconstruction is a dirdiff display choice, not something directly present
in `aligned_lines`. It should be kept conservative:

- do not create impossible line numbers;
- do not borrow arbitrary whitespace from the opposite side at the start of a
  line;
- do not mark reconstructed left context as changed unless difftastic supplies
  a changed span for it.

## Snapshot mismatch categories

Golden mismatches observed during this work are a mix of:

- now-fixed converter bugs, such as whole-line insert tokens for unchanged
  right-only tail context;
- exact token-shape differences, such as `&&` as one token instead of two `&`
  tokens;
- display choices around reconstructed split fragments.

Only the first category should be fixed by changing converter logic. The other
categories need deliberate product decisions before snapshots are updated.
