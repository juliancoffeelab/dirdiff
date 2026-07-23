# Line-pin testing

This is the living test document for the line-pin implementation. It turns the
behavior guidance in
[Stage 10](../stages/10_line_pins.md) into concrete, bounded tests and records
what was actually run. Stage 10 is input to this document, not proof that the
implementation works.

The document must be updated when a test exposes a bug, when a better
reproduction is found, when a fixture is added, and after the final unpatched
browser run.

## Test rules

- Tests seek failures and edge cases; merely executing the happy path does not
  pass a scenario.
- A focused interaction should expose its result within three seconds after its
  required files are ready. Setup time is recorded separately so a slow backend
  cannot disguise slow or hanging browser behavior.
- Existing scroll presets are preferred. A missing fixture may be added only as
  a numbered `_test_preset_<N>` scroll preset with immutable time snapshots.
  Immediately before creating one, reread `tests/presets/scroll`, reserve the
  next unused number, and give every immutable child fixture a unique name.
- Diagnostic browser instrumentation may record calls while forwarding to the
  original browser API unchanged.
- Diagnostic monkey-patches may delay, reject, abort, or otherwise force rare
  lifecycle states. Each patch must be installed by the test, removed by a page
  reload or a fresh page, and identified in the result.
- A diagnostic result alone is not final acceptance. Final acceptance always
  starts from a fresh application page running the real implementation.
  Ordinary behavior is then run without injection. Failure and race contracts
  may use deterministic test-controlled network faults or scheduling barriers,
  but must not replace, branch, or suppress any application algorithm.
- Internal state and DOM attributes may help locate a failure, but the final
  assertion must use user-visible behavior: the URL, painted row, viewport,
  Toast, error surface, controls, or unchanged hunk display.
- Tests do not change application contracts or weaken an existing assertion to
  make a scenario pass.
- Test behavior and expected results are not edited without user approval.
- Tests do not add shared test helpers.

## Time budget

The fast pass contains LP-S1 through LP-S3 and the focused browser cases whose
headings are marked `fast`. Once a preset is loaded, each fast case has a
three-second interaction budget. The complete fast pass should finish within
ninety seconds, excluding the first backend parse of a preset.

The geometry pass may load the four existing scroll presets once each. It
reuses an already-ready page for multiple non-mutating measurements, but each
final behavioral case starts from a fresh URL or reload so prior DOM state
cannot make it pass accidentally.

LP-B9 and LP-B10 each have a sixty-second overall deadline for all three
cancellation windows. LP-B11 has forty seconds for its two injected failures.
LP-B12, LP-L2, LP-G1, LP-G2, LP-G3, and LP-G4 each have a fifteen-second deadline
after the first preset parse. LP-B13, LP-L3, and LP-L4 each have a twenty-second
deadline. LP-L3 and LP-L4 include visible Retry in that deadline. LP-L1 carries
its own stricter deadline.

No test waits on an unbounded condition. A timeout is a failed test with the
last visible state recorded.

## Fixtures

| Fixture                                        | Purpose                                                            |
| ---------------------------------------------- | ------------------------------------------------------------------ |
| ordinary text preset selected during execution | split/inline identity, direct activation, folding, collapsing      |
| notebook preset selected during execution      | `cell_key` disambiguation and notebook repaint                     |
| `many-files`                                   | strict file-lane order, distant targets, disposal, layout movement |
| `mixed-file-sizes`                             | mixed geometry around the destination                              |
| `sandwich`                                     | target between large files and virtual-to-rich movement            |
| `lazy-files`                                   | deferred target, explicit Retry, lazy-info and file failures       |

The exact preset URL, engine, view, profile setting, viewport, target file,
region, side, and line are written into the execution log when the case runs.
If none of the existing presets supplies a required shape, the log first
records why it is insufficient before a numbered `_test_preset_<N>` is added.

## Observational diagnostics

