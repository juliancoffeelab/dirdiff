# Persistent review Threads

## Purpose

A Room contains Threads and Snapshots. One `thread_id` identifies a live
discussion; one `review_thread` row records its origin and one
`review_thread_placement` row binds it to each exact captured code universe:

```text
(snapshot_id, thread_id)
```

Rows sharing a `thread_id` retain independent immutable code placements. Every
action persists `status_after` and `attention_after`; a Snapshot fixes captured
code, while `through_activity_id` fixes the discussion outcome used by a read.

History is the HUD over every Thread returned for one Snapshot. It is not a
relation, placement kind, transition log, or separate read. Located and
unlocated Threads, including resolved and deleted discussions, remain in the
same result.

## Room and Thread boundary

Room adds exactly three review operations:

```python
threads(
    snapshot_id: UUID,
    *,
    page: int,
    limit: int,
    state: Literal["all", "open"],
    through_activity_id: int | None,
    attention: Literal["author", "reviewer"] | None = None,
)
    -> tuple[tuple[Thread, ...], int, int]
get_thread(snapshot_id: UUID, thread_id: UUID) -> Thread
create_thread(snapshot_id: UUID, command: CreateThread) -> Thread
```

The returned Thread performs `discussion`, compact `summary`, Comment
add/edit/delete, and Thread resolve/reopen/delete. `discussion` retains
placement, bounded original selected context, authored Comments, and lifecycle
state. Current File content stays behind the explicit File read instead of a
changed-region response. No operation changes the bound keys. Room does not interpret
private source coordinates.

`threads` bulk-loads placements, origins, actions, and Profiles. Thread objects in
that result load only the selected Snapshot and distinct origin Snapshots they
reference, and share request-scoped captured-text data. `get_thread` and each
discussion write load only the selected placement, unique origin, that Thread's
actions and authors, and the two exact Snapshot records they reference. A write
validates and appends directly from the addressed actions under the Room
publication lock. Starting a Thread returns its bounded first discussion;
existing-Thread actions return only current state and the one changed Comment.

## Targets and placement

A File target stores the exact nullable left/right File pair used by manifest.
A text target additionally stores one public rendered region, selected side,
and positive one-based inclusive line range. Regions are either ordinary File
text or one notebook cell source identified by its public cell key at creation.
An explicit lazy loading policy does not prohibit a text target once the caller
has rendered and selected valid captured contents; origin validation uses the
retained side digest, region, and range exactly like any other File.

Every placement for an existing File references its `snapshot_file` row through
the composite `(snapshot_file_id, snapshot_id)` foreign key. If that exact File
pair is absent, the placement has no File reference and reports
`file_missing`. A present File with a missing reference is an invariant
violation, including empty, binary, lazy, capture-error, and notebook Files.

The migration that removed File-level creation pins each retained historical
File-level Thread to line 1 on its captured right side when present, otherwise
its left side. It preserves every Thread, placement, action, and Comment.

Every Thread originates from a selected text range. Placements report one of
four public states:

```text
unchanged unique region -> exact relocated range, no outdated reason
unique changed region   -> first line of that region, region_changed
region not identified   -> File start/header, region_not_found
exact File absent        -> no code location, file_missing
```

Private source coordinates exist only in the unique text origin row. They are
decoded by Thread implementation to find structural candidates directly from
the origin for each Snapshot. Exactly one matching content hash wins even among
duplicate structural candidates. One changed candidate is identifiable; zero,
multiple, or multiple hash-matching candidates are not. No private coordinate
term or value crosses the HTTP boundary.

Notebook cell identity is likewise private after creation. A real cell id may
identify a changed cell; when that id is duplicated, exactly one matching
source hash among its candidates identifies the unchanged cell. Without a
usable real id, exactly one source hash among all cells identifies the unchanged
cell. Reordering does not matter; zero or multiple hash matches are not
identified. Nested source structure is then matched within the selected cell by
the same rules as ordinary text.

## Original context

Original code is not copied into review persistence. The origin placement
references its immutable Snapshot File, and `Thread.discussion()` reconstructs
one machine-readable selected-side excerpt: its side, first returned line,
absolute selected range, and exact source lines. It contains the complete
selected range plus at most three surrounding lines on each side. Every Thread
has this bounded excerpt. Creation performs the same reconstruction before its
origin and first action are committed, so an accepted target cannot leave a
discussion that later review reads cannot render. Review text uses the same
binary rejection and UTF-8-with-optional-BOM decoding as the File renderer.
Notebook targets read the selected original cell source; valid notebooks do not
also accept ordinary text targets. Excerpt construction does not call or expose
a diff engine or perform a comparison.

