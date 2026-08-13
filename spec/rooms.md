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

`RoomLord` has two distinct lookup operations:

```python
corresponding_room(...) -> tuple[Room, UUID]
find_room(snapshot_id: UUID) -> Room
```

`corresponding_room` is used by the shared explicit capture operation behind
manifest and agent review opening. It applies the Tab's law of correspondence
only to find or create the Room. It separately uses the supplied capture inputs
to capture or reuse the current state, then returns the Room and Snapshot key
separately. Snapshot identity never participates in correspondence.

`find_room` is used by follow-up endpoints. A Snapshot key is globally unique,
so it finds the containing Room directly without executing a correspondence law
or reading live backend state.

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
) -> tuple[Optional[Path], Optional[Path], FileMeta]
threads(snapshot_id: UUID) -> tuple[Thread, ...]
get_thread(snapshot_id: UUID, thread_id: UUID) -> Thread
create_thread(snapshot_id: UUID, command: CreateThread) -> Thread
```

`manifested` yields repository-relative left/right Paths, including Files whose
contents could not be captured. `get` accepts one such pair and returns absolute
Paths to the captured contents. Added and deleted Files use `None` for the absent
side. For a File with a persisted capture error, `get` returns that exact reason
in `FileMeta.capture_error`; rendering boundaries reject or classify it before
decoding the generated diagnostic contents.

`FileMeta` contains tracked provenance, backend change classification, an
explicit lazy override, and required-with-null `capture_error`. A non-null
capture error is the exact persisted reason the File contents are unavailable.
It contains no renderer output or per-File line counts.

Every returned `Thread` is bound to the exact `(snapshot_id, thread_id)` pair.
Comment and lifecycle operations belong to that object. Room neither interprets
private source coordinates nor implements discussion state transitions.

`SnapshotMeta` exposes the containing Room's persisted Tab, the two captured
side labels, and the backend-supplied aggregate added/removed line counts. The
Tab is not duplicated in the Snapshot relation; `Room.meta()` combines the
Room and Snapshot facts. The counts are both present or both `None`.

## Law of correspondence

`corresponding_room` selects a Room from:

- nullable Mark id;
- one of the five HUD Tabs;
- an opaque correspondence key.

The key is constructed as follows:

- Diff against HEAD uses the current HEAD commit id;
- Compare Refs freezes commit-backed refs to commit ids and retains `index` and
  `worktree` as explicit ephemeral sides. Snapshot labels use those same
  canonical values rather than the request's ref spelling;
- Branch Review uses the structured symbolic base/review branch pair;
- Pull Request uses the Pull Request URL;
- each preset catalog/subset pair uses one Mark-less Room.

SQLite compares the complete opaque key for equality but does not interpret its
structure. Partial unique indexes preserve the intended marked and Mark-less
Room identities despite SQLite nullable uniqueness behavior.

The values used to capture a Snapshot are separate from this law. In particular,
the Pull Request Tab captures the prepared merge-base commit on the left and the
prepared Pull Request head commit on the right. Those commits do not identify the
Room and cannot replace, extend, or verify the URL correspondence key.

## Captured state

Manifest observation asks the concrete `WorkspaceBackend` for affected filepath
pairs, File metadata, and one aggregate added/removed line-count pair. Git asks
Git for the aggregate counts. Backends without an authoritative aggregate return
`(None, None)`. Additional untracked worktree Files do not suppress Git's
reported aggregate; they remain additional to the tracked diff counted by Git.
A replaced or modified line count does not exist before a diff engine aligns
one File and is never manifest or Snapshot metadata.

The private capture implementation loads every present side through the backend
content interface and stores it unchanged. Capture does not decode or classify
files. A backend loading failure is contained to that File: capture retains its
repository paths and metadata, writes clearly machine-generated diagnostic
content for each failed side, and persists the actual `DirdiffError` reason.
The genuine contents of a side that loaded successfully are retained. Unrelated
Files continue to be captured. Unexpected programming failures still abort the
operation.

Snapshot equality includes:

- left and right repository-path presence;
- tracked provenance and backend change classification;
- complete captured left and right contents;
- persisted capture error, when present;
- explicit lazy override;
- complete `preset.toml` content when it supplies that override.

Backend order, human labels, aggregate line counts, directory-tree output, and
renderer output do not participate in equality. Canonical filepath sorting is
used only while hashing this set; it is not persisted presentation order.

SHA-256 identifies equal captured state inside one Room and validates each
captured side when `Room.get` returns its Path. Repeating manifest with equal
captured state returns the existing `snapshot_id`. Incompatible capture changes
advance the hash-domain version.

Index and worktree capture remains sequential. No atomic multi-file view or
implicit retry is claimed while another process mutates the repository.

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

snapshot_file_lazy_reason
  file_id, reason

snapshot_file_lazy_reason_content
  file_id, complete metadata content

user_profile
  id, username

agent_profile
  profile_id, agent_uuid

review_thread
  thread_id, snapshot_id, nullable snapshot_file_id, immutable placement facts

review_action
  activity_id, operation_id, thread_id, snapshot_id, sequence,
  non-null profile_id author, authored action fields
```