These diagnostics may be installed from the browser test page. They record
behavior but call the original browser operation with the original receiver and
arguments.

- Count every in-scope programmatic scroll path while forwarding unchanged:
  `Element.prototype.scrollIntoView`, element and window `scroll`, `scrollTo`,
  and `scrollBy`, plus direct `scrollTop`/`scrollLeft` writes if the inspected
  implementation uses them. Record the target element's complete line-pin
  identity and distinguish these calls from test-driven manual scrolling.
- Count `history.replaceState` calls and record the resulting URL.
- Record `window.scrollY`, the target row rectangle, and the selected hunk
  attributes immediately before and after an interaction.
- Record `.pinned-line`, transient Toast, persistent Toast, and
  ChangeSet-boundary error counts.

The page is reloaded after diagnostics. The final acceptance interaction then
runs without these wrappers.

## Diagnostic fault injection

Behavior-changing patches are restricted to finding and reproducing lifecycle
bugs that would otherwise be timing-dependent:

- delay a file response before and after target admission;
- reject one target file response;
- reject lazy-info;
- delay line preparation;
- abort immediately before Navigation's final action;
- throw from line preparation or Navigation;
- dispose or replace the ChangeSet while one of those operations is pending.

Each injected test first demonstrates that the forced condition occurred. It
then checks the specified visible behavior on the real application algorithm.
Cases with an ordinary equivalent also run after a fresh page load without the
patch. Cases whose subject is the injected failure retain that injection as
diagnostic acceptance and do not claim an impossible unpatched reproduction.
LP-B11 is the sole diagnostic allowed to replace the operation that produces
the chosen throw; it does not replace the error-handling algorithm being tested.

## Fast static checks

### LP-S1 — prohibited machinery

Search the line-pin implementation and its in-scope callers for Solid effects,
polling or retry timers, `MutationObserver`, revision watchers, delegated
ChangeSet click listeners, capture-phase click spies, history listeners, query
observers, `fetchQuery`, and calls to `selectHunk()`.

Failure means any prohibited mechanism participates in line-pin behavior. A
text match is inspected in context rather than accepted or rejected
mechanically.

### LP-S2 — one identity path

Inspect all declarations and reads of line-pin identity. The URL must be the
only authoritative identity; retained `AbortController` and current lane work
must not become another target store.

Pinnable line DOM carries only exact side and line coordinates. Its enclosing
DiffGrid supplies file and nullable region from typed renderer inputs. Failure
means line DOM duplicates file or region, encodes a missing region with a
sentinel string, or stores a listener-bound marker. Direct activation must use
one delegated DiffGrid-root listener that survives explicit row replacement and
is removed on component cleanup.

Failure means FileCard, DiffGrid, Navigation, Solid state, or DOM decoration can
independently decide which line is pinned.

Also verify that each `ChangeSetSnapshot` calls `linePins()` exactly once and
passes that same retained instance to every DiffGrid in the snapshot. Multiple
instances fail even when each independently treats the URL as authoritative,
because they split the cancellation lifetime.

### LP-S3 — one loading path

Inspect the ChangeSet file lane and every line-pin caller. Target files must use
the canonical manifest-ordered lane, query, admission, and FileCard.

Failure means line pins can fetch a file, observe a second query, admit a file
outside the lane, or continue past an unfinished ready target.

Also verify that `ChangeSetSnapshot` obtains and resolves its one initial
`parseUrl()` result before the file lane can start. Starting ordinary or
explicit file work first and attaching a parsed target afterward fails even if
all later work uses the canonical lane.

## Fast browser cases

### LP-B1 — direct ordinary-line toggle (`fast`)

1. Open a ready ordinary FullFile.
2. Record selected hunk identity and viewport position.
3. Click one pinnable line number on the left side.
4. Click a different pinnable line number on the right side.
5. Click the right-side line again.
6. Pin a line, then drag a browser text selection across code on only its left
   side.