`region_changed` returns the current matched range and its public outdated
reason. The current File is read explicitly when its content is needed.

## Discussion lifecycle

Every author is a durable ordinary Profile with a globally unique username.
Frontend Profiles are selected by exact-name login or explicitly created by the
HUD; opening an agent review creates the same Profile shape and
adds a separate UUID binding. Reads require no selected Profile. Every browser
and agent write names one existing `profile_id`. Display names come from the
current Profile row, so renaming a Profile changes subsequent review reads
without rewriting authored actions.

`review_action` is append-only. Its first row creates the first Comment at
sequence zero. Later rows create, edit, or tombstone Comments, or resolve,
reopen, or delete the Thread. Folding sequence order produces current Comments,
Comment revisions, Thread state, and Thread revision.

Thread creation time is the sequence-zero action's timestamp. It is not copied
into per-Snapshot placement rows. Every valid Thread has at least that first
Comment; HTTP response validation rejects an empty Comment list.

Legal Thread transitions are:

```text
open -> resolved
resolved -> open
open | resolved -> deleted
```

Replies and Comment changes remain valid while resolved. Deleted Threads reject
every further operation. Only a Comment author may edit it. Any valid Profile
may tombstone a Comment or delete a Thread; the appended action attributes that
deletion to the acting Profile. Deleted Comments remain body-less tombstones.

The browser supplies no operation, Thread-creation, Comment-creation, or
revision identifiers. The backend generates entity and internal action IDs,
reads the current revision under the write lock, validates the transition, and
records the accepted revision. Repeating an HTTP action performs it again; no
retry or replay protocol exists.

## Relational invariants

Review persistence adds two relations. Agent registration adds one narrow
one-to-one relation without changing the existing Profile relation:

```text
user_profile(id, username UNIQUE)

agent_profile(profile_id, agent_uuid)

review_thread(thread_id, origin_snapshot_id)

review_thread_placement(
  thread_id, snapshot_id, snapshot_file_id,
  target_kind, public location fields,
  outdated_reason, private origin coordinate,
  PRIMARY KEY(thread_id, snapshot_id)
)

review_action(
  activity_id, operation_id, thread_id, snapshot_id, sequence, kind, profile_id,
  comment_id, expected_revision, body, created_at, status_after, attention_after
)
```

`review_action.profile_id` is non-null and references `user_profile.id` for
every author. Agent Profiles have the same author shape as frontend Profiles;
`agent_profile` retains only the UUID supplied at agent registration.

Every action row has Thread and sequence values. Comments are folded, while
lifecycle and attention are read directly from the latest persisted outcome.
`activity_id` is a durable increasing order used by agent
continuation reads. There is no separate event, submission, delta, checkpoint,
or agent-authorship action variant.

All nullable variant constraints use explicit discriminated `CASE` predicates.
An unknown or incomplete region, location, locator, or action shape
therefore evaluates false rather than SQLite's permissive NULL CHECK result.

There is at most one origin per `thread_id`; Thread creation establishes that
one exists with its first action in the same transaction. Action sequence is
unique and contiguous per discussion. Backend-generated Comment and internal
operation ids are globally unique. Review reads reject incomplete origin, placement, Profile,
or File references instead of repairing or substituting data.

## Browser HTTP boundary

The browser API is keyed by Snapshot:

```text
GET  /api/review/threads?snapshot_id=...&page=...&limit=...&through_activity_id=...
POST /api/review/post_comment
POST /api/review/edit_comment
POST /api/review/delete_comment
POST /api/review/resolve_thread
POST /api/review/reopen_thread
POST /api/review/delete_thread
```

The first page passes no activity pivot. The persistence read chooses the
greatest current Room activity in the same database session as that page and
returns the concrete inclusive `through_activity_id`; every later page repeats
it. Membership, lifecycle ordering, count, and hydrated actions include only
actions at or before that pivot, so concurrent review writes wait for the next
refetch instead of moving a Thread between pages. The paged read applies stable
open, resolved, deleted ordering and page bounds in SQLite before hydrating Threads.
Each HTTP response is bounded. Pagination is only a transport bound: the
canonical browser query consumes every page before publishing the complete
Snapshot Thread set. Starting a Thread returns its bounded first
discussion. Existing-Thread writes return the exact Thread/Snapshot IDs,
current status, attention, discussion revision, and only the changed Comment.
Lifecycle actions return a Comment when their optional body is present. The discussion revision is the
accepted action's per-Thread sequence. No follow-up read is required for a
contiguous action result.
No History endpoint exists. Expected browser review failures return a direct
`{code, message}` body using the stable review error enum; clients never parse
presentation text to distinguish conflicts.

