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

History is the HUD over every Thread in one Room. It is not a relation,
placement kind, transition log, or separate read. The displayed Snapshot
supplies placement context but never filters discussion membership. Located and
unlocated Threads, including resolved and deleted discussions, remain in the
same result.

## Room and Thread boundary

Room adds exactly four review operations:

```python
threads(
    snapshot_id: UUID,
    *,
    page: int,
    limit: int,
    state: Literal["all", "open"],
    through_activity_id: int | None,
    attention: Literal["author", "reviewer"] | None = None,
    scope: Literal["snapshot", "room"],
)
    -> tuple[tuple[Thread, ...], int, int]
get_thread(snapshot_id: UUID, thread_id: UUID) -> Thread
thread_for_comment(snapshot_id: UUID, comment_id: UUID) -> Thread
create_thread(snapshot_id: UUID, command: CreateThread) -> Thread
```

The returned Thread performs `discussion`, compact `summary`, Comment
add/edit/delete, and Thread resolve/reopen/delete; each existing-Thread write
returns its bounded update view. `discussion` retains placement, bounded
original selected context, authored Comments, and lifecycle state. Current
File content stays behind the explicit File read instead of a changed-region
response. No operation changes the bound keys. Room does not interpret
private source coordinates. `thread_for_comment` serves Comment-addressed
writes that know only the Comment key.

`threads` bulk-loads placements, origins, actions, and Profiles. Snapshot scope
requires a selected placement for agent reads. Room scope includes every
discussion for browser History. When a Room Thread has no selected placement,
its handle uses its real origin placement for discussion writes and reports the
captured relation between the origin File and displayed File. Thread objects in
that result load only the selected Snapshot and distinct origin Snapshots they
reference, and share request-scoped captured-text data. `get_thread` returns a
lightweight handle carrying the selected placement, unique origin, and that
Thread's actions and authors; the referenced Snapshot File records load only
when a read interprets placement, so write-only callers never pay for them. A
write validates and appends directly from the addressed actions under the Room
publication lock. Starting a Thread returns its bounded first discussion;
existing-Thread actions return only current state and the one changed Comment.

## Targets and placement

A File target stores the exact nullable left/right File pair used by manifest.
The internal `FilePair` carries these side paths as `Option[str]`, preserving
them through captured-File lookup and composition. HTTP constructors convert
nullable inputs with `maybe`, and public origin views extract nullable paths.
A text target additionally stores one public composed bay key, selected side,
and positive one-based inclusive line range. The bay key is the universal
sub-file coordinate composition gives every bay: `flatfile` for a File with
text File, and the composer's own key for every other bay. Review stores that
key and never interprets it, so no format has a target shape of its own.
An explicit lazy loading policy does not prohibit a text target once the caller
has rendered and selected valid captured contents; origin validation uses the
retained side digest, bay, and range exactly like any other File.

An `image` or `blob` bay holds no lines, and a target against one is defined
to name the single line `1..1`. Validation reads the kind `bays()` reports and
refuses every other range against such a bay, so a range is never silently
clamped into the one line that exists. That single line is not empty: review
reconstructs it from the media side's own facts as `<media type>, <n> bytes,
sha256 <digest>`, which makes the ordinary origin machinery do real work on a
picture — replacing the content changes the line, the retained region hash stops
matching, and the Thread is reported outdated. Nothing else about the target shape
differs: the bay key, side, range, digest, and placement states are the ones
every File uses.

Every placement for an existing File references its `snapshot_file` row through
the composite `(snapshot_file_id, snapshot_id)` foreign key. If that exact File
pair is absent, the placement has no File reference and reports
`file_missing`; a present File whose capture failed is deliberately
unreferenced too, and reports `file_unreadable`. Every other present File with
a missing reference is an invariant violation, including empty, blob, lazy, and
notebook Files.

The migration that removed File-level creation retains each historical
File-level origin and placement as `file-start`, choosing the captured right
side when present and otherwise the left side of that exact placement. It does
not read captured bytes, fabricate a range, excerpt, or private coordinate, or
change any Thread, action, or Comment identity. New Thread creation accepts
only a selected text range or the whole review.

Every newly created File Thread originates from a selected text range.
Retained historical File-level Threads are the sole `file-start` origin
exception. The other creation shape is the review-scoped Thread, which has no
File at all: it is a conversation about the review as a whole, such as a
summary or a question to the author.

A review-scoped Thread stores the origin `review` and the placement
`review-scope`, which carry no File reference, bay, range, side, digest, or
private coordinate. Placement derivation copies its `review-scope` placement
into every later Snapshot of the Room unchanged, so it is placed in every
Snapshot and is never outdated, relocated, missing, or unreadable. It travels
the browser boundary as `origin_target: {"kind": "review"}` with `placement:
{"kind": "review-scope"}` and no excerpt, and the agent boundary reports it
with no `location`.
A text-origin placement lands in one of six stored states, and each one
publishes as exactly one browser placement kind:

```text
unchanged unique region -> exact relocated range              -> region-kept
unique changed region   -> first line of that region,
                           region_changed                     -> region-changed