Pass requires, within three seconds of each click:

- the first click writes the exact left-side target and paints the complete row;
- the second click replaces it with the exact right-side target and moves the
  decoration;
- the third click removes the target and decoration;
- no click scrolls, loads a file, or changes selected hunk identity;
- the drag produces a visible browser selection whose anchor and focus remain
  inside left-side code and whose selected text contains no right-side content.

This case targets accidental restoration, cross-side identity collapse,
row-fragment painting, and hunk-selection coupling.

### LP-B2 — direct notebook toggle (`fast`)

Pin equal line numbers in two different notebook source regions, then remove the
second pin.

Pass requires the URL to contain the exact non-empty backend `cell_key`, only
the matching region's complete row to be painted, no output or metadata row to
match, and no scroll or hunk-selection change.

This case targets file-plus-line matching that ignores notebook region identity.

### LP-B3 — URL-preserving direct toggle (`fast`)

Start with a URL containing query parameters and unrelated hash fields. Directly
pin, replace, and remove one rendered line, recording `history.length` before
and after.

Pass requires every operation to use replacement rather than a new history
entry, preserve the pathname and query byte-for-byte, preserve every unrelated
hash field byte-for-byte, and change only the one `pin` field.

This case targets whole-hash replacement, query loss, and accidental browser
history entries.

### LP-B4 — initial ordinary restoration

1. Start from a fresh URL containing a valid ordinary-text pin.
2. Record the initial selected hunk once the ChangeSet exists.
3. Allow the normal file lane to reach the target.

Pass requires strict loading through the target, exact side/line preparation,
complete-row decoration, one centered final scroll, and unchanged selected hunk
identity.

Run once in split view and once in inline view. This case targets duplicate
scrolls, approximate matching, and pin-driven hunk selection.

Repeat with the target FileCard initially collapsed and with the exact target
line inside an initially folded range. Pass additionally requires preparation
to expand or unfold the exact target before the same one final scroll. When the
target becomes the expanded fold edge, the exact URL remains and Navigation
still scrolls to it, but `.pinned-line` must not paint that edge.

### LP-B5 — initial notebook restoration

Start from fresh URLs for two notebook source pins that use the same line number
and different non-empty backend `cell_key` values.

Pass requires each run to load and prepare the exact region, paint its complete
row, perform one centered final scroll, and leave the other region unmatched.
The execution log records both exact regions and screenshots.

This case targets region-aware direct painting that nevertheless restores by
file and line alone.

### LP-B6 — repaint without restoration (`fast`)

With a ready pinned row, collapse and reopen its FileCard, collapse and reopen a
containing directory, fold and unfold around the line, switch inline/split, and
allow the pinned file to become virtual and rich again.

Pass requires the URL to remain unchanged throughout. Decoration disappears
only while the exact row is absent and returns only when explicit row creation
renders that exact row as an ordinary row. An explicitly recreated expanded
fold edge retains the URL target but suppresses decoration until the coordinate
is ordinary again. None of these actions performs restoration scrolling.

This case targets URL clearing, hidden repaint listeners, virtualization locks,
and stale decoration.

### LP-B7 — non-pinnable rows (`fast`)

Interact with a collapsed fold edge, an expanded fold edge, a
duplicate-suppressed inline line number, and an absent-side row. Run the
applicable cases in split and inline view.

The collapsed fold edge expands without changing the URL, decoration, viewport,
or selected hunk identity. Clicking the expanded fold edge's line number, code,
or background collapses it with those same values unchanged and never reaches
delegated line-pin activation. An ordinary row inside the same expanded range
remains pinnable. The duplicate-suppressed inline number and absent-side row do
not change the URL, decoration, viewport, or selected hunk identity. No
non-pinnable control may manufacture a missing semantic coordinate.

This case targets event delegation that treats every visible line-number area
as a valid pin.

### LP-B8 — missing and malformed targets

