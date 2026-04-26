# Playwright Browser Tests

This document explains every Playwright testcase in the hunk-navigation browser suite, with emphasis on the internals each case is actually exercising. The point of this layer is not "does the navigation model work in theory?" The point is "does the rendered page behave correctly once scrolling, DOM state, layout, focus, and repo-mode rendering all get involved?"

## Shared Helpers And Invariants

Most tests rely on a small set of helper contracts from [`tests/playwright/hunk-nav.helpers.mjs`](../../../tests/playwright/hunk-nav.helpers.mjs).

`openDirectFixtureDiff()` creates a temporary pair of files and opens the app with `left_file` and `right_file` query parameters. Unless a test overrides it, the fixture is a 420-line direct diff with three changed lines at 120, 240, and 360. That means many tests implicitly depend on there being three hunk anchors in a tall enough page to require real scrolling.

`expectActiveHunk()` does not read the app's internal controller state. It computes the active hunk geometrically by finding the visible `.diff-row.hunk-anchor` whose absolute position is closest to `window.scrollY + HUNK_SCROLL_MARGIN`, where `HUNK_SCROLL_MARGIN` is `120`. That matters because many tests are really checking where the page landed, not just which row has a CSS class.

`expectSelectedHunkIndex()` and `expectSelectedHunkRows()` assert the DOM-side selection markers: exactly two `.diff-row.active-hunk` rows, one in each pane, both with `aria-current="true"` and the expected `data-hunk-index`. When a test uses these helpers, it is verifying rendered row selection rather than only scroll position.

`installSlowSmoothScroll()` patches `window.scrollTo` before page load. Smooth scroll calls are recorded in `window.__hunkNavScrollCalls`, then replayed through a deterministic `requestAnimationFrame` animation. This gives the suite a controllable in-flight scrolling model, which is how the queueing tests make repeated input timing reproducible instead of flaky.

Repo-mode tests use `createTempRepoFixture()` and `startTempRepoServer()`. The fixture creates a temporary Git repo, commits tracked files, rewrites selected lines in the worktree, then starts `uv run dirdiff --headless --repo-root <repo>` on a free port. Those tests are therefore exercising the real repo-backed app path, not a mocked JSON payload.

## `direct-nav.spec.mjs`

Source: [`tests/playwright/direct-nav.spec.mjs`](../../../tests/playwright/direct-nav.spec.mjs)

### `next hunk moves to the first hunk in direct-file mode`

This is the smallest direct-file navigation assertion. It opens the default three-hunk fixture, confirms that the rendered page exposes exactly three `.diff-row.hunk-anchor` elements, clicks the `Next Hunk` button once, and then uses `expectActiveHunk(page, 0)`.

The important internal detail is that `expectActiveHunk()` is geometry-based. This test is therefore checking that the first button press actually scrolls the viewport to the first anchor region, not merely that some internal "current index" changed.

### `selected hunk is highlighted subtly on both panes`

This test exists because landing on the right vertical position is not enough; the page also needs to mark the corresponding left and right rows as selected. After one `Next` click, it asserts:

- exactly two `.diff-row.active-hunk` rows
- exactly two `.diff-row.active-hunk[aria-current="true"]` rows
- exactly two rows with `.diff-row[data-hunk-index="0"].active-hunk`

Those assertions make the test sensitive to DOM wiring bugs where navigation works but only one pane highlights, the wrong rows highlight, or the accessibility marker falls out of sync with the visual state.

### `previous hunk moves backward from the middle hunk`

This test first advances twice so the viewport settles on the middle hunk, then clicks `Prev` once and expects the first hunk to become active.

The useful part here is that it avoids wrap behavior and does not start from the top of the page. It checks the ordinary backward path where the controller has already been exercised once and must resolve the previous anchor relative to a non-edge position.

### `next hunk wraps after the final hunk settles at the bottom of the page`

This test walks forward three times to reach the last hunk, waits `400ms`, then presses `Next` again and expects hunk `0`.

The delay is part of the test logic, not noise. It intentionally lets the browser settle near the bottom before checking wrap behavior, which is where off-by-one logic and bottom-clamp behavior tend to interfere with the next-target calculation.

### `previous hunk wraps after the first hunk settles at the top of the page`

This is the symmetric top-of-page version of the previous test. It moves to hunk `0`, waits `400ms`, then uses `Prev` and expects the final hunk.