Immediate writes target a bounded response within 50 ms. Slower measurements
are inspected for avoidable work rather than hidden by the frontend. Bounded
Thread pages and structural first-Comment parsing are allowed to represent their
real work. Browser review operations use a one-second transport timeout instead
of the generic eight-second timeout.

## Browser HUD

`App` already holds the selected Profile and passes it into the Snapshot-bound
`ReviewProvider`. The review frontend adds no second identity localStorage
entry or identity modal. With no selected Profile,
Threads remain readable and each attempted browser write presents concise text
directing the user to log in or create a Profile through the existing Profile
control. Review does not open, focus, click, dispatch an event to, or otherwise
control that UI, and it does not invent an anonymous author.

`ReviewDraftRoot` lives for the application lifetime and is the sole Solid and
localStorage representation of the strict draft document. Empty Comment input
is not a draft: it remains only in the open input, enters the document on the
first non-whitespace input, and is removed again if emptied.
The root removes empty stored input before publishing the document, validates
the remaining values, and directly serializes later typed draft operations
before publishing them to Solid.
`ReviewProvider` lives for one exact Snapshot outside engine-keyed File
rendering. It observes the complete Thread set of the canonical review query. It
performs explicit writes and updates a contiguous cached Thread with each
returned action. When the returned discussion revision does not immediately
follow the loaded revision, it refetches the canonical pages instead of leaving
any unseen Comment, Comment change, or Thread-state action absent from the HUD.
When provider disposal has removed that cache, completion constructs no
substitute. It does not copy backend Threads into a second client store.

While that bulk query is pending or failed, code markers and File actions are
disabled rather than interpreting unavailable persisted Threads as an empty
set, including while retained data is being refetched after invalidation.
History presents the query state and Retry action; no review write can start
until the authoritative Thread set is current.

File headers and rendered line numbers expose Comment triggers derived from the
Snapshot query and local drafts. Text selection is one-side, one-based, and
inclusive; Shift-click extends the active range in the same File, rendered
region, and side. Notebook source uses its public cell key. A new-Thread draft
retains its local draft ID, Snapshot, Profile, public target, and body. A reply
draft retains only its draft ID, Thread ID, Profile, and body; an edit draft adds
its Comment ID. Reply and edit drafts do not retain a Snapshot, code location,
or timestamp because their loaded Thread supplies current placement and
submission context. No draft copies source excerpts or Thread data.
New-Thread drafts appear in the Drafts section and expose `Continue editing` for
their saved code target. Reply drafts appear only in their Thread's permanent
reply input. Edit drafts appear only beneath their Comment. Their stable Thread
and Comment identities carry them into a later Snapshot representation of the
same discussion without rewriting localStorage on mount. Because the
canonical query contains every Thread, persisted reply and edit input has
an ordinary rendering in that Thread and requires no focused Thread read or
separate navigation action. A new-Thread draft remains bound to its original
Snapshot. Submission disables that draft for one HTTP
action. Success removes it; failure leaves the ordinary editable local draft.
Thread state and Comment deletion controls send one direct action and retain no
replay state.

A `file-start` placement binds to ordinary line one when that side is available
in the current mounted full text renderer; otherwise it binds to the File
header. DiffGrid reports the actual mounted line-one triggers after every
complete render and fold replacement instead of inferring them from backend
rows. It never appears in both places. Before a renderer replaces or disposes row DOM,
it explicitly closes only the Comment input or inline Thread panel anchored inside
that DOM; unfinished input remains saved, and Comment inputs anchored in other Files
remain open. An expanded fold-edge Comment trigger remains visible and its
activation skips the row's fold action.

