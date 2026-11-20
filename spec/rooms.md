# Rooms and captured file state

## Purpose

A Room contains Snapshots and review Threads. One Tab's law of correspondence
selects the Room. `RoomLord` and `Room` are the only application boundary used
by HTTP and rendering; Snapshot capture, publication, and relational records do
not cross it. `room_lord.py` reaches the `dirdiff.db` persistence facade only to
implement those two classes.

The HUD knows only the opaque `snapshot_id` returned by `/api/manifest`. It does
not know Room identity, Room lifetime, or storage layout.

## Contract

- `corresponding_room` applies the law of correspondence only to find or create
  the Room. It separately uses the supplied capture inputs to capture or reuse
  the current state. Snapshot identity never participates in correspondence.
- `find_room` is for follow-up endpoints: a Snapshot key is globally unique, so
  it finds the containing Room without a correspondence law or live source state.
- A Room never stores or implies a selected Snapshot key. Every File and Thread
  operation names its Snapshot explicitly.
- Neither Room nor RoomLord receives a repository/catalog service or live source
  root, resolves a revision, or constructs a source. `Room.capture_context()`
  returns the persisted selection and Mark id (absent for Preset Rooms); source
  setup interprets them outside Room, and passing them back to
  `corresponding_room` selects the same Room.
- A File with a persisted `capture_error` carries the exact reason in
  `FileMeta.capture_error`; its captured bytes are generated diagnostics.
  Callers must check it before decoding or composing the sides.
- Snapshot-scoped `threads` results bind every `Thread` to the supplied
  Snapshot and require its placement there. Room-scoped results include every
  discussion whose origin belongs to the Room; when the supplied Snapshot
  predates a Thread, the handle is bound to its origin Snapshot. Comment and
  lifecycle operations use the handle's bound Snapshot. Room neither
  interprets private source coordinates nor implements discussion state
  transitions.
- `FileMeta.untracked` is the inverse of the database `tracked` column; the
  inversion happens at that boundary.

## Law of correspondence

The key is canonical JSON built by RoomLord from the selection; Room does not
interpret it. SQLite compares it for equality only, and partial unique indexes
keep marked and Mark-less Room identities distinct despite nullable uniqueness.

- Head: `{"commit": baseline}`. Git setup supplies the HEAD commit id, or the
  empty-tree hash for unborn HEAD. The baseline stays frozen after HEAD advances
  (browser continuation and agent descriptors alike); a new Head capture from
  the browser selects a new Room.
- Compare Refs: `{"left": identity, "right": identity}`. Commit-backed refs are
  frozen to commit ids; `index` and `worktree` stay as live ephemeral sides.
  Snapshot labels use these canonical values, not the request's ref spelling.
- Branch Review: `{"base": identity, "review": identity}`. Opaque non-null JSON
  to Room; Git setup resolves the branch pair freshly for each capture.
- Pull Request: the URL as a JSON string. The merge-base and head commits used
  for capture do not identify the Room and cannot replace, extend, or verify
  the URL key.
- Preset: `{"catalog": id, "subset": id}` in one Mark-less Room.

## Captured state

- Per-File replaced or modified line counts belong to diff engines, never to
  Snapshot metadata. Aggregate added/removed counts come from the source and are
  both present or both absent; untracked worktree Files stay outside Git's
  tracked aggregate.
- RoomLord never chooses acquisition order from entry kind, Git modes, storage
  ids, or link facts. The source holds one complete observation per capture and
  hides exact-version reads, filesystem access, LFS expansion, gitlinks, and link
  traversal. Observation is sequential across Files and promises no atomic
  worktree view and no retries.
- An expected outer failure is contained to its side: capture keeps the File
  metadata, writes machine-generated diagnostic content, and persists the exact
  error. Sibling Files stay available. Unexpected programming failures abort
  capture.
- Snapshot equality covers: left/right path presence; entry kind and executable
  bit per present side; each side's opaque source content identity (data and
  link facts); VCS tracking and change classification; the lazy override and the
  complete source policy text supplying it. It excludes source order, labels,
  aggregate counts, directory-tree output, and renderer output. Canonical path
  sorting defines the hash input only.
- Sources namespace their side identities so incompatible materialization rules
  cannot silently identify equal data; Room hashes them without treating them as
  storage addresses. Bump `_SNAPSHOT_HASH_DOMAIN` when the hashed facts change;
  existing Snapshots stay readable and the new encoding selects its own retained
  states.
- A source may establish content identity without loading bytes, so an
  already-captured successful Snapshot stays independent of source availability.
  If acquisition finds a side unavailable and changes an identity, equality is
  rechecked before publication.