Run fresh URLs containing:

- a file absent from the manifest;
- a valid target file with a line absent from the complete file;
- malformed JSON;
- duplicate `pin` fields;
- empty fields;
- a non-positive line;
- a non-canonical decimal line.

Every URL also contains unrelated hash fields.

Pass requires one two-second “Line pin unavailable” notice per load and no
loading or scrolling caused by malformed identity. Genuinely missing file/line
removes only that exact target and preserves every unrelated hash field
byte-for-byte. Malformed identity and all unrelated fields remain untouched.

This case targets silent repair, nearest-line substitution, repeated notices,
and accidental deletion or reserialization of unrelated hash fields.

### LP-B9 — cancellation by direct activation

1. Delay the old target during file loading, preparation, and immediately
   before final navigation in separate diagnostic runs.
2. In one run, directly pin another already-rendered line.
3. In another run, directly activate the same pending target to remove it.
4. Release the old operation.

Pass requires the replacement run's new target and decoration to remain and the
removal run's pin and decoration to remain absent. In both runs, the old
operation performs no later URL mutation, painting, Toast, or scroll.

The final run starts from a fresh application page. Its test harness may insert
one deterministic scheduling barrier around the original loading, preparation,
or final-navigation operation, but it must call the original operation
unchanged after release. This case targets retained stale work and cancellation
checked too early.

### LP-B10 — ChangeSet disposal

Dispose or replace the ChangeSet during target loading, preparation, and
immediately before final navigation.

Pass requires no later URL mutation, painting, Toast, or scroll from the
disposed snapshot.

Exercise the public replacement boundaries separately: deactivate and
reactivate the Tab, replace DiffParams, replace the manifest through manual
reload, and expire the repository cache. When the URL pin remains current, the
replacement snapshot must parse it once, restart restoration through the
replacement manifest and cache ID, and perform no work through the disposed
snapshot. Repository-cache expiration is routine snapshot replacement and must
not produce an error Toast.

This case targets browser work surviving its `ChangeSetSnapshot`.

The final run uses a fresh application page and the same kind of deterministic
pass-through scheduling barrier as LP-B9. Natural timing is not accepted as
proof of the three required disposal windows.

### LP-B11 — unexpected failure

On separate fresh application pages, replace one mounted target FileCard's
`prepareLine_impl` with a function that throws the chosen contract error, then
replace one mounted virtual FileCard's DOM-attached
`intersectsRichEntryZone()` with a throwing operation during a line-navigation
geometry pass. Do not alter the ErrorBoundary, file lane, LinePins error path,
Toasts, or any other application algorithm.

Pass requires damage at the `ChangeSetSnapshot` ErrorBoundary and exactly one
persistent error Toast per failure. The failure must not become a transient
notice, `missing`, `stopped`, a file-error LazyFile, a swallowed rejection, or
an unhandled rejection.

This diagnostic acceptance case targets duplicate Toast paths and over-broad
recovery. The injected throw is the behavior under test and therefore has no
unpatched equivalent.

### LP-B12 — duplicate manifest path

On a fresh page, intercept one manifest response and duplicate one canonical
display path while leaving the rest of the payload valid. Confirm that the
received payload contains the duplicate before releasing it to the application.
The fresh URL contains a valid pin whose `file` is exactly that duplicated
canonical path and whose remaining coordinates are valid for the duplicated
entry.

Pass requires visible `ChangeSetSnapshot` contract failure and exactly one
persistent Toast. Choosing the first or last duplicate, showing a transient
notice, or continuing restoration fails the case.

The test uses response injection because a filesystem preset cannot naturally
contain two entries with the same canonical path. The application algorithm is
not replaced or patched.

### LP-B13 — manual scroll while restoration is pending

On a fresh application page, install one pass-through barrier before the
initial restoration's final Navigation action. Manually scroll the document to
a visibly different file while the target is pending, then release the original
operation.