Marker facts are one memoized derivation indexed by exact rendered
line coordinates from the canonical Snapshot query and new-Thread draft target.
Draft body changes do not publish a marker revision or wake mounted DiffGrids.
Trigger reads perform keyed lookups; the
index is never a writable review store. A rendered line contains only controls
for its actually represented states; absent lifecycle states have no hidden DOM
controls. One rendered line exposes distinct
markers for its open, resolved, and deleted Threads. Resolved markers are green;
clicking any state marker opens only the Threads in that state instead of
combining every Thread on the line. State markers show a compact Comment icon and
count, expose the complete state in their label and tooltip, and grow rightward
so adjacent states remain visible. New-Thread and draft markers remain separate.
A
line-marker failure disables and clears only the affected grid's review decorations and
presents a local error beside its otherwise intact rows. That failed state
persists for the grid lifetime, so every later fold or renderer replacement
also receives disabled review triggers.

History renders the complete canonical Thread set in open, resolved, then
deleted order and shows its Thread count. Open Threads appear directly;
Resolved has a labelled visible group; Deleted has a labelled, counted native
disclosure group that starts folded. Every Thread carries the small status dot
for its open, resolved, or deleted state. Its explicit refresh control refetches
the complete canonical query while retaining its presentation;
it does not introduce a browser activity-delta protocol. Every expanded Thread
labels its retained excerpt with the original selected-side File path and line
range. Inline view starts with History closed in a right-side ChangeSet grid
column mirroring the FileTree rail; opening it expands that same column. The
rail carries a vertical label, Thread count, and `m` hint. Split view uses one
right-side host below the sticky File header; its closed eye-and-`m` control and
expanded overlay occupy that same host. The `m` hotkey and visible panel
controls toggle one ChangeSet-local History state in either mode. The panel has
its own scroll and does not follow File-lane scrolling for content or
navigation. Inline History occupies a viewport-height sticky grid slot, so its
header and independently scrolling contents remain visible during File-lane
scrolling. The split host measures the currently sticky content-sized File
header so both forms remain directly below it. When the Snapshot has no File
header, the same host sits directly below the application header so unlocated
Threads remain accessible. Resolved and deleted Threads start folded; open
Threads show complete Comments and retained code context. An expanded Thread
presents the initiating participant on the left and other participants on the
right, with one compact square-cornered retained excerpt beneath the header's
File-and-line identity. History does not repeat that identity above the excerpt.
Retained source wraps long lines inside its bordered excerpt. The open inline
rail uses the same width as the open FileTree. Their closed controls share the
same icon, vertical-label, and key-hint geometry, so `Files` and `History` begin
on the same horizontal line.
The expanded Thread uses a light vertical conversation guide instead of an
enclosing card; only the excerpt, Comment bubbles, and input carry their own
quiet boundaries, with no shadow.

The new-Thread Comment input and inline Thread panel are inserted directly beneath
their selected-side line inside the diff grid. They expand the document instead
of floating over it; ordinary document scrolling moves the complete input or
Thread and its Comment inputs with their code, with no separate clipped viewport.
One matching Thread opens expanded. Multiple matching Threads open as compact
folded strips, and each strip expands or folds independently.

Every expanded, non-deleted Thread renders a reply textarea directly inside its
complete context. It is the actual input, not a read-only control replaced after
focus. Its first meaningful text creates the one matching persisted reply draft
for the Thread, Snapshot, and Profile; emptying or discarding it removes that
draft. A deleted Thread with an existing reply draft keeps its disabled reply
textarea and Discard action visible inside that Thread; it cannot submit. Edit
renders below its affected Comment and reopens the one matching
persisted draft. A visible close removes an empty new-Thread input and an
unchanged or empty edit draft; otherwise it merely closes the input. Renderer
detachment preserves the draft. Submit actions are labelled `Comment`, `Reply`,
and `Save`. Meta+Enter on macOS and Ctrl+Enter on Windows and Linux submit the
focused Comment input. Enter and Shift+Enter remain ordinary textarea input; a
Shift- or Alt-modified shortcut does not submit. Comment rows expose compact labelled Edit/Delete icons at the top
right and no Comment-level `View`. Comment and Thread deletion require explicit
confirmation before their HTTP action begins.

Each History Thread header strip toggles that Thread's folded state; it has no
separate fold control. A folded strip contains the author and location on its
first line and a one-line Comment summary beneath it. The go-to-code icon is the
only part of the strip that does not toggle. It expands the Thread, navigates to
its rendered marker, and
opens that exact Thread in the code-aligned panel. A warning icon with hover
text replaces a textual outdated label. Go-to is enabled only for an already
loaded full File. It uses the existing exact-line navigation to reveal the
selected side and first selected line, then opens only that Thread in the
code-aligned panel. It never loads or admits a lazy File or Husk. Neither the
navigation nor Thread opening selects a hunk or starts scroll-follow. A
`file_missing` Thread disables
the control and states on hover and in its expanded content that the reviewed
File is not present in the Snapshot. Folding a History row does not navigate.