The point is not just wrap symmetry as an abstract rule. It is checking that the "which anchor is currently active?" calculation behaves correctly when the viewport is parked near the top boundary, where geometry can differ from the middle-of-document case.

### `keyboard shortcuts navigate next and previous hunks`

This test focuses the `#nextHunkBtn` button first, then sends `n`, `n`, and `Shift+N` through `page.keyboard`. After each keystroke it waits for scrolling to settle and then checks the selected hunk index.

The focus step matters because it ensures page-level keyboard handling is active without relying on arbitrary click state. The use of `expectSelectedHunkIndex()` means this test is specifically verifying that keyboard shortcuts drive the same row-selection contract as button clicks.

### `keyboard hunk shortcuts are ignored while an input is focused`

This test focuses `#pathInput`, presses `n` and `Shift+N`, then focuses `#leftFileInput` and presses `n` again. It asserts that `window.scrollY` stays at `0` and that the inputs receive the literal text.

Internally, this protects against global shortcut handlers that fail to bail out when an input element owns focus. It is a browser-only concern because the bug is not "navigation math is wrong"; the bug is "the event target and focus rules were ignored."

### `navigation is a no-op when there are no hunks`

This case turns on the slow-scroll shim and opens an identical left/right fixture so no `.diff-row.hunk-anchor` elements exist at all. It then clicks both buttons and sends both keyboard shortcuts.

The assertions go beyond "no exception thrown." The test checks that the active hunk index stays `-1`, `window.scrollY` stays `0`, and `getScrollToCalls()` returns an empty list. That means the app is not even attempting a scroll request when the diff contains no navigable hunks.

### `manual scroll starting points navigate relative to the visible anchor position`

This is one of the more internal direct-nav tests. It creates a larger fixture with `900` total lines and changed lines at `150`, `350`, `550`, and `750`, then computes a midpoint window between neighboring hunk anchors inside the browser.

The page-side `findNavigationWindow()` helper collects each anchor's absolute Y position, computes a midpoint between consecutive anchors, subtracts `HUNK_SCROLL_MARGIN`, and chooses a scrollTop that stays within document bounds. The test scrolls to that computed location, clicks the page to avoid focused-input interference, and verifies that `n` lands on the later hunk while `Shift+N` lands on the earlier one.

This is not a generic "manual scrolling works" check. It is specifically asserting that the next-target calculation is derived from the current viewport anchor window, not from stale controller state left behind by previous navigation.

## `folds.spec.mjs`

Source: [`tests/playwright/folds.spec.mjs`](../../../tests/playwright/folds.spec.mjs)

### `top-level unchanged classes show method folds instead of a whole-class fold`

The fixture is a tiny Python class with two unchanged methods and a changed trailing assignment outside the class. The rendered page should produce four fold bars total: two panes times two unchanged methods.

The test explicitly checks for fold bars containing `def a(self):` and `def b(self):`, and for zero fold bars containing `class Example:`. That distinction matters because the test is not merely checking that "something folded." It is asserting the class-specific folding policy that unchanged methods remain individually foldable without collapsing the entire class block into one opaque region.

### `changed classes only fold unchanged methods`

This fixture is nearly identical, except method `b` changes on the right side. The expected fold bars drop from four to two, and only `def a(self):` should remain foldable.

Internally, this checks that the fold rendering respects change boundaries inside a structural container. A whole-class or over-broad fold algorithm would still find a class shape and hide too much. This test ensures the browser representation reflects the finer-grained fold hints.

### `fold bars and signature rows toggle collapsed regions without breaking hunk navigation`

This test uses a small Python function plus a changed trailing assignment. It starts by asserting two fold bars, then clicks the first `.diff-row.fold-toggle-row` to expand the folded body, checks that the fold bars disappear, and confirms that a previously hidden `return value` line becomes visible in both panes.

It then clicks the same toggle row again to re-collapse the block, rechecks the fold bars, and finally presses `Next` to ensure hunk navigation still works.

The internal concern here is UI state interaction: fold toggling mutates DOM visibility and row layout, so the test makes sure those changes do not corrupt later hunk navigation or leave the fold UI in an inconsistent state.

### `fold toggle icons do not shift top-level code horizontally`

This uses the same function fixture but asks a layout question instead of a visibility question. `expectCodeTextAligned()` compares the X positions of:

- `.diff-pane:nth-of-type(1) .diff-row.fold-toggle-row .line-code-content`
- `.diff-pane:nth-of-type(1) .diff-row.replace .line-code-content`

The helper polls bounding boxes until the X difference is within `1px`. That makes the test a guard against a specific layout regression: the fold toggle icon must not behave like extra indentation and shove the code text to the right.

### `markdown folds only unchanged heading sections`

This fixture switches to Markdown by naming the left file `left.md`. It builds two sections, `# Intro` and `# Tail`, and only changes the body under `# Tail`.

The test expects exactly two fold bars, both containing `# Intro`, and zero containing `# Tail`. That is narrower than "Markdown folds work." It is checking the current Markdown policy that only unchanged heading sections get collapsed affordances, while changed sections stay expanded.

### `adding a later markdown section keeps earlier unchanged sections folded`

This fixture inserts a new `# Added` section on the right side between unchanged `# One` and `# Two`, with a changed `# Tail` afterward.

The test expects four fold bars total and specifically verifies that `# One` and `# Two` still each produce a left/right fold bar while `# Added` produces none.

The internal regression shape is important here: insertion of a new same-level heading can disturb the section-partitioning logic so that only later siblings continue to fold. This case makes sure earlier unchanged sections survive that re-partitioning.

### `collapsed folds keep later visible markdown headings aligned across panes`

This is another Markdown insertion scenario, but the assertion is geometric rather than count-based. After confirming that `# Intro` is folded, it calls `expectMatchingRowTops(page, "# Tail")`.

That helper finds the first row containing `# Tail` in each pane and polls the absolute Y difference until it is within `1px`. The test therefore protects against a subtle but user-visible failure mode: the fold decisions can be logically correct while later visible headings drift vertically out of alignment.

## `smooth-scroll.spec.mjs`

Source: [`tests/playwright/smooth-scroll.spec.mjs`](../../../tests/playwright/smooth-scroll.spec.mjs)

### `next hunk queues correctly while smooth scrolling is still in flight`

This test installs the slow-scroll shim, opens the default direct fixture, presses `Next`, waits `1000ms` while the synthetic smooth scroll is still ongoing, presses `Next` again, waits `2000ms`, then expects hunk `1`.

The key internal detail is that the second click happens before the first synthetic scroll completes. The test therefore verifies queueing behavior under in-flight motion rather than simple repeated navigation after settle.

### `previous hunk queues correctly while smooth scrolling is still in flight`

This is the backward-direction counterpart. It first moves to hunk `1`, then presses `Prev`, waits mid-flight, presses `Prev` again, and finally expects wrap-around to hunk `2`.

This protects the reverse queueing path, which often differs in implementation from forward navigation once wrap logic gets involved.

### `rapid next bursts land on the sequential wrapped target`

With slow scrolling active, this test clicks `Next` five times in immediate succession, waits `2200ms`, and expects hunk `1`.

Given a three-hunk fixture, five forward moves from the initial position should wrap and land on the second visible target in sequence. The test is specifically about preserving move ordering under burst input, not about how fast the animation itself runs.

### `rapid prev bursts land on the sequential wrapped target`

This mirrors the previous test with five immediate `Prev` clicks and also expects hunk `1`.

That shared target is not accidental. It proves that backward wrap math and burst queueing produce the same deterministic result as repeated logical predecessor steps, rather than collapsing bursts or skipping across the ring incorrectly.

### `alternating rapid bursts preserve move ordering`

This is the ugly input pattern: `Prev`, `Next`, `Prev`, `Next`, `Next`, all before settle. After `2200ms`, the selected hunk should be `1`.

The purpose is to catch queue implementations that only behave under repeated input in one direction. Alternating bursts force the app to preserve exact request order instead of coalescing by direction.

### `smooth scrolling progresses over time and finishes at the correct target`

This test installs a `1600ms` scroll shim, clicks `Next`, samples `window.scrollY` seven times at `180ms` intervals, and inspects the recorded `window.__hunkNavScrollCalls`.

It asserts:

- exactly one recorded scroll call
- `behavior === "smooth"`
- more than three distinct rounded scroll positions
- increasing motion over time
- an early sample still below the final target

After settle, it expects active hunk `0` and checks that final `scrollY` is within `120px` of the recorded target top.