Pass requires the pin and selected hunk to remain unchanged while the user
scrolls, followed by the one eventual exact centered restoration scroll. Manual
scrolling must not stop, replace, or make the pending target dormant.

This case targets accidental coupling between ScrollGuard/user scrolling and
line-pin cancellation.

## File-lane and failure cases

### LP-L1 — distant ordered target

Use `many-files` manifest index 9
(`10-frontend-src-new-hud-Profile/old.tsx`) as the fixed target and manifest
index 11 (`12-lazy-deleted-server/old.py`) as the later explicit LazyFile.
Record the manifest once before the run and fail if those fixed identities no
longer occupy those indices.

Delay at most six observed response completions; release each within 250 ms and
release the rest immediately. The complete scenario has a fifteen-second
timeout including initial target loading.

While the target is pending, click a later LazyFile's visible explicit-load
plank.

Pass requires:

- every automatic file through the target to load in manifest order;
- the target to use the canonical query and admission;
- no request or admission after target index 9 to start before target
  restoration completes;
- clicking the later LazyFile's visible explicit-load plank leaves that action
  waiting while target work has priority;
- the lane to block beyond the ready target while restoration runs;
- ordinary work to resume after restoration.

This case targets a second target loader, priority inversion, and lane
continuation before final navigation.

### LP-L2 — deferred Lazy target

Start with a valid URL target in a deferred LazyFile.

Pass requires strict loading of preceding automatic files, explicit loading of
the target by the existing lane, normal admission, restoration, and subsequent
lane continuation. The Lazy plank is not clicked by the test.

This case targets treating a pinned LazyFile as inert or bypassing manifest
order.

### LP-L3 — file failure and Retry

On a fresh application page, make the test harness reject the target's first
network response, then use its visible RetryButton and allow the real response.
Do not patch the file lane, query, RetryButton, or restoration algorithm.

Pass requires the ordinary error LazyFile and ordinary error Toast, unchanged
URL, continued ordinary lane work, no automatic retry loop, and restoration of
the same still-current target after explicit Retry.

This case targets line-pin-specific file recovery and lost dormant work.

### LP-L4 — lazy-info failure and Retry

On a fresh application page, make the test harness reject lazy-info once, then
use the affected file's visible RetryButton and allow the real response. Do not
patch the lazy-info observer, file lane, RetryButton, or restoration algorithm.

Pass requires the same orthogonal behavior as LP-L3: ordinary failure
presentation, unchanged URL, continued permissible lane work, no automatic
retry loop, and resumed restoration after explicit Retry.

This case targets a permanently abandoned target or line-pin-owned retry path.

## Geometry cases

### LP-G1 — many files

Restore a distant target in `many-files` after the page has a mixture of rich
and virtual FullFiles above it.

Before navigation, record every participating FileCard's path and
`data-file-render` state. Record the exact virtual FileCard whose original
`intersectsRichEntryZone()` result is true for the hypothetical destination,
and count its original `waitToEnrich_impl()` calls without replacing either
operation.

Pass requires that exact candidate to enrich at most once, followed by target
recalculation and one final programmatic scroll. The pass-through scroll
diagnostic records the exact prepared row and requires its midpoint immediately
after the forwarded scroll to be within eight pixels of the clamped viewport
midpoint. No later application scroll may correct the result.

After two animation frames, the exact row may either remain rendered or
disappear through ordinary permitted virtualization. If it remains, its
midpoint must still satisfy the eight-pixel tolerance. If it becomes virtual,
the URL must remain exact, the target FileCard must retain stable reserved
geometry at the landed document position, and no correction scroll may occur.

### LP-G2 — mixed file sizes

Choose a target whose hypothetical centered viewport crosses a nearby virtual
FullFile's rich-entry zone.

Record the target, the named virtual candidate, its original true
rich-entry-zone result, and its virtual pre-state. Pass requires that file to
enrich at most once during the operation, followed by target recalculation and
the same forwarded-scroll and post-virtualization requirements as LP-G1.

