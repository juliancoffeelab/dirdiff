## 68. Line pins

Line pins preserve one exact backend line in the URL and restore it without changing hunk selection. They are separate from hunk navigation, scroll-follow, FileTree navigation, browser side selection, file loading, and row decoration.

### 68.1 Identity

The URL hash field `pin` contains one JSON object:

```ts
type LinePinTarget = {
  file: string;
  region: string | null;
  side: "left" | "right";
  line: string;
};
```

`file` is the canonical ChangeSet display path. Ordinary text uses `region: null`; notebook source uses the backend `cell_key`. `side` distinguishes the old and new sides. `line` is the exact positive backend line number serialized as canonical decimal text.

Every field is required. The hash may contain at most one `pin` field.

Line-pin identity exists only in the URL. FileCard, DiffGrid, Navigation, Solid state, and DOM decoration do not retain another authoritative copy.

Each `ChangeSetSnapshot` creates one LinePins instance and passes that same instance to its DiffGrids.

The LinePins module exports:

```ts
type ParsedLinePin =
  | { state: "none" }
  | { state: "invalid" }
  | { state: "valid"; target: LinePinTarget };

type LinePinToggleResult = "pinned" | "unpinned";

type LinePinRestoration =
  | { state: "complete" }
  | { state: "missing" }
  | { state: "stopped" };

type LinePins = {
  parseUrl(): ParsedLinePin;

  toggleUrlState(target: LinePinTarget): LinePinToggleResult;

  restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration>;
};

function linePins(): LinePins;
```

`linePins()` is called exactly once during `ChangeSetSnapshot` setup beneath the
existing Navigation and Toast providers. It obtains those two required scoped
interfaces and returns the one retained LinePins instance for that snapshot.

There is no `start()`, `stop()`, `current()`, generic `toggle()`, mounted LinePins component, `ChangeSet.onTarget`, or callback through which LinePins submits a target to ChangeSet.

`LinePins.parseUrl()` reads and validates the current URL. It does not start loading, mutate state, inspect DOM, decorate rows, or scroll.

`LinePins.toggleUrlState(target)`:

1. aborts the active asynchronous restoration, if one exists;
2. compares `target` with the valid current URL target;
3. removes the `pin` field and returns `"unpinned"` when they are equal;
4. otherwise replaces the `pin` field with `target` and returns `"pinned"`.

The LinePins instance retains the active restoration’s `AbortController`, because the controller is what can call `abort()`. The controller and its signal are browser-work state, not another line-pin identity.

`toggleUrlState()` changes only the hash through `history.replaceState`. It preserves the pathname, query string, and every unrelated hash field. It does not create a browser-history entry, emit a synthetic DOM event, inspect rendered rows, decorate anything, or start restoration.

Malformed identity is not accepted or repaired. It produces one two-second “Line pin unavailable” Toast and no loading, decoration, or scrolling.

### 68.2 Direct line activation and decoration

Every pinnable line-number element writes:

```text
data-line-pin-side
data-line-pin-line
```

These are the only line-local coordinates. The enclosing DiffGrid retains its
typed file name and nullable notebook region and supplies both when direct
activation constructs the complete `LinePinTarget`. Ordinary text therefore
does not encode a nonexistent region in DOM, and notebook source does not
duplicate its `cell_key` on every line.

Fold placeholders, duplicate-suppressed inline line numbers, and absent sides are not pinnable.

DiffGrid installs one delegated click listener on its persistent row root and
removes it on cleanup. Explicit row and fold replacement therefore requires no
per-line listener, bound marker, or rebinding pass. The listener combines the
clicked line's side and line with the DiffGrid's file and region, resolves the
exact rendered row, and calls:

```ts
const result = linePins.toggleUrlState(target);

changeSetRoot.querySelector(".pinned-line")?.classList.remove("pinned-line");

if (result === "pinned") {
  row.classList.add("pinned-line");
}
```

Direct activation does not call `LinePins.parseUrl()`, ChangeSet, or Navigation after changing the URL. It does not scroll.

DiffGrid owns all line-pin painting.

The complete ordinary rendered row containing the current pin carries
`.pinned-line`. An expanded fold edge remains resolvable but is not painted.
The URL remains the identity; `.pinned-line` is only its current visible
decoration.

Every explicit DiffGrid operation that creates or replaces rows reads
`LinePins.parseUrl()` and paints the matching ordinary row when it exists. An
expanded fold edge is deliberately not painted. This includes:

- initial rich rendering;
- explicit fold expansion;
- rich rendering after virtualization;
- inline/split replacement;
- notebook source-region rendering.

There is no MutationObserver, decoration signal, revision counter, row-registry notification, delegated ChangeSet click listener, history listener, or retry mechanism for decoration.

Collapsing a file or directory, folding lines, or replacing rich content with virtual content never changes the URL pin. Decoration naturally disappears while the exact row is not rendered.