region not identified   -> start of the origin's own bay,
                           region_not_found                   -> region-lost
bay no longer composed  -> start of the File's first bay
                           carrying the origin side,
                           bay_not_found                      -> bay-lost
   ... and where no composed bay carries that side, File
       start, bay_not_found                                   -> side-lost
File not captured       -> no code, file_unreadable           -> file-unreadable
exact File absent       -> no code, file_missing              -> file-absent
```

A retained historical File-level placement is the eighth kind, `whole-file`;
the review-scoped placement is the ninth, `review-scope`.
The stored `bay_not_found` covers two different landings, and the browser
names them apart because they present differently: `bay-lost` has a bay to
mark and navigate to, `side-lost` has none.

`file_unreadable` is the exact File whose capture failed in this Snapshot. The
File pair is present — the backend listed it — but its capture directory holds
dirdiff's placeholder text rather than the File's own bytes, so every
coordinate it could offer describes something dirdiff wrote: a bay would name
composed placeholder text, and File start would name a side record whose digest
describes that same text. The Thread therefore lands with no code location at
all, which distinguishes it from `file_missing` by reason alone. Derivation
contains the failure at this one placement — a Snapshot with one unreadable
File still lists every other Thread — and logs the captured `error`, which no
placement field carries.

Derivation chooses and stores every landing, including the first-bay
`bay_not_found` landing, while it already holds the target File's composed
bays. Reads publish the stored fields verbatim: listing Threads never reads
captured bytes and performs no composition. A historical File-level origin
has no write-time boundary that composes its File, so its placements are
`file-start` with no outdated reason everywhere — it never gains a bay
landing.

Private source coordinates exist only in the unique text origin row. They are
decoded by Thread implementation to find structural candidates directly from
the origin for each Snapshot. Exactly one matching content hash wins even among
duplicate structural candidates. One changed candidate is identifiable; zero,
multiple, or multiple hash-matching candidates are not. No private coordinate
term or value crosses the HTTP boundary.

Bay identity is public and durable rather than private. `Composer.bays()`
recomputes the bay keys a File composes into, and the stored key selects one
of them directly; a notebook cell key is the notebook's own cell id, so an edited
or moved cell keeps it. A File that no longer composes the stored key places
nothing. Nested source structure is then matched within the selected bay by
the same rules as ordinary text.

An id-less notebook cell instead derives a pseudo-cell frame key from the
SHA-256 of its source plus its zero-based occurrence among cells with that same
source. Its source, metadata, and output bays extend that frame key. The
occurrence only makes repeated identical source unique; the source is the
identity, so any matching pseudo-cell has the exact source the Thread named. An
edit changes the hash and therefore follows ordinary `bay_not_found` placement.

Validation calls `bays()` rather than re-deriving what the renderer shows, so
the bay keys a target may name are exactly the keys composition produces. That
call carries a `BayContext`, which has no renderer field, so bay identity
is recomputed without a diff engine in reach.

## Original context

Original code is not copied into review persistence. A text origin references
its immutable Snapshot File, and `Thread.discussion()` reconstructs one
machine-readable selected-side excerpt: its side, first returned line, absolute
selected range, and exact source lines. It contains the complete selected range
plus at most three surrounding lines on each side. Creation performs the same
reconstruction before its origin and first action are committed, so an accepted
text target cannot leave a discussion that later review reads cannot render.
Review text uses the same binary rejection and UTF-8-with-optional-BOM decoding
as the File renderer. The excerpt is the named bay's own text, which
composition already decoded, so a notebook cell reads its cell source and a File
that composes no `flatfile` bay accepts no flatfile target. A media bay's excerpt
is its one pseudo-line, quoted back like any other selected line, so an agent
reading the discussion sees the media type, size, and digest it commented on. Excerpt
construction does not call or expose a diff engine or perform a comparison. A retained
historical `file-start` origin has no selected range and therefore returns no
excerpt; reads never fabricate one from captured content.

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

agent_profile(profile_id, agent_uuid, model NULL, harness NULL)

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
`agent_profile` retains the UUID supplied at agent registration and the model
and harness names the agent last reported; both are NULL only for agents that
registered before they were recorded.

Every action row has Thread and sequence values. Comments are folded, while
lifecycle and attention are read directly from the latest persisted outcome.
`activity_id` is a durable, database-wide increasing order used by the agent
`activity` and `wait` reads as their cursor, across every Room of a Mark. There is no separate event, submission, delta, checkpoint,
or agent-authorship action variant.

The store exposes `ReviewThreadRecord.placement` as one validated range,
bay-start, File-start, missing, or unreadable variant. Private locator bytes
and the origin label remain separate record metadata. Review uses that same
variant for derivation and interpretation, with no flat-record conversion.

Action writes supply `ReviewActionInput` with a tagged payload. Creation and
editing require Comment identity and text; editing and Comment deletion also
require the prior revision. Resolve/reopen may carry one whole Comment or none;
Thread deletion carries no Comment fields. Reads return `ReviewActionRecord`
with the accepted action and a required positive `activity_id`. Per-Thread
sequence, author authorization, transition legality and revision checks remain
cross-row review invariants.

*Implementation detail:* nullable SQL columns encode these variants. Store
mappers validate their presence and discriminators before constructing typed
values, and flatten typed inputs only at insertion. Enum vocabularies are
validated in Python. SQL retains identifier shapes, non-empty text, ordinal
ranges, presence pairings, foreign keys, and uniqueness constraints.

There is at most one origin per `thread_id`; Thread creation establishes that
one exists with its first action in the same transaction. Action sequence is
unique and contiguous per discussion. Backend-generated Comment and internal
operation ids are globally unique. Review reads reject incomplete origin, placement, Profile,
or File references instead of repairing or substituting data.

## Browser HTTP boundary

The browser API uses the displayed Snapshot as Room and placement context:

```text
GET  /api/review/threads?snapshot_id=...&page=...&limit=...&through_activity_id=...
POST /api/review/post_comment
POST /api/review/edit_comment
POST /api/review/delete_comment
POST /api/review/resolve_thread
POST /api/review/reopen_thread
POST /api/review/delete_thread
```

The first page passes no activity pivot. Browser membership contains every
Thread whose origin belongs to the Snapshot's Room. A selected-Snapshot
placement is returned when it exists. Otherwise the response returns the real
origin placement, its origin Snapshot id for follow-up actions, and whether the
same captured File in the displayed Snapshot is unchanged, changed, missing, or
unreadable. This origin placement produces no displayed-code marker. History
uses an unchanged origin as an exact jump, a changed origin as an explicitly
approximate File-start jump, and missing or unreadable Files as disabled jumps.

The persistence read chooses the
greatest current Room activity in the same database session as that page and
returns the concrete inclusive `through_activity_id`; every later page repeats
it. Membership, lifecycle ordering, count, and hydrated actions include only
actions at or before that pivot, so concurrent review writes wait for the next
refetch instead of moving a Thread between pages. The paged read applies stable
open, resolved, deleted ordering and page bounds in SQLite before hydrating Threads.
Each HTTP response is bounded. The canonical browser query publishes page one
immediately, then automatically reads and appends later pages under its retained
activity boundary. The HUD therefore becomes useful before complete Room
membership has arrived. Starting a Thread returns its bounded first
discussion. Existing-Thread writes return the exact Thread/Snapshot IDs,
current status, attention, discussion revision, and only the changed Comment.
Lifecycle actions return a Comment when their optional body is present. The discussion revision is the
accepted action's per-Thread sequence. No follow-up read is required for a
contiguous action result.
Every Comment in every browser response carries `author` as
`{profile_id, display_name, is_agent, model, harness}`. `model` and `harness`
are the facts an agent Profile reported, null for an agent that registered
before they were recorded, and always null for a human, whose `is_agent` is
false. The review domain's author view carries these facts itself: the bulk
Profile read that a review read already performs joins the agent registration,
so no response, browser or agent, reads them separately.
A returned Thread states its immutable creation target once, as
`origin_target`, and either what the displayed Snapshot did to it or its real
origin placement when the displayed Snapshot predates it. `location_source`
distinguishes those cases. The
origin is the only place the File pair, bay, and side appear, and it carries
the reconstructed excerpt cut from its own Snapshot; the placement adds only
what the origin cannot say — the relocated range, the replacement bay, or the
bare name of the loss. No field pairing crosses the two, so no response shape
is valid in one field and invalid in another.
`post_comment` takes `{snapshot_id, profile_id, target, body}`. Its `target`
is either a `text` target (File pair, composed bay, side, and inclusive range)
or `{"kind": "review"}`. A text target goes through code Thread creation. A
review target reuses the agent batch's `CreateReviewThread` operation in a
batch of one, acting as `reviewer`; the new Thread therefore waits on the
author, as every browser-created code Thread does. Its response carries
`origin_target` `{"kind": "review"}` and placement `review-scope`, and the
operation hydrates no File.
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
rendering. It observes the progressively loaded pages of the canonical review
query. Those pages, their total, and their activity boundary remain immutable.
Successful browser writes publish their mutation responses as post-boundary
activity beside the pages, so the HUD shows the user's accepted creation,
Comment change, or lifecycle action without rewriting historical query data.
Presentation applies contiguous updates by discussion revision and ignores an
activity once a later query result contains an equal or newer revision. It does
not copy query Threads into another client store.

Before page one arrives, or after the progressive query fails, code markers and
File actions are disabled rather than interpreting unavailable persisted
Threads as an empty set. Each successful page is authoritative at the fixed
activity boundary and becomes usable while later pages load. History presents
query failure and Retry; retry starts a fresh page-one read.

Rendered line numbers expose Comment triggers derived from the
Snapshot query and local drafts. Text selection is one-side, one-based, and
inclusive; Shift-click extends the active range in the same File, rendered
bay, and side. Every bay is named by its composed key. A new-Thread draft
retains its local draft ID, Snapshot, Profile, public target, and body. A reply
draft retains only its draft ID, Thread ID, Profile, and body; an edit draft adds
its Comment ID. Reply and edit drafts do not retain a Snapshot, code location,
or timestamp because their loaded Thread supplies current placement and
submission context. No draft copies source excerpts or Thread data.
The History header carries one whole-review control, a Comment icon titled
"Comment on the whole review". It opens the ordinary new-Thread Comment input in
a slot at the top of History's scroll area, or closes it when it is already
open there. That input has no code anchor, so renderer changes never close it.
An inline/split view switch rebuilds History and discards its slot, so the switch
explicitly closes the input mounted there with ordinary close semantics: typed
text remains a draft under Drafts and an empty input leaves none, and the
header control then opens a fresh input in the new slot.
It is also available on a Snapshot with no Files. Its draft targets
`{"kind": "review"}` and has no line marker.
New-Thread drafts appear in the Drafts section and expose `Continue editing`: a
code draft continues at its saved code target, a whole-review draft, labelled
"Whole review", in History's slot. Reply drafts appear only in their Thread's permanent
reply input. Edit drafts appear only beneath their Comment. Their stable Thread
and Comment identities carry them into a later Snapshot representation of the
same discussion without rewriting localStorage on mount. Because the
progressive query eventually loads every Thread, persisted reply and edit input
has an ordinary rendering once that Thread arrives and requires no focused
Thread read or separate navigation action. A new-Thread draft remains bound to its original
Snapshot. Submission disables that draft for one HTTP
action. Success removes it; failure leaves the ordinary editable local draft.
Thread state and Comment deletion controls send one direct action and retain no
replay state.

A `region-kept` or `region-changed` placement marks its stored bay-local
line; `region-lost` and `bay-lost` mark line one of the bay they name — both
are ordinary line markers on coordinates the backend stored, with no read-time
reconstruction and no frontend-minted bay key. The three placements naming no
bay — `side-lost`, `file-absent`, `file-unreadable` — and every `whole-file`
placement produce no marker, and History is their only presentation; a
`review-scope` placement likewise produces no marker. Before a renderer replaces or disposes row DOM,
it explicitly closes only the Comment input or inline Thread panel anchored inside
that DOM; unfinished input remains saved, and Comment inputs anchored in other Files
remain open. An expanded fold-edge Comment trigger remains visible and its
activation skips the row's fold action.

Marker facts are one memoized derivation indexed by exact rendered
line coordinates from the canonical Snapshot query and new-Thread draft target.
Draft body changes do not publish a marker revision or wake mounted TextDiffGrids.
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

History progressively renders loaded Threads in open, resolved, then deleted
order and shows loaded and total Thread counts. Open Threads appear directly;
Resolved has a labelled visible group; Deleted has a labelled, counted native
disclosure group that starts folded. Every Thread carries the small status dot
for its open, resolved, or deleted state. Its explicit refresh control refetches
the Room Thread query through its ordinary query operation;
it does not introduce a browser activity-delta protocol. Every expanded Thread
labels its retained excerpt with the original selected-side File path and line
range. A Thread without a displayed-Snapshot placement uses its unchanged
origin coordinate for exact navigation. If the same File changed, a text
origin's go-to control has a subdued warning and navigates to the File start
without creating a marker or claiming that location as placement. A whole-File
origin already names that exact File start and gives no reload advice. A missing
or unreadable File disables go-to and explains why. Inline view starts with
History closed in a right-side ChangeSet grid
column mirroring the FileTree rail; opening it expands that same column. The
rail carries a vertical label, Thread count, and `m` hint. Split view uses one
right-side host below the sticky File header; its closed eye-and-`m` control and
expanded overlay occupy that same host. The `m` hotkey and visible panel
controls toggle one ChangeSet-local History state in either mode. The closed
panel stays mounted — the host's open class swaps which of the permanent
toggle-and-panel siblings displays — so closing cannot destroy per-Thread UI
state, warmed Thread heights, or the reading position; only an inline/split
view switch rebuilds the panel, and the tracked scroll position is restored
after it. The panel has
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
first line and a one-line Comment summary beneath it. Wherever History, the inline
Thread panel, or a folded strip names a Comment author, an agent's name is
followed by a quiet label: its model and harness joined by " · ", the one it
reported, or "agent" when it reported neither. A human shows only the name. The
name and label truncate inside the header and the tooltip carries both in full. The go-to-code icon is the
only part of the strip that does not toggle. For a displayed-Snapshot placement,
it expands the Thread, navigates to its rendered marker, and opens that exact
Thread in the code-aligned panel. Origin-bound navigation expands the Thread but
never creates or opens a displayed-code marker or panel. A warning icon with
hover
text replaces a textual outdated label. Go-to is enabled only for an already
loaded full File. A displayed placement or unchanged text origin uses exact-line
navigation to reveal its selected side and first selected line. A changed text
origin instead scrolls to the actual File start without selecting a hunk. It
never loads or admits a lazy File or Husk, and neither path starts scroll-follow.
A Thread whose
File this Snapshot could not offer at all — `file-absent` or
`file-unreadable` — disables the control and states on hover and in its
expanded content which of the two happened to the reviewed File. Every other
placement keeps the enabled control, which the loaded-File rule then governs.
A review-scoped Thread has no File: History labels it "Whole review" in place of
File and line, shows no excerpt, and renders no go-to control. Folding a History row does not navigate.

Unexpected review-presentation derivation failures are contained around the
Comment input, inline Thread panel, and History presentation. The File lane is a
sibling of that boundary and remains mounted; renderer-local failures continue
to stop at their individual File boundary.

## Agent HTTP boundary

The agent API mirrors what the browser does and gates nothing: it is the
browser's Rooms, Snapshots, Threads, Comments, and Profiles, reached with
cheap reads and one write route, so the common loop is a few calls. There is no
session, no role stored on a Profile, no continuation protocol, and no
instrument vocabulary. Nine routes exist; every other `/api/agent` path is
absent.

```text
GET  /api/agent/onboard?<copied HUD tab URL params>
POST /api/agent/profile   {agent_uuid, name, model, harness}
POST /api/agent/capture   {tab}
GET  /api/agent/reviews?project_id=
GET  /api/agent/threads?snapshot_id=&status=open|all&page=&limit=&through_activity_id=
GET  /api/agent/thread/{thread_id}?page=&limit=
GET  /api/agent/activity?project_id=&after=&limit=
GET  /api/agent/wait?project_id=&after=&timeout=
POST /api/agent/actions   {profile_id, actions: [...]}
```

`threads` mirrors the browser's `/api/review/threads` pivot: the response
carries the concrete `through_activity_id` it was read at, the first page omits
the parameter so the server picks the Room's greatest current activity, and
every later page must repeat the returned value, so membership, order, and
total stay fixed while a caller pages (a first page that sends it, or a later
page that omits it, is rejected). Comment pages of `thread/{thread_id}` have no
pivot. Responses are JSON. `limit` defaults to 20 for Thread lists and Comment pages
and to 50 for activity, and is at most 100; pages are one-based and a page past
the end is empty. Every rejected operation returns a non-2xx response whose
plain-text body describes the concrete failure (see the end of this section).

### Onboard

Onboarding is a read-only pre-session operation. The Profile menu copies an
absolute onboarding URL only when the browser URL describes one complete Head,
Refs, Branch Review, or Pull Request Tab; Preset and incomplete Tabs keep that
action disabled. The route takes those same query parameters and returns
`{dirdiff_url, project_id, tab, skill_paths, next_step}`: the running server origin, the
Mark id (`null` for a Pull Request URL, which names no registered repository),
the Tab descriptor accepted unchanged by `capture`, and the absolute paths of
the installed `review-patch`, `round-review`, and `babysit-patch` `SKILL.md`
files, and `next_step`, plain text giving the `dirdiff agent init` command with
the requested URL filled in, which skill fits which role, and that the CLI is the
supported way to use the API. Repository-backed Tabs name the exact path of an active Mark; nothing
marks a path implicitly. The browser's Head selection names no baseline: it
always means the current HEAD. Onboarding therefore fills the descriptor's
`baseline` with the repository's current HEAD commit id (the empty-tree id for
an unborn HEAD), so the `tab=head&project_id=N` URL an agent builds by hand
works the same and yields a descriptor `capture` can use immediately. Onboarding creates no Profile, Room, Snapshot, or
activity.

### Profile

`profile` registers or refreshes one agent. `agent_uuid` is a compact lowercase
or canonical hyphenated UUID, normalized to compact lowercase. The call is
idempotent on that UUID: the first call creates one ordinary `user_profile` with
its one-to-one `agent_profile` binding, and later calls return the same Profile
and replace its name, model, and harness. It returns
`{profile_id, name, model, harness}`. A name already used by a different Profile
is rejected. The Profile carries no role and no permission; any Profile may
perform any action, and its id is the only author identity.

### Capture

`capture` takes `{tab}` where `tab` is the descriptor `onboard` or `reviews`
returned, naming `kind` `head`, `refs`, `branch-review`, or `pull-request` with
the same fields as the browser's Tab. It runs the same preparation and
`corresponding_room` publication as the browser manifest, so the same
descriptor reaches the same Room, and an unchanged capture returns the same
Snapshot id. It returns:

```json
{"project_id": 1, "snapshot_id": "<32 hex>", "tab": {...},
 "snapshot_path": "<dir>",
 "latest_activity_id": 0,
 "files": [{"left_path": "a.txt", "right_path": "a.txt",
            "change_type": "modify|add|delete|rename|copy",
            "capture_error": null,
            "left_file": "<abs path>", "right_file": "<abs path>"}]}
