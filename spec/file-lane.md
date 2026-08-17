# File lane

## Purpose

The file lane is the data lifecycle of one mounted `ChangeSetSnapshot`. It starts
with one manifest and its opaque `snapshot_id`, produces the canonical file
states consumed by `FileCard`, and ends when that snapshot is disposed.

The lane lives in `ChangeSet.tsx`. Backend types, query keys, and HTTP operations
come from `api/api.ts`. File presentation begins only after the lane has produced
a `Husk`, `Full`, or `Lazy` state; that lifecycle is described in
[`file-meat.md`](file-meat.md).

## Snapshot creation

An active Tab renders `ChangeSetContent`, which observes one manifest for the
complete selected `DiffParams` value. The diff engine is supplied separately and
does not participate in manifest identity. A successful manifest creates a keyed
`ChangeSetSnapshot`.

The snapshot treats these values as immutable for its entire lifetime:

- `DiffParams`;
- the file-rendering engine;
- the manifest tree and manifest statistics;
- the manifest `snapshot_id`;
- the manifest-order list of files.

The manifest tree is flattened depth-first. That order is the file index used by
the lane, `FileCard`, navigation, and FileTree. Duplicate file identities are an
error.

The snapshot immediately creates one stable `FileCard` position for every
manifest entry. Files do not wait for their backend diff before they appear in
the component tree.

Inactive Tabs keep their Tab state but do not retain a mounted
`ChangeSetSnapshot`, query observers for its opaque `snapshot_id`, or rendered
files.

## Canonical backend data

The snapshot has three related query layers:

1. one manifest query, keyed by `DiffParams`;
2. one lazy-info query, keyed by `snapshot_id`, only when the manifest contains
   lazy files;
3. one ordered collection of canonical file-query definitions, keyed by engine,
   `snapshot_id`, and the manifest entry's left/right path locator, executed
   only imperatively by the lane.

File queries have no observers. TanStack's `createQueries` store deep-unwraps
every query's data on every update, which walks all loaded rows per lane event
and froze loading quadratically. Instead the snapshot keeps one replaceable
`FileQueryView` signal per manifest position (`idle`/`fetching`/`success`/
`error`, without payloads; a signal swap replaces the whole value, unlike a
Solid store write, which merges fields) plus one plain payload slot always
written before its view settles to `success`; a prefetch settlement and the
lane's join of the same in-flight fetch may both record the identical payload.
The lane is the sole writer and records exactly the transitions it causes. With no observers a settled canonical query is
garbage-collected immediately (`gcTime` 0), so the payload slot is the sole
surviving reference and a `success` view must never fetch again; while a fetch
or prefetch is in flight, the canonical cache entry still deduplicates joiners.

The snapshot derives backend states from the view signals instead of copying
backend data into a second reactive store:

| View and manifest state | File state |
| --- | --- |
| view is `fetching` | `Husk`, fetching |
| view is `success` | `Full` |
| view is `error` | error `Lazy` |
| lazy-info failed ordinarily | error `Lazy` for every manifest-lazy file |
| manifest marks the file lazy and lazy-info is available | deferred `Lazy` |
| file has not started | `Husk`, queued |

Every successful file response is checked against its manifest entry. Lazy-info
must describe exactly the manifest entries marked lazy, once each, with a
non-null lazy reason.

The lazy-info request may run concurrently with the ordered file lane. It
provides metadata; it does not create a second file-loading path. An ordinary
lazy-info failure is presented through every manifest-lazy file because none of
those files can obtain its required deferred metadata.

Backend state and render admission are separate. Query success changes the
canonical state to `Full` immediately. Until the lane admits that file, its
stable card can show the Full header and statistics while omitting the body and
using its header or collapsed anchors for navigation. An expanded nonzero-hunk
file uses one temporary Husk target; zero-hunk and collapsed files already have
their permanent zero or coordinate-preserving skip targets.

## Strict manifest-order loading

The automatic lane loads non-lazy manifest files sequentially from the start of
the manifest to the end. One file is processed at a time; while it is
processed, the lane additionally launches a fixed bounded number of upcoming
automatic canonical file queries (in manifest order) so backend latency
overlaps processing. A prefetch settlement records its payload or failure on
the file's view (prefetches are only ever cancelled by the stop of the whole
lane, which suppresses further view writes); a file
whose recorded attempt already failed is not fetched again by the automatic
pass, and stopping the lane cancels in-flight prefetches with the active
query. Prefetching applies only to engines measured to tolerate concurrent
backend renders (dirdiff 23% and git 14% faster total load with it); heavy
engines degrade outright — difftastic measured 2.4x slower total load at
three in flight — so `isHeavyEngine` keeps them strictly serial per lane,
and an engine added later stays heavy until measured otherwise.

For each automatic file, the lane:

1. executes (or joins) its canonical file query unless its view already
   settled to `success`;
2. leaves an ordinary failure on that file's view and proceeds to the next
   file;
3. yields to the browser after a successful fetch;
4. admits that file for expensive rendering;
5. proceeds to the next manifest position.