When the row is explicitly rendered again as an ordinary row, DiffGrid reads
the current URL and restores the decoration without scrolling. An expanded fold
edge retains its backend coordinates so preparation and Navigation can resolve
and scroll to the exact line, but DiffGrid never adds `.pinned-line` to that
edge. The URL remains unchanged, and decoration may return only when later
explicit row construction renders the coordinate as an ordinary row.

A pinned FullFile may become virtual according to the ordinary virtualization policy. Line-pin decoration must not prevent virtualization.

Fold edges are not pinnable. Clicking a collapsed fold edge expands it, and
clicking the complete first revealed row that represents the expanded fold edge
collapses it. Neither operation reaches direct line-pin activation, changes the
URL, or paints `.pinned-line`, even though the expanded edge displays backend
line numbers. Other ordinary rendered rows inside the expanded range remain
pinnable. URL restoration may still resolve and scroll to an expanded fold
edge, but it does not decorate that edge. Explicit fold-edge pinning remains a
separate postponed interaction.

### 68.3 ChangeSet and the existing file lane

ChangeSet calls `LinePins.parseUrl()` before the initial file lane starts. There is no `hashchange`, history, or other URL listener.

The initial parse gives the lane the pin target and therefore the manifest index through which normal sequential loading must proceed.

ChangeSet owns the active line target and its manifest index inside the existing immutable-snapshot file lane. These are browser-work values, not another line-pin identity. The URL remains authoritative.

ChangeSet resolves a valid target path against the immutable manifest:

- exactly one match supplies its manifest index;
- no match is `missing`;
- multiple matches are a manifest-contract failure and throw visibly.

There is no second line-pin loading loop.

At every iteration, the existing file lane considers both its ordinary work and the current line target. For a target that has not reached a ready FullFile, the same lane:

1. waits for canonical lazy-info when the manifest contains lazy files;
2. preserves strict manifest ordering through the target index;
3. loads every ordinary preceding file through the existing sequence;
4. explicitly loads a deferred LazyFile when it is the target;
5. admits a successful FullFile through the existing rendering backpressure;
6. continues using the canonical file query and FileCard.

While that target has not yet succeeded or failed, its ordered work takes
priority over newly selected LazyFiles. The lane does not load files beyond the
target or service a later explicit selection until the target is admitted and
restoration completes.

No line-pin code calls `fetchQuery`, creates another query observer, bypasses the file lane, or reorders preceding manifest files.

When the target FullFile is admitted, the lane calls and awaits:

```ts
await linePins.restore(target, fileIndex, changeSetAbortController.signal);
```

`LinePins.restore()` verifies the URL immediately before calling Navigation. From then until the final scroll, `toggleUrlState()` cancels the retained `AbortController`, and Navigation checks that abort signal immediately before scrolling. A target replaced or removed through `toggleUrlState()` while the file lane was working returns `stopped` without scrolling, painting, showing a Toast, or changing the newer URL.

`restore()` aborts any older active restoration, creates and retains a new `AbortController`, and combines its lifetime with `changeSetAbortSignal`. `toggleUrlState()` aborts that retained controller directly. ChangeSet disposal aborts `changeSetAbortSignal`. No callback from DiffGrid or ChangeSet is required.

The lane does not start later work until that restoration returns. Browser yielding, rendering, preparation, layout calculation, and final scrolling may complete while the lane is blocked.

A slow pin may scroll several seconds after it was parsed. Manual user scrolling does not cancel it. Direct line activation, replacement through `toggleUrlState()`, and ChangeSet disposal do cancel it.

A direct DiffGrid activation does not start restoration because the clicked line is already rendered and DiffGrid has already painted it.

### 68.4 File failure and Retry

A file-fetch failure is orthogonal to the line pin.

The ordinary file machinery:

- presents the error LazyFile;
- presents its ordinary error Toast;
- allows later file work to continue;
- retains the line target and URL unchanged.

There is no `failed` line-pin result.

The pin does not call Retry, and Retry does not contain line-pin-specific behavior.

If the user later retries the file successfully, the ordinary file lane observes the canonical file becoming ready, admits it normally, and completes the still-current line target.

A failed file must not cause an immediate unbounded line-pin retry loop. The target waits for the ordinary explicit Retry action.

An ordinary lazy-info failure follows the same waiting rule. The affected
target remains dormant while ordinary lane work may continue. Its existing
LazyFile RetryButton submits the explicit file load; successful loading then
admits the target and resumes restoration.

### 68.5 File and line preparation

The existing FullFile DOM interface gains one operation:

```ts
type PreparedLine =
  | { state: "ready"; row: HTMLElement }
  | { state: "missing" }
  | { state: "stopped" };

type EnrichableFileCard = HTMLElement & {
  intersectsRichEntryZone(viewportTop: number): boolean;
  waitToEnrich_impl(): Promise<void>;

  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};
```

There is no separate line-preparation FileCard interface.

`prepareLine_impl()` receives semantic coordinates, not an HTMLElement supplied by the caller.

It performs the required file-local preparation:

1. verifies that `target.file` belongs to this FileCard;
2. expands a collapsed FileCard;
3. materializes a virtual FullFile through its existing rich-render operation;
4. selects the exact ordinary or notebook DiffGrid by `target.region`;
5. asks that DiffGrid to unfold the exact requested line when necessary;
6. returns the unique rendered row.

