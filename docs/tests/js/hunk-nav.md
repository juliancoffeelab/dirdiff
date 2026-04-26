# JavaScript Hunk Navigation Tests

Source:

- [`tests/js/hunk_nav.test.cjs`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/js/hunk_nav.test.cjs)
- [`tests/js/hunk_nav_controller.test.cjs`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/js/hunk_nav_controller.test.cjs)
- [`tests/js/folds.test.cjs`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/js/folds.test.cjs)
- Code under test: [`src/dirdiff/static/hunk_nav.js`](/Users/illiadenysenko/Workspace/lab/dirdiff/src/dirdiff/static/hunk_nav.js)
- Additional code under test: [`src/dirdiff/static/folds.js`](/Users/illiadenysenko/Workspace/lab/dirdiff/src/dirdiff/static/folds.js), [`src/dirdiff/static/row_sync.js`](/Users/illiadenysenko/Workspace/lab/dirdiff/src/dirdiff/static/row_sync.js)

## Why This Layer Exists

These tests are the cheap, deterministic guardrail for hunk navigation logic.
They also cover the pure fold-row preprocessing used before the browser renderer
builds collapsible fold bars.

They do not try to prove browser rendering or actual scrolling behavior.
Instead, they validate the controller math and state transitions directly:

- snapshot normalization
- hunk index stepping
- wrap behavior
- target resolution
- active-index reuse
- reset and settle behavior

This layer is fast enough to run as part of normal `pytest` through the Python wrapper.

## How These Tests Work

- They run with Node’s built-in test runner.
- They import `src/dirdiff/static/hunk_nav.js` directly.
- They pass synthetic snapshots into pure helpers and into `reduceState()`.
- They assert exact controller state and exact effect payloads.

## `tests/js/hunk_nav.test.cjs`

`normalizeSnapshot sorts, dedupes, and computes currentPosition`

- What it tests: snapshot cleanup and derived fields.
- How it tests it: passes unsorted positions with close duplicates and checks normalized positions, signature, and `currentPosition`.
- Why it exists: makes later controller logic stable and deterministic.

`targetScrollTopForPosition clamps to the top and bottom of the document`

- What it tests: scroll target clamping.
- How it tests it: feeds positions above the top, in the middle, and beyond the maximum scroll range.
- Why it exists: protects top and bottom edge behavior.

`next navigation targets the first hunk below the current scroll anchor`

- What it tests: forward relative targeting.
- How it tests it: calls `pickRelativeIndex()` with positions and a current anchor.
- Why it exists: guards ordinary forward movement math.

`previous navigation targets the closest earlier hunk and wraps`

- What it tests: backward relative targeting and wrap.
- How it tests it: calls `pickRelativeIndex()` for a middle anchor and for a position above the first hunk.
- Why it exists: protects the core backward-selection rule.

`stepHunkIndex wraps in both directions`

- What it tests: explicit step-based wrap math.
- How it tests it: steps forward from the last index and backward from the first.
- Why it exists: protects repeated move sequencing once a current hunk is already known.

## `tests/js/hunk_nav_controller.test.cjs`

`active index is usable during auto-scroll even before scroll settles`

- What it tests: the controller keeps trusting the selected hunk while auto-scroll is active.
- How it tests it: constructs a state with `autoScrollInProgress: true` and checks `isActiveIndexUsable()`.
- Why it exists: protects in-flight repeated navigation.

`active index remains usable when the viewport is bottom-clamped at the target`

- What it tests: the active hunk still counts as current when bottom clamping prevents the ideal anchor position.
- How it tests it: uses a snapshot whose active hunk target equals `maxScrollTop`.
- Why it exists: protects settled bottom-edge behavior.

`request navigation is a no-op when no hunk anchors exist`

- What it tests: empty snapshots produce no scroll effect.
- How it tests it: dispatches `REQUEST_NAVIGATION` with no positions.
- Why it exists: prevents empty-state controller churn.

`first next request chooses the first hunk below the current scroll anchor`

- What it tests: initial forward target resolution.
- How it tests it: dispatches a first `next` request from the top of the page.
- Why it exists: guards the entry path into controller state.

`first previous request wraps to the last hunk when starting above the first anchor`

- What it tests: initial backward wrap from the top.
- How it tests it: dispatches a first `prev` request from the top.
- Why it exists: keeps the first backward action symmetric with forward behavior.

`repeated next requests during auto-scroll advance deterministically`

- What it tests: repeated `next` requests during in-flight motion step through hunks in order.
- How it tests it: dispatches three `next` requests against slightly changing snapshots before settle.
- Why it exists: protects sequential move semantics during scroll.

`rapid alternating requests preserve sequential move semantics`

- What it tests: alternating directions preserve order instead of recomputing from scratch incorrectly.
- How it tests it: dispatches `next`, `next`, `prev`, then `next`.
- Why it exists: catches queue-order bugs.

`signature changes invalidate the active index and recompute from scroll position`

- What it tests: if the visible hunk set changes, stale active-index state is not blindly reused.
- How it tests it: changes the positions signature between requests and checks the new target.
- Why it exists: protects reload and filtering behavior.

`settled event clears the auto-scroll flag without dropping the active target`

- What it tests: settle handling stops the in-flight state but preserves the selected hunk.
- How it tests it: requests navigation, then dispatches `SETTLED`.
- Why it exists: guards the transition from motion to stable state.

`reset clears all navigation state`

- What it tests: reset wipes navigation state clean.
- How it tests it: dispatches `RESET` against a dirty state object.
- Why it exists: protects diff reloads and mode changes.

## `tests/js/folds.test.cjs`

`normalizeFoldHints drops invalid ranges and sorts by row order`

- What it tests: raw fold hints are normalized into valid, ordered row ranges.
- How it tests it: passes invalid and out-of-order hints into `normalizeFoldHints()`.
- Why it exists: keeps the browser fold renderer from processing malformed payloads.

`addFoldRows inserts synthetic fold rows with folded payload slices`

- What it tests: fold hints become synthetic `status: "fold"` rows that retain the hidden row payload.
- How it tests it: feeds a simple function diff into `addFoldRows()` and inspects the returned fold row.
- Why it exists: protects the DOM rendering seam between backend fold hints and UI toggles.

`addFoldRows suppresses nested hints when an outer fold already consumed the rows`

- What it tests: nested fold hints do not render duplicate or overlapping fold bars.
- How it tests it: supplies overlapping outer and inner hints and asserts only the outer fold survives.
- Why it exists: protects the outermost-first fold rendering rule.

`row sync still collects fold bars as direct diff rows`

- What it tests: fold bars remain compatible with the existing row-sync collector.
- How it tests it: passes a synthetic container with ordinary rows and a fold bar into `collectDirectDiffRows()`.
- Why it exists: protects equal-height syncing once folds are present in the DOM.
