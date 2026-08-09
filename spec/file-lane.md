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
3. one ordered collection of file queries, keyed by engine, `snapshot_id`, and
   the manifest entry's left/right path locator.

The file-query collection has exactly the same order and length as the flattened
manifest. Its observers are disabled for automatic fetching. The lane executes
those same canonical query definitions through TanStack Query, so observation
and imperative loading refer to one cache entry per file.

Backend results remain in TanStack Query. The snapshot derives backend states
from the current observer results instead of copying backend data into a second
store:

| Query and manifest state | File state |
| --- | --- |
| file request is active | `Husk`, fetching |
| file query succeeded | `Full` |
| file query failed ordinarily | error `Lazy` |
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
the manifest to the end. At most one file query is active in the lane.

For each automatic file, the lane:

1. executes its canonical file query;
2. leaves an ordinary failure on that query and proceeds to the next file;
3. yields to the browser after a successful fetch;
4. admits that file for expensive rendering;
5. proceeds to the next manifest position.

Fetch order and render admission therefore have the same manifest order. A
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

Ordinary file failures are local. The failed query becomes an error `LazyFile`,
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

Disposal removes the snapshot’s query observers, file states, admission state,
progress, and rendered files together. Nothing from the disposed snapshot may
later write DOM, report progress, or scroll.

## Interfaces to file presentation

For each manifest index, `ChangeSet` supplies `FileCard` with:

- one reactive `Husk`, `Full`, or `Lazy` state derived from canonical queries;
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
- Automatic file fetches and admissions are sequential in manifest order.
- One canonical file query represents one manifest entry in one snapshot.
- Lazy planks, Retry, and line pins all use the same lane.
- Admission is separate from fetch success and yields before mounting the
  expensive `FileBody`.
- One file failure does not stop later files.
- An unknown `snapshot_id` receives no special recovery path.
- Disposed snapshots perform no later loading, presentation, or navigation
  work.
