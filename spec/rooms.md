# Rooms and captured file state

## Purpose

A Room contains Snapshots and review Threads. One Tab's law of correspondence
selects the Room. The application boundary used by HTTP and rendering is
`RoomLord` and `Room`; Snapshot capture, publication, and relational records do
not cross that boundary. `room_lord.py` reaches the separate `dirdiff.db`
persistence facade only to implement those two classes.

The HUD knows only the opaque `snapshot_id` returned by `/api/manifest`. It does
not know Room identity, Room lifetime, or storage layout.

## Public interface

`RoomLord` has three operations for selecting, capturing, and locating Rooms:

```python
corresponding_room(
    *, mark_id: int | None, selection: CaptureSelection,
    source: ChangeSourceProtocol, left_label: str, right_label: str,
) -> tuple[Room, UUID]
recapture(
    room: Room, *, source: ChangeSourceProtocol,
    left_label: str, right_label: str,
) -> UUID
find_room(snapshot_id: UUID) -> Room
```

`corresponding_room` is used by the shared explicit capture operation behind
manifest and agent review opening. It applies the Tab's law of correspondence
only to find or create the Room. It separately uses the supplied capture inputs
to capture or reuse the current state, then returns the Room and Snapshot key
separately. Snapshot identity never participates in correspondence.

`recapture` is used to continue an already selected Room. It captures or reuses
the supplied source state under that Room's original correspondence; it does not
apply a new selection law. Prepare that source from `Room.capture_context()`
outside RoomLord. For example, if HEAD advances, continuing an existing Head
Room retains its original baseline, while opening a new Head Tab through
`corresponding_room` supplies the new baseline and selects a different Room.

`find_room` is used by follow-up endpoints. A Snapshot key is globally unique,
so it finds the containing Room directly without executing a correspondence law
or reading live source state.

A Room never stores or implies a selected Snapshot key. Every File and Thread
operation names the exact state explicitly:

```python
meta(snapshot_id: UUID) -> SnapshotMeta
manifested(snapshot_id: UUID) -> Iterator[
    tuple[Optional[Path], Optional[Path], FileMeta]
]
get(
    snapshot_id: UUID,
    left: Optional[Path],
    right: Optional[Path],
) -> tuple[
    Optional[CapturedFileSide],
    Optional[CapturedFileSide],
    FileMeta,
]
threads(
    snapshot_id: UUID,
    *,
    page: int,
    limit: int,
    state: Literal["all", "open"],
    through_activity_id: int | None,
    attention: Literal["author", "reviewer"] | None = None,
    scope: Literal["snapshot", "room"],
) -> tuple[tuple[Thread, ...], int, int]
get_thread(snapshot_id: UUID, thread_id: UUID) -> Thread
thread_for_comment(snapshot_id: UUID, comment_id: UUID) -> Thread
create_thread(snapshot_id: UUID, command: CreateThread) -> Thread
```

`Room.capture_context()` returns the complete persisted `CaptureSelection` and
Mark id. Source setup interprets those values outside Room, locates the active
Mark, and constructs a new source. `RoomLord.recapture(room, source=...,
left_label=..., right_label=...)` publishes it under that Room's original
correspondence. The Room must belong to the same database and capture-store
context as RoomLord. Preset continuation remains rejected.
`Room.path_for_snapshot(snapshot_id)` returns an already published Snapshot
directory. Neither Room nor RoomLord receives a repository/catalog service or
live source root, resolves a revision, or constructs a source.

`manifested` yields repository-relative left/right Paths, including Files whose
contents could not be captured. `get` accepts one such pair and returns each
present side as a `CapturedRegularFileSide` or `CapturedSymlinkFileSide`.
Both variants carry the absolute captured Path. The symlink variant requires
authenticated `CapturedLink` facts and the regular variant cannot carry them.
Added and deleted Files use `None` for the absent side.
For a File with a persisted capture error, `get` returns that exact reason
in `FileMeta.capture_error`; rendering boundaries reject or classify it before
decoding the generated diagnostic contents.

`FileMeta.untracked` reports whether a File is untracked by the selected VCS,
matching source and manifest metadata. The database stores the inverse in its
`tracked` column; capture and Room reads invert explicitly at that boundary.

*Implementation detail: preset fixtures report `untracked=False` because VCS
tracking does not apply to them.*

File metadata also contains source classification, an explicit lazy override,
and required-with-null `capture_error`. A non-null capture error is the exact
persisted reason the File contents are unavailable. It contains no renderer
output or per-File line counts.

