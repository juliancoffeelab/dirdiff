# Playwright Hunk Navigation Tests

Source:

- [`tests/playwright/hunk-nav.spec.mjs`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/playwright/hunk-nav.spec.mjs)
- [`tests/playwright/hunk-nav.helpers.mjs`](/Users/illiadenysenko/Workspace/lab/dirdiff/tests/playwright/hunk-nav.helpers.mjs)

## Why This Layer Exists

These tests cover behaviors that only show up in a real browser:

- smooth scrolling and settle timing
- DOM selection state
- fold-bar rendering and expand/collapse interactions
- viewport clamping near the bottom of the page
- keyboard wiring
- multi-file repo rendering
- reload and resize interactions

If a regression depends on actual scroll movement, layout, or browser event timing, this is the layer that should catch it.

## How These Tests Work

- Direct-file tests build temporary left/right text fixtures and open the app with `left_file` and `right_file` query params.
- Repo-mode tests create temporary git repositories, modify tracked files, and start `uv run dirdiff --headless` against that repo.
- Slow-scroll tests replace `window.scrollTo` with an animated test double so repeated input can be observed while scrolling is still in flight.
- Helper assertions check both geometric location and explicit DOM selection through `.active-hunk` and `data-hunk-index`.

## Covered Tests

### Basic direct-file navigation

`next hunk moves to the first hunk in direct-file mode`

- What it tests: first `Next` from the top selects the first hunk.
- How it tests it: opens a three-hunk direct diff and clicks `Next` once.
- Why it exists: protects the most basic entry path into hunk navigation.

`previous hunk moves backward from the middle hunk`

- What it tests: `Prev` moves backward from a non-edge hunk.
- How it tests it: navigates to the middle hunk, then clicks `Prev`.
- Why it exists: guards ordinary backward navigation without wrap behavior.

### Selection rendering

`selected hunk is highlighted subtly on both panes`

- What it tests: the selected hunk marks exactly two rows, one per pane.
- How it tests it: navigates to the first hunk and asserts `.active-hunk`, `aria-current`, and `data-hunk-index`.
- Why it exists: catches broken selection wiring between navigation state and rendered rows.

`top-level unchanged classes show method folds instead of a whole-class fold`

- What it tests: unchanged classes render method-level folds without a whole-class fold bar.
- How it tests it: opens a Python direct-file diff with an unchanged class and a later changed line, then inspects the fold-bar text.
- Why it exists: protects the DRAISS-style class special-case in the real browser.

`changed classes only fold unchanged methods`

- What it tests: changed classes keep changed methods expanded while still folding unchanged methods.
- How it tests it: opens a Python direct-file diff with one changed method and asserts only the unchanged method gets fold bars.
- Why it exists: protects the selective class-member folding rule end to end.

`fold bars and signature rows toggle collapsed regions without breaking hunk navigation`

- What it tests: clicking fold signature rows and fold bars expands/collapses hidden rows while hunk navigation still works afterward.
- How it tests it: expands and re-collapses a folded Python function, asserts hidden lines appear, then navigates to the remaining changed hunk.
- Why it exists: catches browser-only regressions at the boundary between fold UI and hunk navigation.

`fold toggle icons do not shift top-level code horizontally`

- What it tests: the fold toggle indicator does not push code text to the right on folded signature rows.
- How it tests it: opens a folded Python function diff and compares the rendered x-position of the folded signature text against a normal top-level code row in the same pane.
- Why it exists: protects the regression where the inline fold arrow behaved like an extra tab and visibly shifted code indentation.

`markdown folds only unchanged heading sections`

- What it tests: Markdown renders fold bars only for unchanged heading sections and not for changed sibling sections.
- How it tests it: opens a direct Markdown diff, checks that the unchanged `# Intro` section gets fold bars on both panes, and confirms the changed `# Tail` section stays expanded.
- Why it exists: protects the Markdown-specific heading-section fold policy in the real browser.

`adding a later markdown section keeps earlier unchanged sections folded`

- What it tests: inserting a new Markdown section does not suppress fold bars for older unchanged sibling sections.
- How it tests it: opens a direct Markdown diff where the right side inserts `# Added` between unchanged `# One` and `# Two` sections, then checks that both older sections still render fold bars on both panes.
- Why it exists: protects the browser-facing regression where adding a new same-level section could leave only the later unchanged section folded.

`collapsed folds keep later visible markdown headings aligned across panes`

- What it tests: collapsed fold bars do not knock later visible Markdown headings out of vertical alignment between panes.
- How it tests it: opens the same inserted-section Markdown diff and compares the rendered top positions of the visible `# Tail` heading rows.
- Why it exists: protects the layout regression where folded regions could shift later code around instead of preserving paired row alignment.

### Wrap behavior

`next hunk wraps after the final hunk settles at the bottom of the page`

- What it tests: `Next` wraps from the last hunk back to the first after scrolling has settled.
- How it tests it: walks to the last direct-file hunk, waits, then presses `Next`.
- Why it exists: protects the bottom-wrap bug class.

`previous hunk wraps after the first hunk settles at the top of the page`

- What it tests: `Prev` wraps from the first hunk to the last after settling.
- How it tests it: moves to the first hunk, waits, then presses `Prev`.
- Why it exists: keeps top-wrap behavior symmetric with bottom-wrap behavior.

### In-flight and burst navigation

`next hunk queues correctly while smooth scrolling is still in flight`