Render admission is therefore strictly sequential in manifest order, while
fetches may overlap within the fixed bound. A
successful query can already produce a `FullFile` header and statistics while
its body remains unmounted. For an expanded nonzero-hunk file, the temporary
navigation target is still a Husk; zero-hunk and collapsed files already expose
their permanent targets. Admission is the deliberate browser backpressure
boundary for the expensive body.

Manifest entries marked lazy are skipped by ordinary automatic loading. Their
`LazyFile` plank is the explicit way to enter them into the lane.

## Explicit loads and Retry

An explicit lazy plank and a `RetryButton` enqueue the corresponding manifest
index in the same lane. They do not fetch from `FileCard` and do not create
another query definition.

Without a pending line target, explicit work runs between automatic files.
Line-target preparation and restoration have higher priority than both explicit
selections and later automatic files. Duplicate queued entries and a file that
is already successful or active are ignored.

The timeout policy belongs to the attempt:

- automatic loading and an ordinary lazy-plank load use the bounded policy;
- `RetryButton` uses the unbounded policy;
- retrying the current failed line-pin target also uses the unbounded policy.

The timeout policy is not part of file identity, query identity, or backend
parameters.

When an explicit load succeeds, the resulting `FullFile` is expanded unless the
user explicitly collapsed it while the request was active.

## Line-pin priority

The snapshot parses the URL line pin before the lane begins. A valid target is
resolved to one exact manifest index.

A pending line pin changes scheduling without creating another loader:

- the ordinary sequence loads every required non-lazy file up to the target in
  manifest order;
- the target itself is loaded even when the manifest marked it lazy;
- when the manifest contains any lazy file, the lane awaits that snapshot’s
  single lazy-info query before scheduling the target, including a non-lazy
  target;
- after the target is fetched and admitted, line-pin restoration runs;
- later file loading remains blocked until that restoration finishes.

An ordinary target-file failure leaves the file as an error `LazyFile` and the
pin dormant. A later explicit Retry reuses the same target and lane. File
failure remains local to the file; it does not remove or reinterpret the URL
identity.

The complete line-pin lifecycle, including preparation, decoration, scrolling,
and cancellation, is described in [`navigation.md`](navigation.md).

## Progress and header presentation

Lane progress counts completed automatic non-lazy attempts. A failed automatic
attempt still advances progress because the lane has finished that manifest
position.

The failure count is derived from current error `LazyFile` states. The active
file identifies whether work came from the automatic sequence, an explicit
selection, or a line target. An active file becomes slow after eight seconds.

`ChangeSet` presents this information through stable `AppHeader` targets:

- manifest file and line statistics;
- loading progress;
- the current slow-file indicator;
- the number of file failures.

The presentation moves into `AppHeader`; ownership of the underlying snapshot
data remains in `ChangeSet`.

## Failures

Ordinary file failures are local. The failed view becomes an error `LazyFile`,
its error is visibly reported, and later files continue.

Unexpected lane failures are not converted into a file state. The lane writes
them to the snapshot’s error signal, which throws into the nearest error
boundary. An unexpected line-restoration failure explicitly stops the lane
before it is rethrown; other orchestration failures become terminal when that
boundary disposes the snapshot.

An unknown `snapshot_id` is a backend-contract failure and remains visible like
any other request failure. The frontend does not classify it or replace the
manifest automatically.

An explicit reload stops the lane, disposes the keyed snapshot, resets file
expansion, and refetches the manifest. Repository or workspace reset boundaries
additionally clear client state and reconstruct it from the current URL.

## Cancellation and disposal

Stopping a snapshot is idempotent. It synchronously prevents new work, disables
explicit enqueueing, aborts active line-pin restoration, cancels the exact
active TanStack query, and exposes one Promise to every caller waiting for
shutdown.

Disposal removes the snapshot’s view signals, payload slots, file states,
admission state, progress, and rendered files together. Nothing from the disposed snapshot may
later write DOM, report progress, or scroll.

## Interfaces to file presentation

For each manifest index, `ChangeSet` supplies `FileCard` with:

- one reactive `Husk`, `Full`, or `Lazy` state derived from the view signals
  and payload slots;
- whether the fetched file has been admitted for rendering;
- the shared file expansion value;
- explicit load and Retry operations that enqueue the lane;
- manifest identity and statistics;
- hunk-display data mirrored from the mounted DOM;
- the per-snapshot `LinePins` interface.

`FileCard` never observes TanStack Query and never performs an HTTP operation.

## Lane invariants

- One mounted snapshot has one immutable `DiffParams`, engine, manifest, and
  `snapshot_id`; engine remains outside manifest and Room identity.
- File indexes are manifest indexes and remain stable for the snapshot.
- Automatic admissions are sequential in manifest order; automatic fetches are
  launched in manifest order and overlap only within one fixed bound.
- One canonical file query represents one manifest entry in one snapshot.
- Lazy planks, Retry, and line pins all use the same lane.
- Admission is separate from fetch success and yields before mounting the
  expensive `FileBody`.
- One file failure does not stop later files.
- An unknown `snapshot_id` receives no special recovery path.
- Disposed snapshots perform no later loading, presentation, or navigation
  work.