### LP-G3 — sandwich

Choose the compact middle file and a target beyond it in separate runs so large
files can change geometry on both sides of the destination.

Record the virtual pre-state and exact candidate identities on both sides.
Pass requires the same forwarded-scroll and post-virtualization requirements
as LP-G1 without polling, timer retries, repeated scrolls, or a virtual file
remaining rich merely because it once participated.

### LP-G4 — lazy files

Run one ordinary ready target and one deferred target in `lazy-files`.

Pass requires the ready target not to disturb unrelated Lazy planks and the
deferred target to use the canonical file lane before the same finite geometry
pass. Record all participating virtual pre-state, every true rich-entry-zone
candidate, and the same forwarded-scroll and post-virtualization evidence.

## Acceptance tiers

Stage 10 supplied the behavior matrix; it did not make every possible
fault-injection case a completion gate.

The required current acceptance is:

1. LP-S1 through LP-S3;
2. exact direct left/right and notebook pinning;
3. exact ordinary and notebook restoration without hunk selection;
4. folded-line preparation;
5. repaint after explicit row creation and disappearance while the row is
   absent;
6. the complete malformed and missing URL matrix;
7. distant manifest-ordered restoration and a deferred LazyFile target;
8. one many-files and one mixed-size geometry run;
9. fresh unpatched reruns after every implementation correction;
10. format, type, focused lint, and deep reviewer passes.

LP-B9 through LP-B13, LP-L1's explicit later-plank priority trace, LP-L3,
LP-L4, and fully instrumented LP-G1 through LP-G4 are further diagnostic
coverage. They remain in this living document because they are valuable
bug-seeking tests, not because an unavailable deterministic browser checkpoint
should block the current implementation. Any failure when one is run remains a
real bug; the distinction changes test scheduling, not expected behavior.

Every run distinguishes unpatched ordinary acceptance, pass-through scheduling
diagnostics, network fault injection, and injected contract failure. None is
reported as another category.

## Execution log

### 2026-07-23 — delegated DiffGrid activation

The existing Vite session and a separate fresh browser page verified the
line-local identity and listener-lifecycle changes without diagnostic patches.

- Ordinary and notebook DiffGrids contained no `data-line-pin-file`,
  `data-line-pin-region`, or `data-line-pin-bound` attributes. Pinnable line
  numbers retained only their exact side and line coordinates.
- Direct ordinary activation still pinned and unpinned the complete row.
- Direct notebook activation distinguished `alpha/right/2` from
  `beta/right/2`, wrote the exact region to the URL, and kept exactly one
  painted row.
- A genuinely fresh page restored and painted `alpha/right/2` in the notebook
  fixture. Changing the engine also recreated the snapshot and restored the
  same target.
- Four consecutive inline/split replacements retained one painted row. One
  subsequent click moved the pin exactly once, and the next click removed it,
  demonstrating that row replacement had not accumulated listeners.
- Collapsed fold placeholders had no pin coordinates. Both the collapsed fold
  control and the expanded fold control changed only fold state: the URL stayed
  unchanged and no `.pinned-line` appeared.
- A real pointer click on the line number inside the first revealed row
  collapsed that expanded fold edge. The event stopped before delegated
  activation, so the URL stayed unchanged and no `.pinned-line` appeared.
  Ordinary rendered rows outside the fold edge remained directly pinnable.
- A fresh `beta/left/1` URL unfolded the exact expanded fold edge, retained the
  exact URL, and completed its restoration scroll without painting
  `.pinned-line`. Collapsing and reopening that edge again retained the URL and
  kept decoration suppressed.
- A real pointer drag across left-side code set the grid's selection-side marker
  and left the existing URL and painted pin unchanged. The in-app browser input
  surface produced no native `Selection`, including when the same drag was
  repeated over ordinary title text outside the DiffGrid. LP-B1's native
  selection-content assertion therefore remains unproved by this harness rather
  than being reported as a pass.