Snapshot-scoped `threads` results bind every `Thread` to the supplied
`snapshot_id` and require its placement there. Room-scoped results include every
discussion whose origin belongs to the Room. When the supplied Snapshot predates
a Thread and therefore has no placement, that handle is bound to its actual
origin Snapshot and reports the captured relation between its origin File and
the displayed File. Comment and lifecycle operations use the handle's bound
Snapshot. Room neither interprets private source coordinates nor implements
discussion state transitions.

`SnapshotMeta` exposes the containing Room's persisted Tab, the two captured
side labels, and the source-supplied aggregate added/removed line counts. The
Tab is not duplicated in the Snapshot relation; `Room.meta()` combines the
Room and Snapshot facts. The counts are both present or both `None`.

## Law of correspondence

`corresponding_room` selects a Room from:

- nullable Mark id;
- one discriminated capture selection — one variant per Tab law, each
  carrying exactly its required inputs, so validity concerns values rather
  than cross-Tab absence;
- an opaque correspondence key derived from that selection.

Source setup prepares the identities below; RoomLord constructs the canonical
JSON key without interpreting their storage meaning. The key encodings are:

- Head uses `{"commit": baseline}`. Git setup supplies the current HEAD commit
  id or empty-tree hash for unborn HEAD. Continuation keeps this original
  baseline even after HEAD advances; a new Head capture selects a new Room;
- Compare Refs freezes commit-backed refs to commit ids and retains `index` and
  `worktree` as explicit ephemeral sides. Snapshot labels use those same
  canonical values rather than the request's ref spelling. The key is
  `{"left": identity, "right": identity}` and continuation keeps the frozen
  identities while explicit ephemeral inputs remain live;
- Branch Review uses `{"base": identity, "review": identity}`. These non-null
  JSON values are opaque to Room; current Git setup supplies the structured
  local/remote branch pair and resolves it freshly for each capture;
- Pull Request uses the URL as a JSON string; continuation prepares fresh
  revisions for that same URL outside Room;
- each preset catalog/subset pair uses `{"catalog": id, "subset": id}` in one
  Mark-less Room.

SQLite compares the complete opaque key for equality but does not interpret its
structure. Partial unique indexes preserve the intended marked and Mark-less
Room identities despite SQLite nullable uniqueness behavior.

The values used to capture a Snapshot are separate from this law. In particular,
the Pull Request Tab captures the prepared merge-base commit on the left and the
prepared Pull Request head commit on the right. Those commits do not identify the
Room and cannot replace, extend, or verify the URL correspondence key.

## Captured state

Manifest observation asks the selected `ChangeSourceProtocol` for ordered `FileChange`
records and optional aggregate added/removed line counts. Git obtains its
counts from Git. Sources without an authoritative aggregate return both counts
absent. Additional untracked worktree Files remain outside the tracked aggregate.
Per-File replaced or modified line counts belong to diff engines, never Snapshot
metadata.

RoomLord combines semantic side properties and source-supplied content
identities with File metadata to determine Snapshot equality. It first checks
for an equal retained Snapshot. Otherwise it asks the source for paired
`LoadedFile` results, verifies their
count and exact observed File association, checks equality again if acquisition
changed a side identity, and publishes the acquired results. RoomLord never chooses
acquisition order from
entry kind, Git modes, storage ids, or link facts. The source holds one complete
observation for this capture and hides exact-version reads, filesystem access,
LFS expansion, gitlinks, and link traversal.

Source setup supplies the reached-link size limit at source construction.
The source returns exact outer bytes and any `CapturedLink` facts; RoomLord persists
those facts beside the raw link data. Targets larger than 1 MiB stop with a
visible diagnosis. Other File contents remain unclassified. An expected outer
failure is contained to its side: capture retains File metadata, writes the
established machine-generated diagnostic content, and persists its exact error.
Successful siblings remain available. Unexpected programming failures abort
capture.

Snapshot equality includes:

- left and right repository-path presence;
- semantic entry kind and executable permission for each present side;
- each side's opaque source content identity, covering its data and link facts;
- VCS tracking and source change classification;
- explicit lazy override;
- complete source policy text supplying that override.

Source order, human labels, aggregate counts, directory-tree output, and
renderer output do not participate in equality. Canonical path sorting defines
the hash input set only. Sources namespace their side identity encodings so
incompatible materialization rules cannot silently identify equal data. Room
combines these side identities under the Snapshot hash domain; it does not
interpret them as storage addresses.

