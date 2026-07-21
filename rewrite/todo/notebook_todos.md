# Notebook TODOs

This document preserves notebook-specific issues intentionally deferred from the
current rewrite. It records future investigation targets without authorizing an
implementation or changing the governing rewrite specifications.

## F75 — Define manifest cell-aggregate presence

`ManifestSummary` exposes `changed_cells`, `added_cells`, `modified_cells`, and
`removed_cells` as independently nullable values. `ManifestStatistics` does not
display `changed_cells`, but currently uses its presence to mount the Cells group
and then requires all three displayed aggregates. A response with null
`changed_cells` therefore hides any non-null displayed aggregates, while a
non-null `changed_cells` paired with a null displayed aggregate produces a
rendering error.

A later notebook contract review should decide whether the four manifest cell
aggregates are an all-or-none group. If they are, enforce that relationship at
the API boundary and retain one validated presentation branch. If partial
aggregates are intentional, specify how AppHeader presents each missing value
instead of treating `changed_cells` as an undocumented availability flag.

## F61 — Reconcile `changed_cells` with rendered `cells`

The notebook renderer obtains its summary count and rendered cell collection
from independent backend fields:

```tsx
<span class="badge badge-neutral">
  {props.backend_data.summary.changed_cells} changed cell
  {props.backend_data.summary.changed_cells === 1 ? "" : "s"}
</span>

<Show
  when={props.backend_data.cells.length > 0}
  fallback={
    <p class="file-placeholder">
      No changed cells detected for the selected notebook sides.
    </p>
  }
>
```

— [frontend/src/new/hud/NotebookFile.tsx:60](../../frontend/src/new/hud/NotebookFile.tsx#L60)

The validated API requires an integer `summary.changed_cells` and an array of
`cells`, but does not assert that their values agree. Contradictory input can
therefore display a non-zero changed-cell badge beside “No changed cells
detected,” or render cells while the badge reports zero.

A later notebook review should define the backend relationship between these
fields and assert it at the input boundary. The current rewrite accepts this
notebook behavior without correction.