### 2026-07-23 — implementation pass at `4151db7`

The page was served by the existing Vite session at `127.0.0.1:5173`. Browser
acceptance used a fresh application reload after every URL setup. No diagnostic
patch or replacement algorithm remained in any final run.

`make format`, `make tscheck`, and focused ESLint over every affected frontend
implementation file passed. Full `make eslint` reached the existing unrelated
strict-boolean error in unchanged `frontend/src/new/utils.ts:21`; no line-pin
file failed.

#### Static audit

LP-S1 through LP-S3 passed after contextual inspection:

- `linePins.ts` contains no Solid effect, query observer, polling loop,
  `MutationObserver`, history listener, delegated ChangeSet click listener,
  `fetchQuery`, or `selectHunk()` call;
- one `linePins()` instance is created by each `ChangeSetSnapshot` and passed
  to every rendered file;
- the URL is the only retained line identity; the retained
  `AbortController` carries cancellation only;
- the initial URL is parsed before the canonical file lane starts;
- file loading, lazy-info loading, admission, and Retry remain in the canonical
  ChangeSet lane;
- the only three `selectHunk()` call sites remain Next, Previous, and
  scroll-follow.

Matches in adjacent files were inspected rather than mechanically ignored.
The remaining effects, query observers, file-query `fetchQuery`, hunk-display
`MutationObserver`, and timers belong to existing file loading, rendering,
hunk-display, or slow-file behavior rather than a separate line-pin path.

#### Ordinary text and notebook interaction

LP-B2, LP-B5, LP-B7, and LP-B8 received complete focused unpatched coverage in
a `1280 × 720` browser viewport. LP-B1, LP-B4, and LP-B6 received the partial
coverage recorded below; their unproved branches remain explicitly open.

- Ordinary text was exercised in the existing Clojure diff preset. Direct
  left/right activation replaced the exact URL identity, painted exactly one
  complete row, and left hunk selection unchanged. Fresh restoration painted
  and scrolled to the exact row. Inline/split replacement repainted without
  changing the URL. Collapsing removed only the decoration; reopening repainted
  it. A real pointer-drag Selection check was not run, initial restoration into
  an already-collapsed FileCard was not run, and a complete
  rich→virtual→rich repaint cycle was not run.
- `scroll/_test_preset_1` was added because no existing scroll preset contained
  two notebook source regions with the same line numbers. It uses immutable
  `old.ipynb` and `new.ipynb` snapshots with backend cell keys `alpha` and
  `beta`.
- Direct notebook activation first pinned
  `alpha/right/2`, then `beta/right/2`. Each operation left exactly one painted
  complete row and wrote the exact region to the URL.
- A fresh URL for `beta/right/2` restored only the `beta` row. The current
  fold-edge-specific fresh restoration evidence is recorded in the later
  delegated-activation pass above.
- Activating the notebook fold placeholder left an existing `alpha/left/2` pin
  and its decoration unchanged.
- A fresh URL for the nonexistent `beta/right/999` showed the transient
  “Line pin unavailable” notice, removed only the exact `pin` field, preserved
  `#keep=raw%2Fvalue`, and painted no row.
- A fresh malformed `pin=%7Bbad` URL showed the same transient notice, kept the
  malformed field and unrelated raw hash field byte-for-byte, and painted no
  row.
- Fresh absent-manifest-file, duplicate-pin, empty-file, empty-region,
  empty-line, zero-line, and non-canonical-decimal URLs each showed the same
  transient notice. The genuinely absent file removed only the exact pin.
  Every malformed case remained byte-for-byte unchanged, including
  `#keep=raw%2Fvalue`.
- After the terminal-restoration lane correction and final formatting pass, a
  fresh unpatched `beta/right/1` notebook restoration unfolded the exact line,
  retained the exact URL, and showed no error surface. The later fold-edge rule
  now deliberately suppresses decoration when that line is the expanded edge.