A source can establish ordinary immutable content identity without loading its
bytes, allowing an already captured successful Snapshot to remain independent
of source availability. If acquisition for a new Snapshot finds an unavailable
side, that result supplies a failure identity. Room recomputes equality before
publication. A subsequent capture uses a new source and performs normal lookup;
it can reuse successful retained state or acquire the formerly unavailable data.
Mutable sides and links acquired during observation are returned unchanged by
later load calls, even if live input changes afterward. Observation itself is
sequential across Files and does not promise an atomic worktree view or retries.

SHA-256 over the semantic token set identifies equal captured state inside one
Room. Per-side content digests authenticate each published raw side; symbolic
link relations separately authenticate metadata and optional reached data.
Reusing equal retained state verifies those published artifacts. Advancing the
Snapshot hash domain for an incompatible equality encoding leaves existing
Snapshots readable; the new encoding selects its own retained states.

## Relational state

The normalized relations are:

```text
room
  id, mark_id, tab, backend_key

snapshot
  id, room_id, content_hash

snapshot_meta
  snapshot_id, left_label, right_label, added_lines, removed_lines

snapshot_file
  id, snapshot_id, absolute capture-directory path, tracked, change_type, error

snapshot_file_left
  file_id, repository_path, content_hash

snapshot_file_right
  file_id, repository_path, content_hash

snapshot_file_symlink
  file_id, side, absolute metadata_path, metadata_hash,
  nullable absolute target_capture_path, nullable target_hash

snapshot_file_lazy_reason
  file_id, reason

snapshot_file_lazy_reason_content
  file_id, complete metadata content

user_profile
  id, username

agent_profile
  profile_id, agent_uuid

review_thread
  thread_id, origin_snapshot_id

review_thread_placement
  thread_id, snapshot_id, nullable snapshot_file_id, immutable placement facts

review_action
  activity_id, operation_id, thread_id, snapshot_id, sequence,
  non-null profile_id author, authored action fields, status_after,
  attention_after
```

The two Snapshot line-count columns are nullable together. A File's capture
error is nullable because successful capture has no error. Optional File sides,
symbolic-link identity, lazy reasons, and lazy-reason source content are
represented by row absence. A symbolic-link row belongs to one present
left/right side. It stores the exact physical sidecar paths and their digests;
the target path and hash are present together only after a successful walk.
Chain hops, the terminal diagnosis, and the final repository target path remain
in the authenticated metadata rather than being duplicated relationally.

The lazy-reason content relation retains the exact preset metadata input that
participated in identity without adding that content to source path or Room
File metadata. No relation stores manifest ordering, directory trees, display
names, rendered rows, or per-File line counts.

## Publication

Capture writes a process-unique staging directory. Every present side is the
exact `left` or `right` bytes. A link side additionally has private
`left-link.json`/`right-link.json` metadata and, after successful resolution,
private `left-target`/`right-target` bytes beside it. Publication constructs
their final absolute paths once, hashes their exact bytes, and inserts one
`snapshot_file_symlink` row for that side. Later reads use those stored paths;
they do not probe sibling filenames to decide whether a side is a link. Under
the database-wide
advisory lock, it checks for equal captured state, renames a new complete directory under
`snapshots/<snapshot_id>`, and publishes all relational rows in one database
transaction.

A Snapshot becomes visible only through committed relational rows referencing
its complete immutable directory. A crash after rename and before transaction
commit may leave an unreferenced directory, but cannot expose partial contents.
No collector exists in this patch; a future garbage collector may remove those
directories.

A genuinely new Snapshot derives only the Room Thread placements missing for
that new Snapshot, and publication commits them with the Snapshot. Reuse of an
equal retained Snapshot performs no placement derivation. Capture callers do
not select a predecessor or supply Snapshot ancestry.

Creating a Thread inserts only its origin row and first Comment action in one
transaction. Ordinary Thread reads never create or repair placement rows.

When equal retained state is reused, capture verifies every referenced raw side,
link metadata, and optional link target against its relational digest before
returning its key. Missing, moved, or modified retained content therefore fails
manifest instead of advertising a Snapshot that cannot be read.

The default store is `store` beside the SQLite database; `--store-path` selects
another root. Database and store paths inside a reviewed repository are rejected
before storage directories are created. Captured File rows retain absolute paths,
so changing `--store-path` affects new publication and does not relocate existing
Snapshots.