Unexpected review-presentation derivation failures are contained around the
Comment input, inline Thread panel, and History presentation. The File lane is a
sibling of that boundary and remains mounted; renderer-local failures continue
to stop at their individual File boundary.

## Agent HTTP boundary

The agent API is a filesystem-oriented boundary over the same Rooms,
Snapshots, Threads, Comments, and Profiles as the browser:

```text
POST /api/agent/join_review
GET  /api/agent/thread_summary?snapshot_id=...&page=...&limit=...
GET  /api/agent/threads?snapshot_id=...&for=author|reviewer&page=...&limit=...&through_activity_id=...
GET  /api/agent/thread/{thread_id}?snapshot_id=...&page=...&limit=...
POST /api/agent/continue_review
POST /api/agent/actions
```

Join review accepts an agent UUID, display name, and an explicit HEAD, refs,
Branch Review, or Pull Request Tab. Repository-backed Tabs name the exact
path of an active Mark; nothing marks a path implicitly. Registration creates
one ordinary `user_profile` and its one-to-one `agent_profile` UUID binding.
The response contains only the Profile id, Snapshot id, current activity
boundary, actionable author/reviewer/both counts, and absolute filesystem
Snapshot path.

The filesystem path is the existing durable Snapshot directory already
published by normal capture. Agents use ordinary filesystem operations on the
exact captured Files used by the frontend. The API creates no copy, hardlink,
materialized directory, generated layout, File reference, or HTTP File-content
operation.

The returned directory has one immediate opaque File-id directory per captured
File pair. Each pair directory contains an exact `left` file when the left side
exists and an exact `right` file when the right side exists. File ids are not
repository paths, and the tree contains no manifest, ordering, display name, or
repository-path mapping. Agents enumerate every pair, inspect each present side
with content-appropriate tools, and pass the exact absolute side path to
`create-finding`. Missing `left` or `right` files represent absent sides; the
Snapshot tree is immutable and never a worktree.

This filesystem contract has separate role-specific operational instructions
in `.agents/skills/review-patch/references/snapshot_structure.md` and the
implementor appendix in
`.agents/skills/babysit-patch/references/snapshot_structure.md`. Any change to
Snapshot directory organization, side filenames, path meaning, mutability, or
the way agents obtain captured bytes must update both skill references and this
specification in the same change. The reviewer document defines complete
capture inspection and finding paths; the implementor document defines
finding-evidence reads, live-worktree changes, recapture, and reviewer handoff.

Thread summary returns open Threads with first/latest Comment previews and
counts. It defaults to 20 items and permits at most 100. Bulk Threads returns
complete open Threads, defaults to 5, and permits at most 20. Focused Thread
reads include every lifecycle state, repeat placement and original-excerpt
metadata on each page, default to 20 Comments, and permit at most 100. Pages
are one-based; pages past the end are empty. Preview bodies contain at most
256 Unicode characters, using the first 255 and `…` when truncated. Complete
Thread data and original excerpts are never truncated; excerpts are inherently
bounded by the selected range and six context lines.

Continue uses the old Snapshot to recover its Room and persisted Tab, captures
that Tab again, derives missing placements only when the capture publishes a
genuinely new Snapshot, and returns the selected Snapshot directory. File
changes are sorted absolute paths to captured Files in the old or new Snapshot.
Thread changes are only
authored `review_action` rows after the supplied activity id, ordered by
`activity_id`; placement changes and code changes are not Thread activity.
The default activity limit is 20 and the maximum is 100.

Actions accepts one to 100 ordered `create-finding`, `author-response`,
`reviewer-return`, `reviewer-resolve`, `inert-comment`, or `reviewer-delete`
items. The Profile is the ordinary Profile returned by join review. Creation accepts the actual
absolute path of an existing captured File in the named Snapshot and an
inclusive one-based ordinary text range.
The complete array is validated and appended under the Room write lock in one
database transaction; any invalid item discards the complete batch. Generated
Thread, Comment, and operation ids are fresh. Re-sending an HTTP entity simply
performs its actions again; the agent boundary has no submission identity,
retry, replay, checkpoint, or recovery contract.

Every rejected agent HTTP operation returns a non-2xx response whose
plain-text body describes the concrete validation, HTTP, or domain failure.
Unexpected failures remain ordinary internal-server errors and do not expose
tracebacks.