The direct-activation checks used browser locator clicks. A locator may scroll
its target into view before dispatching the click, so those harness movements
were not counted as application restoration scrolls. Fresh URL restoration was
measured separately.

#### Ordered loading and geometry

LP-L2, LP-G1, and LP-G4 received partial unpatched browser coverage. LP-L1's
index-9 target, later index-11 explicit-load plank, and ordering trace were not
run and are not inferred from the distant-target result.

- In `many-files`, a fresh pin for
  `many-files/13-trailing-query-client/new.tsx`, right line `20`, loaded through
  the distant target, painted the exact row, and landed with the row centered
  after the finite rich-entry pass. The final document position was
  approximately `scrollY = 82640`; selected hunk identity remained file `0`,
  hunk `0`. This observation does not yet include the exact candidate call
  trace or eight-pixel settled-midpoint proof required by LP-G1.
- The first `many-files` run exposed a real canonical-lane bug: awaiting
  TanStack's observer `.promise` during an already-active initial lazy-info
  fetch never settled in this Solid client configuration, leaving the lane at
  `0/12`. The implementation now awaits
  `lazyInfo.refetch({ cancelRefetch: false })`, which joins the canonical
  observer's in-flight fetch. The fresh unpatched rerun passed.
- In `lazy-files`, a fresh pin for
  `lazy-files/02-uv-lock/new.lock`, right line `1`, loaded preceding files and
  the deferred target through the canonical lane, painted and scrolled to the
  exact row, then allowed ordinary virtualization to replace that rich body.
  The URL remained exact and the decoration disappeared while the row was
  absent, as required.
- In `mixed-file-sizes`, a fresh pin for
  `mixed-file-sizes/05-pyproject/new.toml`, right line `1`, landed with the
  target FileCard beginning at viewport position `247px`; ordinary
  virtualization had already replaced its rich row when the settled state was
  inspected. The exact URL remained. This run exposed why geometry acceptance
  must record the exact row at the forwarded final scroll and then separately
  accept permitted post-scroll virtualization; it does not by itself complete
  LP-G2.

#### Further diagnostic coverage not run

LP-B1, LP-B3, LP-B4, LP-B6, LP-B9 through LP-B13, LP-L1, LP-L3, LP-L4, and
LP-G1 through LP-G4 have not yet received complete acceptance evidence. The
reviewer must not infer them from the passing cases above. In particular,
pointer selection, initially collapsed restoration, virtual→rich repaint,
deterministic cancellation, disposal and real snapshot replacement, injected
contract failure, duplicate manifest path, ordinary Retry, exact lane priority,
and instrumented geometry still require a bounded fresh-page run.

The in-app browser emitted a visible notebook-restoration screenshot during
this task. Persistent screenshot artifacts for every case listed by Fresh final
acceptance have not yet been captured, so the screenshot requirement remains
open rather than being inferred from prose observations.

## Reviewer checklist

The reviewer must read this document together with `AGENTS.md`,
`rewrite/stages/guidance.md`, the complete line-pin specification, Stage 10,
the affected implementation, and adjacent callers.

The reviewer reports:

- missing lifecycle, cancellation, ordering, geometry, folding, collapsing,
  virtualization, notebook, side-selection, or error cases;
- cases that can pass without proving the visible contract;
- cases whose setup changes the behavior it claims to verify;
- time budgets that permit hangs or make the suite impractical;
- fixtures that are mutable, collide with another test, or fail to reproduce
  the claimed shape;
- tests reported as run without adequate evidence;
- contradictions between this document, the specification, the stage,
  `AGENTS.md`, guidance, code, and observed behavior.

The reviewer may inspect any adjacent file, run non-mutating checks, and use its
own browser page. It must not edit, format, restore, stage, or commit files.
