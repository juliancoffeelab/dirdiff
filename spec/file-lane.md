# File lane

The file lane (`changeSet/fileLane.ts`, created by `ChangeSetSnapshot` in
`changeSet/ChangeSet.tsx`) is the data lifecycle of one mounted snapshot: it
turns one manifest into the `Husk`/`Full`/`Lazy` states consumed by `FileCard`
and ends when the snapshot is disposed. Presentation after that point is
described in [`file-meat.md`](file-meat.md); the line-pin lifecycle is described
in [`navigation.md`](navigation.md).

## Snapshot identity

- A snapshot is immutable for its lifetime: `DiffParams`, the complete engine
  configuration, the manifest, its `snapshot_id`, and the manifest-order file
  list. The engine is not part of manifest identity.
- A change of engine configuration (including any Git option) replaces the lane
  without refetching the manifest or replacing the review state. Unrelated
  preference changes must not replace an equal configuration. The manifest,
  Snapshot and `ReviewProvider` stay mounted across the replacement.
- File rendering takes a complete `EngineConfig`, and the whole value is part
  of query identity. `word_boundary.regex` travels only under mode `custom`, so
  other modes share one query identity whatever pattern is stored.
- File indexes are depth-first manifest indexes, stable for the snapshot, and
  shared by the lane, `FileCard`, navigation, and FileTree. Duplicate file
  identities are an error.
- Inactive Tabs keep their Tab state but mount no snapshot.

## Backend data

- One canonical File query represents one manifest entry in one snapshot under
  one complete engine configuration. `FileCard` never observes TanStack Query
  and never performs an HTTP operation.
- File queries have no observers; the lane runs them imperatively. A settled
  canonical query is garbage-collected immediately (`gcTime` 0), so the
  per-file payload slot is the sole surviving reference and a `success` view
  must never fetch again. While a fetch or prefetch is in flight the canonical
  cache entry still deduplicates joiners.
- Each manifest position has one replaceable `FileQueryView` signal (swapped
  wholesale, not merged like a Solid store write) plus a payload slot that is
  always written before the view settles to `success`. The lane is the sole
  writer. Do not copy backend data into another reactive store.
- Every successful file response is checked against its manifest entry's
  canonical name. Lazy-info must describe exactly the manifest entries marked
  lazy, once each, with a non-null lazy reason.
- An ordinary lazy-info failure is presented as an error `Lazy` on every
  manifest-lazy file, since none can obtain its deferred metadata.
- Backend state and render admission are separate. Query success makes the
  state `Full` immediately; until the lane admits the file, its card shows the
  header and statistics but not the body. An expanded nonzero-hunk file uses one
  temporary Husk navigation target meanwhile; zero-hunk and collapsed files
  already have permanent targets.

## Loading order

- Automatic loading walks non-lazy manifest files sequentially. Admission is
  strictly sequential in manifest order and is the deliberate backpressure
  boundary for the expensive body; it yields to the browser before admitting.
- While one file is processed, a fixed bounded number of upcoming automatic
  fetches overlap it, in manifest order. Only engines measured to tolerate
  concurrent backend renders prefetch (`isHeavyEngine` false); heavy engines
  stay strictly serial.
- A prefetch records its payload or failure on the file's view. Prefetches are
  cancelled only by stopping the whole lane, which suppresses further view
  writes. A file whose recorded attempt already failed is not fetched again by
  the automatic pass.
- An ordinary failure is left on that file's view and the lane proceeds.
- Lazy manifest entries are skipped by automatic loading; their `LazyFile`
  plank is the explicit way to enter them.

## Explicit loads and Retry

- Lazy plank loads and `RetryButton` enqueue a manifest index in the same lane;
  `FileCard` never fetches and no second query definition exists. Duplicate
  queued entries, and files already successful or active, are ignored.
- Without a pending line target, explicit work runs between automatic files. A
  pending line target takes priority over explicit selections and later
  automatic files.
- The timeout policy belongs to the attempt, not to identity: automatic and
  lazy-plank loads are bounded; `RetryButton` and retrying a failed line-pin
  target are unbounded. It is not part of any query key or backend parameter.
- When an explicit or line-target load succeeds, the lane notifies the host
  before admission; the host expands the file unless the user collapsed it
  meanwhile. Expansion stays host-owned; the lane never touches it. The
  notification must not enqueue or stop the lane.

## Line-pin priority

- The host resolves the URL pin to one manifest index and gives the lane only
  that index and a restoration gate; URL parsing, identity, and toasts stay
  with the host.
- A pending target is loaded in manifest order like any file, even if the
  manifest marks it lazy. When the manifest has any lazy file, the lane awaits
  the snapshot's single lazy-info query before scheduling the target, including
  a non-lazy target.
- After the target is fetched and admitted, restoration runs, and later file
  loading stays blocked until it finishes.
- A target-file failure leaves the file an error `LazyFile` and the pin
  dormant; a later Retry reuses the same target and lane. File failure does not
  alter the URL identity.

## Failures

- Ordinary file failures are local: the file becomes an error `LazyFile`, the
  error is visibly reported, later files continue. Automatic failures still
  advance progress.
- Unexpected lane failures are not converted into a file state. The lane writes
  them to its error signal, which `ChangeSetSnapshot` throws into the nearest
  error boundary. An unexpected restoration failure stops the lane before it is
  rethrown.
- An unknown `snapshot_id` has no special recovery path.
- Reload stops the lane and awaits it, resets file expansion, then refetches
  the manifest.

## Disposal

- `stop()` is idempotent. It synchronously prevents new work, rejects further
  enqueueing, aborts active line-pin restoration, cancels the active and
  prefetched queries, and returns one Promise to every caller.
- Nothing from a disposed snapshot may later write DOM, report progress, or
  scroll.