It does not scroll, parse or modify the URL, select a hunk, fetch a file, or create another loading path.

DiffGrid’s preparation operation distinguishes:

- `ready`: the exact line exists and has one unique rendered row;
- `missing`: the exact line no longer exists in the current complete file;
- `stopped`: the operation was aborted or its FileCard was disposed.

A file becoming shorter is an ordinary `missing` result.

Malformed row identity, duplicate matching rows, or structurally inconsistent DiffGrid DOM throws visibly. Those failures are not converted into `missing`.

Folding or collapsing does not remove the URL. Restoration is permitted to expand the target FileCard, enrich it, and unfold its exact line.

### 68.6 Navigation

LinePins calls Navigation through one coordinate-bearing operation:

```ts
type LineNavigationCommand = {
  kind: "line";
  fileIndex: number;
  target: LinePinTarget;
  abortSignal: AbortSignal;
};

type NavigationResult =
  | { state: "complete" }
  | { state: "missing" }
  | { state: "stopped" };
```

Navigation finds the exact FileCard by `fileIndex` and calls:

```ts
await fileCard.prepareLine_impl(target, abortSignal);
```

Navigation does not parse or mutate the URL, fetch files, retain pin identity, paint rows, select hunks, or create another preparation path.

For a ready target, Navigation:

1. obtains the prepared row;
2. calculates its hypothetical centered viewport;
3. finds expanded virtual FullFiles whose rich-entry zones intersect that viewport;
4. enriches each such file at most once during this operation;
5. recalculates the target row and centered viewport after each synchronous layout change;
6. stops scroll-follow immediately before scrolling;
7. performs exactly one final centered scroll.

It does not poll, retry through timers, repeatedly scroll, or select an approximate row.

`missing` means the exact line does not exist in the current complete target file.

`stopped` means the operation was aborted or Navigation/FileCard was disposed before its final action.

Cancellation never masquerades as `missing`.

Line navigation never calls `selectHunk()`.

### 68.7 Missing targets and errors

When the manifest does not contain the target file, ChangeSet:

1. verifies that the URL still contains the same target;
2. shows one two-second “Line pin unavailable” Toast;
3. calls `LinePins.toggleUrlState(target)` to remove it.

When Navigation reports `missing`, LinePins performs the same three actions.

If `toggleUrlState()` replaces or removes the target while an asynchronous operation is running, the old operation is `stopped`. It does not remove the newer URL target or show a Toast.

A file-fetch failure leaves the URL pin unchanged and uses only ordinary file-error presentation.

Unexpected parsing-independent contract failures reject to one persistent error Toast.

`LinePins.restore()` does not Toast an unexpected rejection. The existing
file-lane failure path routes it into the nearest `ChangeSetSnapshot`
ErrorBoundary, which presents the local damage and produces the one persistent
Toast. Navigation, LinePins, and the lane do not independently Toast the same
failure.

There is no fallback target, nearest-line selection, silent recovery, polling loop, retry timer, MutationObserver, revision watcher, capture-phase click spy, collapse interception, history listener, or rule preventing a pinned file from becoming virtual.

### 68.8 Required invariants

1. The URL is the only authoritative line-pin identity.
2. Line pins never call `selectHunk()` and never change selected hunk identity.
3. ChangeSet calls `LinePins.parseUrl()`; LinePins never reports targets through `ChangeSet.onTarget`.
4. ChangeSet reads the URL before the initial file lane and does not listen to history changes.
5. Direct line activation calls `LinePins.toggleUrlState()` and does not scroll.
6. `toggleUrlState()` aborts the LinePins instance’s active restoration without a ChangeSet callback.
7. DiffGrid owns all `.pinned-line` decoration.
8. FileCard, Navigation, and LinePins do not paint line-pin decoration.
9. Folding, collapsing, and virtualization never clear the URL pin.
10. Target file loading remains inside the canonical single file lane.
11. File fetches before and through the target preserve manifest order.
12. A file-fetch failure is orthogonal to the pin and does not complete or remove it.
13. A ready target blocks later file-lane work until restoration finishes.
14. Restoration performs exactly one final programmatic scroll after required layout work.
15. Manual scrolling does not cancel a pending line pin.
16. Direct activation, replacement or removal through `toggleUrlState()`, or a disposed ChangeSet prevents every later scroll from the older operation.
17. Missing files and genuinely vanished lines produce the transient Toast and remove that exact URL target.
18. Stopped operations do not mutate URL, paint, Toast, or scroll.
19. No line-pin behavior uses a Solid effect, polling timer, retry timer, MutationObserver, revision watcher, capture-phase click spy, history listener, or hidden fallback.
20. Every pinnable DOM line carries exact side and line coordinates; its enclosing DiffGrid supplies the exact file and nullable region.
21. Notebook region identity is one non-empty backend `cell_key` unique within its file.
22. Direct activation uses one delegated DiffGrid-root listener; line DOM carries no listener-bound marker.