## HTTP flow

`/api/pull-request/prepare` is the complete Pull Request preparation boundary. It
parses the URL, finds the marked repository and forge remote, fetches the
forge-recorded target and head objects directly by SHA, and computes their
merge base locally. It returns the repository id, Pull Request URL, merge-base
commit, and Pull Request head commit, plus observed source/target branch names.
Missing or non-directory marks are logged and skipped while searching; they do
not prevent a later available mark from matching the Pull Request.
Preparation retains each observed target/head under `refs/dirdiff/prepared/<sha>`
in the marked repository's shared Git directory, keeping the capture sides and
their history reachable after refreshes and Git garbage collection. These refs
have no automatic expiry and are independent of Room and Snapshot identity.
Fetching never selects a newer revision by reading updated tracking refs.
No part of that operation belongs to manifest.

`/api/manifest` accepts one complete Tab parameter value in order to show the
specified repository state and provide keys for later `/api/file-diff` operations.
Its concrete source setup validates controls and storage placement, constructs
the source, and separately supplies prepared correspondence facts and labels to
`corresponding_room`. No raw revision controls or source roots enter Room APIs.
It asks the returned Room for `meta(snapshot_id)` and `manifested(snapshot_id)`, builds the
manifest tree and File totals, copies the aggregate line counts, and returns the
same Snapshot key. For Pull Request parameters, manifest uses the URL only for
Room correspondence and the two commits only for capture. It does not parse a
Pull Request URL, contact a forge, fetch refs, derive commits, or apply Branch
Review selection logic.

`POST /api/agent/join_review` registers one disposable ordinary Profile,
normalizing either compact lowercase or canonical hyphenated UUID input to the
compact stored identity. It maps the supplied Tab into the shared capture
operation and returns the resulting Snapshot id, Profile id, activity boundary,
and the existing durable Snapshot directory.
`POST /api/agent/continue_review` recovers the exact Room and persisted Tab from
the supplied Snapshot and passes the new captured state through the same
publication path. Neither operation introduces another Room identity or
persistent review-handle entity.

Before joining, `GET /api/agent/onboard` translates one complete supported HUD
Tab URL into the existing join-review Tab shape. It also returns the running
server origin and exact absolute paths to the three installed agent workflow
skills. It reads the active Mark registry for repository-backed Tabs but does
not capture, register, or persist anything.

An agent retains the Profile id, latest Snapshot id/path, and activity boundary
returned by these operations for later rounds in the same task. Later rounds
start with continue review; they do not register another Profile through join
review.

`/api/lazy-info` accepts only `snapshot_id`, calls `find_room(snapshot_id)`, and
then `manifested(snapshot_id)`. It reads captured File metadata and never
reloads preset metadata. The Room's persisted Tab supplies the display-name
rule. Persisted capture errors do not prevent either metadata operation.

`/api/file-diff` accepts `snapshot_id`, engine, and the filepath pair. It calls
`find_room(snapshot_id)` and then performs one direct
`get(snapshot_id, left, right)`. The API layer checks the returned capture error,
then reads the authenticated captured sides and any parsed link facts, uses the Room's
persisted Tab for display naming, starts the selected engine and renderer, and
constructs the HTTP response through format composition. A persisted capture error
follows the endpoint's existing `DirdiffError` response path. The endpoint does
not iterate the Room or reload Git, index, worktree, or preset contents. The
agent API instead returns the existing Snapshot directory already published by
ordinary capture. It creates no additional File, directory, link, or Snapshot
representation.

That returned directory is an agent-facing contract: immediate children are
opaque captured File ids. The exact `left` and/or `right` files are its public
review sides. A link side may have private `*-link.json` and `*-target` files;
agents must neither enumerate those as changed Files nor address findings to
them. The public File ids and side names encode no repository mapping or
manifest presentation; private link metadata contains only its captured nested
paths. The tree is read-only. The reviewer and implementor skills provide distinct
role-specific instructions over this layout in their respective
`references/snapshot_structure.md` files. Changes to publication
layout, side naming, byte meaning, or path interpretation must update
`spec/reviews.md` and all three skill references together so capture changes
cannot silently break the agent workflow.

Repository-mark removal deactivates a Mark instead of deleting its row, leaving
Room and Snapshot identity intact. Marking the same path again reactivates the
same Mark id.