```

`files` is the manifest: one entry per captured File pair with its repository
paths, change type, and the absolute paths of the captured `left` and `right`
bytes (`null` for an absent side). The paths are the existing durable Snapshot
directory published by normal capture; the API creates no copy, hardlink, File
reference, or HTTP File-content operation, and the tree is read-only and never
a worktree. A File whose capture failed stays listed with its `capture_error`;
its captured bytes are dirdiff's placeholder, not source. Public sides hold
materialized content: Git LFS sides hold the verified payload, not the
repository's pointer. Link sides may have private `*-link.json` and `*-target`
files beside them; those are not changed Files and never appear in `files`.
`latest_activity_id` is the newest authored activity of the Mark, ready to pass
as `after` to `activity` or `wait`; 0 means none. `tab` is the descriptor of
the Room the capture landed in, in the same form `reviews` lists.

A Head descriptor is `{"kind": "head", "repo_path": ..., "baseline": <full
commit id>}`; `baseline` is required everywhere. A Head Room is keyed on its
baseline commit, so `capture` with a descriptor naming it selects that Room and
compares the live worktree with that original baseline, also after HEAD has
advanced, and `reviews` lists each Head review with its own baseline. A new
Head review starts from a fresh `onboard`, which names the then-current HEAD.
The browser's Head manifest still always freezes the current HEAD; the agent
capture uses its own selection model that takes the baseline explicitly.

The path-and-bay addressing below needs no search of the Snapshot directory:
`files` maps repository paths to captured bytes directly.

The captured bytes also carry the placement coordinate, so no composed content
crosses the agent boundary. An ordinary text File composes the single
`flatfile` bay spanning the whole File, so its bay lines are the lines of the
side on disk. A notebook cell bay is keyed by the `id` that cell carries in the
`.ipynb`, and its lines are the lines of that cell's joined `source`. Bays whose
text a renderer produces rather than the File, such as cell outputs, cell
metadata, and notebook metadata, hold no coordinate an agent can derive from
the bytes; review accepts those keys like any other, and the agent boundary
gives an agent no way to count lines inside them. An `image` or `blob` File
composes one bay keyed `image` or `blob`, and the agent addresses its whole
content as the single line `1..1`.

This filesystem contract is taught by
`.agents/skills/review-patch/references/snapshot_structure.md` (manifest, bay
keys, line addressing) and in short form by the `SKILL.md` files of
`round-review` and `babysit-patch`. Any change to Snapshot directory
organization, side filenames, path meaning, mutability, the manifest shape, or
the way agents obtain captured bytes must update those three skills and this
specification in the same change.

### The `dirdiff agent` CLI

Agents do not write HTTP. `dirdiff agent <command>` (`src/dirdiff/cli/_base/agent_commands.py`)
is the supported client of the routes above and the interface the three skills
teach. Every command takes a required `--session PATH` naming that agent's own
JSON file; there is no default path and no environment lookup, so an author and
a reviewer in one directory keep separate state. The file holds the server URL,
the agent UUID (generated by `init`), the Profile id, the role, the Tab
descriptor, the repository id, the latest Snapshot id and directory, and the
activity cursor. The role is taken from it for every `create-thread`, `comment`,
and `reopen`. A session file belongs to one review. A first `init` generates the
agent UUID; joining another review under the same Profile is `init
--identity-from OTHER_SESSION`, which copies the UUID, name, model, and harness
from that file (and rejects `--name`, `--model`, `--harness` beside it), relying
on `profile` being idempotent on the UUID.

| command | route |
| --- | --- |
| `init --onboard-url --role (--name --model --harness \| --identity-from SESSION)` | `onboard`, `capture`, `profile`, `threads` |
| `capture` | `capture` |
| `reviews` | `reviews` |
| `threads [--all]` | `threads` (every page, repeating the returned `through_activity_id`) |
| `show THREAD_ID` | `thread/{id}` (every page) |
| `news`, `wait --timeout S` | `activity`, `wait`; advance the cursor |
| `find --path --side --lines --bay`, `find --review` | `actions` `create-thread` |
| `comment`, `resolve`, `reopen THREAD_ID` | `actions` |
| `edit`, `delete-comment THREAD_ID COMMENT_ID`, `delete-thread THREAD_ID` | `actions` |

Bodies come from `--body-file` or standard input, never from an argument. Output
is compact plain text with repository paths, titles, who a Thread waits on, and
timestamps labelled UTC. `news` and `wait` print only other participants'
events and move the cursor past their own; `wait` keeps holding, within its
`--timeout`, until an event from another participant arrives. A failing call prints the server's
plain-text body to standard error and exits non-zero; nothing is retried.

### Reviews

`reviews?project_id=` lists the Rooms of one Mark that have a Snapshot, in Room
creation order, as `{reviews: [{tab, snapshot_id, open_threads,
waiting_on_author, waiting_on_reviewer}]}`. `tab` is a descriptor accepted
unchanged by `capture`, rebuilt from the Room's persisted selection;
`snapshot_id` is the Room's newest Snapshot. A Thread whose latest state came
from a browser action waits on `both` and counts toward each role. Preset Rooms
belong to no Mark and never appear.

### Threads and Thread

`threads?snapshot_id=&status=` lists the Threads of the whole Room containing
the Snapshot, as the browser History does, so any Snapshot id of the Room
works and an older id still lists every Thread. `status` is required: `open` lists
open Threads and `all` adds resolved and deleted Threads. Each item is
`{thread_id, status, waiting_on, title, location, outdated_reason,
comment_count, first_comment, latest_comment, latest_activity_id}`, where
`latest_activity_id` is the Thread's greatest activity at or before the page's
`through_activity_id`. `status` is
`open|resolved|deleted` and `waiting_on` is `author|reviewer|both|none`.
`title` is the first line of the first Comment cut to 120 characters, and
`null` when that Comment was deleted. `location` is read through the given
Snapshot: `{path, side, bay_key, start_line, end_line}` with a repository path
and the bay-local inclusive one-based lines, or `null` for a review-scoped
Thread. A kept or relocated range reports its landed lines; every other landing
reports the original lines, a lost bay names its replacement bay, and a
historical File-level Thread has a null `bay_key` and no lines.
`outdated_reason` is null when the original lines are intact, otherwise one of
`region_changed`, `region_not_found`, `bay_not_found`, `file_missing`, or
`file_unreadable`. Previews are `{body, deleted, truncated}` with at most 256
Unicode characters, the first 255 and `…` when truncated. The wrapper is
`{items, page, limit, total, has_more}`.

`thread/{thread_id}` reads one Thread by its id alone, in any state, with no
Snapshot. It returns `{thread_id, snapshot_id, status, waiting_on, location,
original_excerpt, comments, page, limit, total_comments, has_more}`:
`snapshot_id` is the origin Snapshot, and `location` and `original_excerpt`
describe the Thread as created there. `original_excerpt` is the bounded
selected-line context, null for a review-scoped or historical File-level
Thread. Comments are in creation order, tombstones included, each as
`{comment_id, author, body, deleted, revision, created_at, updated_at}` with
`author` as `{profile_id, name, is_agent, model, harness}`; complete Comments
and present excerpts are never truncated.

### Activity and wait

`activity?project_id=&after=&limit=` returns the authored actions newer than the
cursor `after` across every Room of the Mark, in increasing `activity_id`, as
`{events, next_after, has_more}`. Each event is self-contained:
`{activity_id, created_at, tab, thread_id, title, location, action, author,
comment, status, waiting_on}`, where `action` is the action kind, `comment` is
the created or changed Comment `{comment_id, author, body}` or null, and
`status` and `waiting_on` are the Thread's state after that action. Pass
`next_after` as `after` in the next call; with no events it equals `after`.
Placement changes and code changes are not activity.

`wait?project_id=&after=&timeout=` returns the same response, but when nothing
newer than `after` exists it waits up to `timeout` seconds (1 to 540) and
returns an empty `events` list if time runs out. It polls the newest activity id
without holding a worker thread. A caller that gets an empty result simply calls
again with the same cursor.

### Actions

`actions` takes `{profile_id, actions}`: one Profile and one to 100 ordered
actions, each with a `kind`:

| kind | fields | effect |
| --- | --- | --- |
| `create-thread` | `snapshot_id`, `role`, `body`, optional `path`, `side`, `bay` | new Thread, first Comment |
| `comment` | `thread_id`, `role`, `body` | append a Comment |
| `reopen` | `thread_id`, `role`, `body` | resolved to open with a Comment |
| `resolve` | `thread_id` | open to resolved |
| `edit-comment` | `thread_id`, `comment_id`, `body` | revise own Comment |
| `delete-comment` | `thread_id`, `comment_id` | tombstone a Comment |
| `delete-thread` | `thread_id` | delete the Thread |

`role` is `author` or `reviewer`, is required on `create-thread`, `comment`,
and `reopen`, and states which side of the conversation the acting Profile
speaks for in that action only; it is never stored on the Profile. After such
an action the Thread waits on the other role. After `resolve` and
`delete-thread` it waits on nobody (`none`); `edit-comment` and
`delete-comment` leave the wait unchanged. The same legal transitions as the
browser apply: resolved Threads accept Comments, deleted Threads accept
nothing, only a Comment's author may edit it, and any Profile may tombstone a
Comment or delete a Thread.

`create-thread` sends `path`, `side`, and `bay` together or not at all. Together
they make a code Thread: `path` is the repository-relative path of a File in
the named Snapshot, `side` is `left` or `right` and must name a present side of
that File, and `bay` is `{bay_key, start_line, end_line}` with the public bay
key the File composes and an inclusive one-based range local to that bay. Bay
keys the File does not compose are rejected; the boundary assumes no key of its
own. A non-text bay accepts only `1..1`, and any wider or later range is
rejected rather than clamped. Sent with none of them it makes a review-scoped
Thread. Thread ids are generated by the server, so one batch cannot address a
Thread it creates.

Every action of a batch must address the same Room. An action on an existing
Thread is recorded against that Thread's origin Snapshot, so no Snapshot id is
sent. Each action is validated against the state the earlier ones produce, and
the complete array is appended under the Room write lock in one database
transaction; any invalid item discards the whole batch. When several items are
invalid, which item's error is reported is unspecified. The response is `{results: [{kind,
thread_id, comment_id, activity_id, status, waiting_on}]}` in input order, with
`comment_id` null for `resolve` and `delete-thread`. Re-sending a request
simply performs its actions again; the agent boundary has no submission
identity, retry, replay, checkpoint, or recovery contract.

Every rejected agent HTTP operation returns a non-2xx response whose
plain-text body describes the concrete validation, HTTP, or domain failure.
Unexpected failures remain ordinary internal-server errors and do not expose
tracebacks.