- Per-side content digests authenticate each published raw side; link metadata
  and optional reached data are authenticated separately. Reusing an equal
  retained Snapshot re-verifies every referenced raw side, link metadata, and
  link target against its digest, so missing or modified content fails the
  manifest instead of advertising an unreadable Snapshot.

## Relational state

Absence is represented by row absence: an absent File side, a symlink record,
a lazy reason, and its content have no row. A symlink row belongs to one present
side; its target path and hash are present together, only after a successful
walk. Chain hops, the terminal diagnosis, and the final repository target stay
in the authenticated metadata, not relationally. No relation stores manifest
ordering, directory trees, display names, rendered rows, or per-File line
counts. The Tab lives on the Room relation only, and `Room.meta()` combines it
with Snapshot facts. The lazy-reason content relation retains the exact preset
metadata input that participated in identity without adding it to Room File
metadata.

## Publication

- A link side has private `left-link.json`/`right-link.json` and, after
  successful resolution, `left-target`/`right-target` beside it. Final absolute
  paths are fixed once at publication and stored; later reads never probe
  sibling filenames to decide whether a side is a link.
- Under the database-wide advisory lock, capture rechecks for equal state,
  renames the complete staging directory to `snapshots/<snapshot_id>`, and
  inserts all rows in one transaction. A Snapshot is visible only through
  committed rows referencing a complete immutable directory. If derivation or
  publication fails after the rename, the directory is moved back to staging
  and removed.
- Thread placement derivation reads the new Snapshot's captured bytes, so it
  runs after the rename and before the transaction; placements are committed
  with the Snapshot. A genuinely new Snapshot derives only the placements it is
  missing; reuse of an equal Snapshot derives none. Capture callers never select
  a predecessor or supply ancestry. Ordinary Thread reads never create or
  repair placements; creating a Thread inserts only its origin row and first
  Comment action in one transaction.
- Captured File rows retain absolute paths, so changing `--store-path` affects
  only new publication and does not relocate existing Snapshots. Database and
  store paths inside a reviewed repository are rejected before storage
  directories are created.

## HTTP flow

- `/api/pull-request/prepare` is the whole Pull Request preparation boundary;
  manifest only receives its results. It fetches the forge-recorded target and
  head objects by SHA and computes the merge base locally. Each observed commit
  is retained under `refs/dirdiff/prepared/<sha>` in the marked repository so
  restored review URLs stay capturable after Git garbage collection; these refs
  never expire and are independent of Room and Snapshot identity. It never
  selects a newer revision from updated tracking refs. Missing or non-directory
  marks are logged and skipped while searching.
- `/api/manifest` validates the per-Tab `selection` (422 on malformed fields),
  prepares the source, hands it to `corresponding_room`, then reads `meta` and
  `manifested` from the returned Room. For Pull Request it uses the URL only for
  correspondence and the two commits only for capture; it never parses the URL,
  contacts a forge, or fetches refs.
- `/api/lazy-info` and `/api/file-diff` take a `snapshot_id`, call `find_room`,
  and read captured state only; they never reload Git, index, worktree, or
  preset contents. Persisted capture errors do not block lazy-info and follow
  `/api/file-diff`'s `DirdiffError` path.
- Agent routes capture through the same `corresponding_room` operation as the
  browser manifest: the same descriptor always reaches the same Room, an
  unchanged capture returns the same Snapshot id, and Preset Tabs are rejected
  because a preset Room has no Mark. No agent route introduces another Room
  identity or review-handle entity, and none recovers a Room from a Snapshot to
  recapture it; a later round captures the descriptor again. An agent keeps its
  Profile id and latest activity id and does not register another Profile.
- A Head Room is identified by its frozen baseline commit, which the agent's
  Head descriptor carries (`baseline`, required everywhere). The browser's Head
  selection names no baseline and always freezes current HEAD, so `onboard`
  fills `baseline` from the current HEAD (empty-tree id when unborn).
- The agent capture response returns the existing Snapshot directory already
  published by ordinary capture; it creates no other representation. Immediate
  children are opaque captured File ids; the exact `left` and `right` files are
  the public review sides. Private `*-link.json` and `*-target` files must not
  be enumerated as changed Files or addressed by findings. File ids and side
  names encode no repository mapping; the `capture` manifest (see
  `spec/reviews.md`) maps repository paths to sides. The tree is read-only.
  Changes to publication layout, side naming, byte meaning, or path
  interpretation must update `spec/reviews.md` and the `review-patch`,
  `round-review`, and `babysit-patch` skills together, so capture changes
  cannot silently break the agent workflow.
- Repository-mark removal deactivates the Mark instead of deleting its row, so
  Room and Snapshot identity survive; marking the same path again reactivates
  the same Mark id.