The two Snapshot line-count columns are nullable together. A File's capture
error is nullable because successful capture has no error. Optional File sides,
lazy reasons, and lazy-reason source content are represented by row absence.

The lazy-reason content relation retains the exact preset metadata input that
participated in identity without adding that content to backend path or Room
File metadata. No relation stores manifest ordering, directory trees, display
names, rendered rows, or per-File line counts.

## Publication

Capture writes a process-unique staging directory. Under the database-wide
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

When equal retained state is reused, capture verifies every referenced content
digest before returning its key. A missing, moved, or modified retained File
therefore fails manifest instead of advertising a Snapshot that cannot be read.

The default store is `store` beside the SQLite database; `--store-path` selects
another root. Database and store paths inside a reviewed repository are rejected
before storage directories are created. Captured File rows retain absolute paths,
so changing `--store-path` affects new publication and does not relocate existing
Snapshots.

## HTTP flow

`/api/pull-request/prepare` is the complete Pull Request preparation boundary. It
parses the URL, finds the marked repository and forge remote, fetches the required
refs, and returns the repository id, canonical Pull Request URL, merge-base commit,
and Pull Request head commit. No part of that operation belongs to manifest.

`/api/manifest` accepts one complete Tab parameter value in order to show the
specified repository state and provide keys for later `/api/file-diff` operations.
It constructs the workspace backend and calls `corresponding_room`. It asks the
returned Room for `meta(snapshot_id)` and `manifested(snapshot_id)`, builds the
manifest tree and File totals, copies the aggregate line counts, and returns the
same Snapshot key. For Pull Request parameters, manifest uses the URL only for
Room correspondence and the two commits only for capture. It does not parse a
Pull Request URL, contact a forge, fetch refs, derive commits, or apply Branch
Review selection logic.

`POST /api/agent/reviews/new` registers one disposable ordinary Profile, maps
the supplied Tab into the shared capture operation, and returns the resulting
Snapshot id, Profile id, activity boundary, and the existing durable Snapshot
directory.
`POST /api/agent/continue_review` recovers the exact Room and persisted Tab from
the supplied Snapshot and passes the new captured state through the same
publication path. Neither operation introduces another Room identity or
persistent review-handle entity.

`/api/lazy-info` accepts only `snapshot_id`, calls `find_room(snapshot_id)`, and
then `manifested(snapshot_id)`. It reads captured File metadata and never
reloads preset metadata. The Room's persisted Tab supplies the display-name
rule. Persisted capture errors do not prevent either metadata operation.

`/api/file-diff` accepts `snapshot_id`, engine, and the filepath pair. It calls
`find_room(snapshot_id)` and then performs one direct
`get(snapshot_id, left, right)`. The API layer checks the returned capture error,
then reads the captured Paths, uses the Room's persisted Tab for display naming,
starts the selected engine and renderer, and constructs the HTTP response.
Because those consumers require text, this endpoint decodes the selected
contents and rejects binary or non-UTF-8 input locally. A persisted capture error
follows the endpoint's existing `DirdiffError` response path. The endpoint does
not iterate the Room or reload Git, index, worktree, or preset contents. The
agent API instead returns the existing Snapshot directory already published by
ordinary capture. It creates no additional File, directory, link, or Snapshot
representation.

Repository-mark removal deactivates a Mark instead of deleting its row, leaving
Room and Snapshot identity intact. Marking the same path again reactivates the
same Mark id.