- What it tests: repeated `Next` during active smooth scroll advances sequentially.
- How it tests it: injects slow scrolling, presses `Next`, waits mid-flight, presses `Next` again, and checks the final hunk.
- Why it exists: protects the “hit next while still scrolling” regression.

`previous hunk queues correctly while smooth scrolling is still in flight`

- What it tests: repeated `Prev` during active smooth scroll advances sequentially backward.
- How it tests it: injects slow scrolling, moves to the middle hunk, then issues repeated `Prev` while motion is ongoing.
- Why it exists: keeps backward in-flight behavior as strong as forward behavior.

`rapid next bursts land on the sequential wrapped target`

- What it tests: fast bursts of `Next` preserve ordering and wrap correctly.
- How it tests it: issues five quick `Next` presses under slow scrolling and checks the final landing hunk.
- Why it exists: catches burst-input sequencing bugs.

`rapid prev bursts land on the sequential wrapped target`

- What it tests: fast bursts of `Prev` preserve ordering and wrap correctly.
- How it tests it: issues five quick `Prev` presses under slow scrolling and checks the final landing hunk.
- Why it exists: covers the backward burst path.

`alternating rapid bursts preserve move ordering`

- What it tests: alternating `Prev` and `Next` requests do not scramble queue order.
- How it tests it: issues a short alternating burst under slow scrolling.
- Why it exists: catches hidden assumptions that only work for repeated moves in one direction.

`rapid repeated input during smooth scrolling does not snap back to the wrong target`

- What it tests: burst input does not cause snap-back or a wrong final target after motion completes.
- How it tests it: issues three quick `Next` presses under slow scrolling, samples intermediate scroll positions, then checks the final hunk.
- Why it exists: protects the final-landing behavior after a noisy input sequence.

### Keyboard behavior

`keyboard shortcuts navigate next and previous hunks`

- What it tests: `n` moves forward and `Shift+N` moves backward.
- How it tests it: drives page-level key presses and asserts active hunk changes.
- Why it exists: protects keyboard-to-controller wiring.

`keyboard hunk shortcuts are ignored while an input is focused`

- What it tests: hunk shortcuts do not steal typing from form fields.
- How it tests it: focuses `#pathInput` and `#leftFileInput`, sends key presses through the inputs, then asserts no selected hunk appears and the fields receive the text.
- Why it exists: catches shortcut handlers that interfere with normal typing.

### No-hunk behavior

`navigation is a no-op when there are no hunks`

- What it tests: buttons and keyboard do nothing when there are no hunk anchors.
- How it tests it: opens an identical direct-file diff and checks that no scroll or selection state changes occur.
- Why it exists: prevents empty-state actions from causing bogus selection or scrolling.

### Manual scroll and viewport behavior

`manual scroll starting points navigate relative to the visible anchor position`

- What it tests: navigation decisions are relative to the current viewport anchor, not just prior controller state.
- How it tests it: scrolls to a position between hunks and checks that `Next` and `Prev` choose the expected neighboring hunks.
- Why it exists: catches off-by-one errors after manual user scrolling.

`smooth scrolling progresses over time and finishes at the correct target`

- What it tests: smooth scrolling produces observable intermediate motion and lands close to the requested target.
- How it tests it: injects slow scrolling, samples `scrollY` across time, then checks the final hunk and approximate target position.
- Why it exists: protects the real browser motion contract without overfitting to exact animation curves.

`resizing during smooth scroll preserves the selected target and later navigation`

- What it tests: viewport resize during an in-flight scroll does not lose the active target or break subsequent navigation.
- How it tests it: starts smooth scroll, resizes the viewport mid-flight, waits for settle, then navigates again.
- Why it exists: catches interactions between geometry changes and the nav controller.

### Single-hunk and bottom-clamp edge cases

`single-hunk diffs stay pinned to the only hunk across wraps and rapid input`

- What it tests: a one-hunk diff always resolves to that same hunk, even under wraps and burst input.
- How it tests it: opens a one-hunk direct diff and issues repeated `Next` and `Prev` bursts.
- Why it exists: protects the smallest non-empty edge case.

`later single-file tail hunks stay distinct even when bottom clamping stops scroll movement`

- What it tests: later bottom-clamped hunks can still advance selection even when scroll movement becomes very small near the end.
- How it tests it: creates a tall direct diff, finds a consecutive clamped tail pair from live DOM geometry, navigates into them, and asserts selection advances while `scrollY` changes only slightly.
- Why it exists: catches “last hunks collapse together” failures near document bottom.

### Repo-mode behavior

`repo mode navigates across file-card boundaries`

- What it tests: `Next` keeps working when the next hunk lives in another file card.
- How it tests it: creates a two-file repo diff with one hunk per file and navigates across the file boundary.
- Why it exists: protects global hunk ordering in repo mode.

`repo mode keeps later global hunks selected in the final file`

- What it tests: later global hunks in the last file still produce a real selected row pair.
- How it tests it: creates a repo diff with six hunks across two files, walks into the later hunks in the final file, and asserts `.active-hunk` uses the right global `data-hunk-index`.
- Why it exists: catches the “global nav index vs per-file DOM index” bug that made tail hunks appear to disappear.

`reloading the diff during in-flight scroll resets selection cleanly before the next navigation`

- What it tests: changing the diff while auto-scroll is in progress resets the old selection and allows clean navigation in the new result set.
- How it tests it: starts a repo-mode navigation, changes the path filter mid-scroll, waits for the single-file result, then navigates again.
- Why it exists: protects against stale selection and stale timer state across reloads.