This is the closest thing the suite has to a motion-contract test. It does not overfit to an exact easing curve, but it does require observable intermediate movement and a final landing near the requested target.

### `rapid repeated input during smooth scrolling does not snap back to the wrong target`

This also uses the `1600ms` shim, but it clicks `Next` three times immediately, samples scroll positions six times at `160ms` intervals, and checks that motion continues forward before finally expecting hunk `2`.

The bug class here is snap-back: a later queued target appears selected briefly, then the page resolves to an earlier destination when an older animation callback wins. This test ensures the final target remains the last logical request.

### `single-hunk diffs stay pinned to the only hunk across wraps and rapid input`

The fixture here has `260` lines and exactly one changed line at `180`, so only one hunk anchor exists. With slow scrolling enabled, the test clicks `Next`, then `Prev` three times, then `Next` four times, checking after each burst that both the active and selected indexes remain `0`.

This guards the smallest non-empty navigation case. Wrap logic, burst queueing, and selection updates should all collapse to the same single target instead of producing null selections or phantom indexes.

### `later single-file tail hunks stay distinct even when bottom clamping stops scroll movement`

This fixture is deliberately shaped to create several late hunks near the bottom: `120`, `540`, `590`, and `615` in a `620`-line file. The test sets a tall viewport, waits for settle, then runs page-side code that computes each hunk's target scroll position after applying the standard top margin and browser max-scroll clamp.

It scans from the tail for a consecutive pair whose clamped targets round to the same value. That means the browser can barely move, or cannot move at all, between the two later hunks. The test then navigates to the first of that pair, records `scrollY`, navigates once more, and asserts that the selected index advanced while the two final `scrollY` values differ by less than `120`.

This is a very specific browser pathology test: distinct logical hunks must remain distinct even when viewport clamping removes most of the physical scroll delta between them.

### `resizing during smooth scroll preserves the selected target and later navigation`

This test uses a `520`-line fixture with four hunks, starts a slow scroll, waits `350ms`, resizes the viewport to `1280x540`, waits for settle, then confirms the selected target stayed on hunk `0`. After that it clicks `Next` again and expects hunk `1`.

The point is to catch stale geometry assumptions. A resize during in-flight scrolling can invalidate target positions, active-anchor calculations, and settle logic. The test makes sure the app recovers cleanly and remains navigable afterward.

## `repo-mode.spec.mjs`

Source: [`tests/playwright/repo-mode.spec.mjs`](../../../tests/playwright/repo-mode.spec.mjs)

### `repo mode navigates across file-card boundaries`

This uses the default repo fixture, which creates two tracked files:

- `alpha.txt` with one changed line at `80`
- `beta.txt` with one changed line at `180`

After committing the clean versions and modifying the worktree, the test launches the real app in repo mode, confirms two `.file-card` containers and two hunk anchors, then presses `Next` twice and expects hunk `0` then hunk `1`.

Internally, this proves that global navigation is not scoped to the current rendered file card. The second `Next` must cross a DOM boundary and still land on the next repo-wide hunk.

### `repo mode keeps later global hunks selected in the final file`

This fixture scales the repo scenario up to six total hunks across two files. `alpha.txt` has changed lines at `40`, `120`, and `200`; `beta.txt` has changed lines at `300`, `360`, and `405`.

The test advances four times to reach the later hunks in the second file, then checks both `expectActiveHunk()` and `expectSelectedHunkRows()` for indexes `3`, `4`, and `5`.

The important internal distinction is between global navigation indexes and per-file DOM layout. A broken implementation can advance the global index correctly while failing to render the expected `.active-hunk` rows in the final file card. This test explicitly checks both pieces.

### `reloading the diff during in-flight scroll resets selection cleanly before the next navigation`

This test combines repo mode with the slow-scroll shim. The repo fixture has two files with two hunks each, the page starts a navigation, then after `250ms` the test fills `#pathInput` with `beta.txt` while scrolling is still active.

That input change causes the page to reload into a single-file repo result. The test confirms:

- only one `.file-card` remains
- the heading `beta.txt` is present
- no `.diff-row.active-hunk` rows survive from the old result set

Only after that cleanup check does it click `Next` again and expect selected hunk `0`.

This is the stale-state guard for repo reloads. It ensures old timers, old row selections, and old target indexes do not leak into a freshly loaded diff result.
