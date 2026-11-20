# Persistent review Threads

## Model

One `thread_id` identifies a live discussion. One `review_thread` row records
its origin and one `review_thread_placement` row binds it to each captured
Snapshot, keyed `(snapshot_id, thread_id)`. Rows sharing a `thread_id` keep
independent immutable placements. A Snapshot fixes captured code;
`through_activity_id` fixes the discussion outcome a read sees.

History is the HUD over every Thread of one Room. It is not a relation, a
placement kind, or a separate read. The displayed Snapshot supplies placement
context but never filters membership: located and unlocated Threads, including
resolved and deleted ones, stay in the same result.

Review stores the public composed bay key and never interprets it, so no format
has a target shape of its own. Room does not interpret private source
coordinates.

## Targets and placement

- A review-scoped Thread has origin `review` and placement `review-scope`: no
  File, bay, range, side, digest, or private coordinate. Derivation copies its
  placement into every later Snapshot unchanged, so it is never outdated,
  relocated, missing, or unreadable.
- An `image` or `blob` bay holds no lines; a target against one names the single
  line `1..1`, and every other range is refused rather than clamped. That line
  is reconstructed from the media side's own facts (type, size, digest), so
  replacing the content changes the line and the ordinary origin machinery
  reports the Thread outdated.
- A placement for an existing File references its `snapshot_file` row. If the
  exact File pair is absent the placement has no reference (`file_missing`); a
  present File whose capture failed is also deliberately unreferenced
  (`file_unreadable`). Any other present File with a missing reference is an
  invariant violation.
- Historical File-level Threads keep a `file-start` origin with no range and no
  excerpt; reads never fabricate one. Their placements are `file-start` with no
  outdated reason and never gain a bay landing.
- Bay identity is public and durable. A notebook cell key is the notebook's own
  cell id, so an edited or moved cell keeps it. An id-less cell derives a key
  from the SHA-256 of its source plus its zero-based occurrence among cells with
  the same source; an edit changes the hash and follows ordinary `bay_not_found`
  placement. Validation calls `bays()` rather than re-deriving what the
  renderer shows, with a `BayContext` that has no renderer, so no diff engine is
  in reach.
- Private source coordinates exist only in the text origin row, are decoded only
  by the Thread implementation, and never cross the HTTP boundary. Exactly one
  matching content hash wins among duplicate structural candidates; one changed
  candidate is identifiable; zero or multiple are not.

A text-origin placement is stored in one of these landings, each published as
one browser placement kind:

```text
unchanged unique region -> relocated range                    -> region-kept
unique changed region   -> first line of region,
                           region_changed                     -> region-changed
region not identified   -> start of the origin's bay,
                           region_not_found                   -> region-lost
bay no longer composed  -> start of the File's first bay
                           carrying the origin side,
                           bay_not_found                      -> bay-lost
   ... where no composed bay carries that side, File
       start, bay_not_found                                   -> side-lost
File not captured       -> no code, file_unreadable           -> file-unreadable
exact File absent       -> no code, file_missing              -> file-absent
```

`whole-file` and `review-scope` are the remaining two kinds. The stored
`bay_not_found` covers two landings; the browser names them apart because
`bay-lost` has a bay to mark and navigate to and `side-lost` has none.

`file_unreadable` means the capture directory holds dirdiff's placeholder text
instead of the File's bytes, so every coordinate it could offer would describe
text dirdiff wrote. The Thread therefore lands with no code location.
Derivation contains the failure at that one placement (the Snapshot still lists
every other Thread) and logs the captured `error`, which no placement field
carries.

Derivation chooses and stores every landing, including the first-bay one, while
it already holds the target File's composed bays. Reads publish stored fields
verbatim: listing Threads reads no captured bytes and performs no composition.

## Original context

Original code is not copied into review persistence. A text origin references
its immutable Snapshot File, and `Thread.discussion()` reconstructs the excerpt
from the named bay's own text (already decoded by composition): the complete
selected range plus at most three lines on each side. Creation performs the
same reconstruction before the origin and first action are written, so an
accepted target cannot leave a discussion that later reads cannot render. A
File that composes no `flatfile` bay accepts no flatfile target. Excerpt
construction calls no diff engine. `region_changed` returns the current matched
range and its outdated reason; current File content is read explicitly.

## Discussion lifecycle

- Every author is a durable ordinary Profile with a globally unique username.
  Agent Profiles have the same author shape; `agent_profile` adds the agent UUID
  binding and the model and harness last reported (NULL only for agents
  registered before they were recorded). Reads need no Profile; every write
  names one existing `profile_id`. Display names come from the current Profile
  row, so renaming changes later reads without rewriting actions.
- `review_action` is append-only. Its first row creates the first Comment at
  sequence zero; later rows create, edit, or tombstone Comments, or resolve,
  reopen, or delete the Thread. Folding sequence order yields current Comments,
  Comment revisions, Thread state, and Thread revision. Creation time is the
  sequence-zero timestamp. Every valid Thread has at least that Comment.
- Every action persists `status_after` and `attention_after`; lifecycle and
  attention are read from the latest persisted outcome, Comments are folded.
- Transitions: `open -> resolved`, `resolved -> open`, `open | resolved ->
  deleted`. Replies and Comment changes remain valid while resolved. Deleted
  Threads reject every operation. Only a Comment's author may edit it; any valid
  Profile may tombstone a Comment or delete a Thread, attributed to the acting
  Profile. Tombstones stay body-less.
- The browser supplies no operation, Thread, Comment, or revision identifiers.
  The backend generates them, reads the current revision under the write lock,
  validates the transition, and records the accepted revision. Repeating an
  HTTP action performs it again; there is no retry or replay protocol.

## Relational invariants

- `review_action.profile_id` is non-null and references `user_profile.id`.
- `activity_id` is a durable, database-wide increasing order, the cursor of the
  agent `activity` and `wait` reads across every Room of a Mark. There is no
  separate event, submission, delta, checkpoint, or agent-authorship action
  variant.
- There is at most one origin per `thread_id`; creation writes it with the first
  action in one transaction. Action sequence is unique and contiguous per
  Thread. Comment and internal operation ids are globally unique.
- Review reads reject incomplete origin, placement, Profile, or File references
  instead of repairing or substituting data.
- Per-Thread sequence, author authorization, transition legality, and revision
  checks are cross-row invariants that SQL does not enforce.
- Enum vocabularies are validated in Python; SQL keeps identifier shapes,
  non-empty text, ordinal ranges, presence pairings, foreign keys, and
  uniqueness.

## Room reads and writes

- Snapshot-scope `threads` requires a selected placement (agent reads); Room
  scope includes every discussion (browser History). A Room Thread without a
  selected placement uses its real origin placement for discussion writes and
  reports the captured relation between the origin File and the displayed File.
  A write validates and appends under the Room publication lock.

## Browser HTTP boundary

- The displayed Snapshot is the Room and placement context. Browser membership
  is every Thread whose origin belongs to the Snapshot's Room. A selected
  placement is returned when it exists; otherwise the real origin placement,
  its origin Snapshot id for follow-up actions, and whether the same captured
  File is unchanged, changed, missing, or unreadable in the displayed
  Snapshot. An origin placement produces no displayed-code marker.
- Paging: the first page passes no pivot; the persistence read picks the
  greatest current Room activity in the same database session and returns it as
  `through_activity_id`; every later page repeats it. Membership, lifecycle
  ordering, count, and hydrated actions include only actions at or before the
  pivot, so concurrent writes wait for the next refetch instead of moving a
  Thread between pages. Ordering (open, resolved, deleted) and page bounds are
  applied in SQLite before hydration. The canonical browser query publishes page
  one immediately and appends later pages under its retained boundary.
- The discussion revision is the accepted action's per-Thread sequence, so a
  contiguous action result needs no follow-up read. Existing-Thread writes
  return only current state and the one changed Comment.
- A returned Thread states its immutable creation target once as
  `origin_target`, with the reconstructed excerpt, and either the displayed
  Snapshot's placement or its real origin placement; `location_source`
  distinguishes them. The origin is the only place the File pair, bay, and side
  appear; the placement adds only what the origin cannot say. No field pairing
  crosses the two.
- No History endpoint exists. Expected review failures return a direct
  `{code, message}` body using the stable review error enum. The browser turns
  them into `ReviewRequestError`; callers classify its `code` and never
  interpret `message` prose.
- Immediate writes target a bounded response within 50 ms. Slower measurements
  are inspected for avoidable work, not hidden by the frontend.

## Browser HUD

Ownership and lifetime:

- `App` holds the selected Profile and passes it to the Snapshot-bound
  `ReviewProvider`; review adds no second identity storage and never invents an
  anonymous author. With no Profile, Threads stay readable.
- `ReviewDraftRoot` (mounted once by `main.tsx`) lives for the application
  lifetime and is the sole Solid and localStorage representation of drafts;
  consumers touch drafts only through `useReviewDrafts`. Empty Comment input is
  not a draft: it lives only in the open input.
- `ReviewProvider` lives for one Snapshot, outside engine-keyed File rendering.
  The pages of the canonical query, their total, and their activity boundary
  stay immutable. Successful writes publish their responses as post-boundary
  activity beside the pages; presentation applies contiguous updates by
  discussion revision and ignores an activity once a query result holds an
  equal or newer revision. Query Threads are never copied into another store.
- `ReviewBinding` is the narrow renderer interface: renderers neither perform
  review HTTP operations nor store Thread data. `threadViews.tsx` is pure
  presentation and originates no query, mutation, draft write, or navigation.
- Before page one arrives, or after the progressive query fails, code markers
  and File actions are disabled rather than reading unavailable Threads as an
  empty set.

Drafts:

- A new-Thread draft retains its draft ID, Snapshot, Profile, public target, and
  body, and stays bound to its original Snapshot. Reply and edit drafts retain
  only draft ID, Thread ID (and Comment ID for edit), Profile, and body; the
  loaded Thread supplies current placement. No draft copies source excerpts or
  Thread data.
- Submission disables that draft for one HTTP action; success removes it,
  failure leaves the editable draft. No action keeps replay state.
- Renderer detachment preserves the draft. Before a renderer replaces or
  disposes row DOM it closes only the Comment input or inline Thread panel
  anchored inside that DOM.

Markers:

- `region-kept` and `region-changed` mark their stored bay-local line;
  `region-lost` and `bay-lost` mark line one of the bay they name. These are
  coordinates the backend stored: no read-time reconstruction and no
  frontend-minted bay key. Every other kind produces no marker; History is its
  only presentation.
- Marker facts are one memoized derivation indexed by exact rendered line
  coordinates from the canonical query and new-Thread draft target; it is never
  a writable review store. Draft body changes do not publish a marker revision
  or wake mounted TextDiffGrids.
- A line-marker failure disables and clears only the affected grid's review
  decorations for the grid's lifetime and shows a local error beside its intact
  rows.
- Unexpected derivation failures are contained around the Comment input, inline
  Thread panel, and History; the File lane is a sibling of that boundary and
  stays mounted.

History:

- The closed panel stays mounted, so closing cannot destroy per-Thread UI
  state, warmed Thread heights, or the reading position.
- Go-to never loads or admits a lazy File or Husk, never selects a hunk, and
  starts no scroll-follow.

## Agent HTTP boundary

The agent API mirrors the browser and gates nothing: no session, no role stored
on a Profile, no continuation protocol. Agents reach it through the
`dirdiff agent` commands, never through hand-written HTTP.

- `threads` repeats the browser pivot: the first page omits
  `through_activity_id`, every later page must repeat the returned value.
  `threads` lists the whole Room containing the Snapshot.
- `onboard` is read-only and creates no Profile, Room, Snapshot, or activity.
  Nothing marks a path implicitly.
- `profile` is idempotent on the agent UUID and carries no role or permission;
  its id is the only author identity.
- `capture` points at the existing durable Snapshot directory; the API creates
  no copy, hardlink, or File-content operation, and the tree is read-only and
  never a worktree. Captured bytes carry the placement coordinate, so no
  composed content crosses the agent boundary.
- `activity_id` orders `activity` and `wait` across every Room of a Mark;
  placement and code changes are not activity.
- `role` on an action states which side the Profile speaks for in that action
  only and is never stored. After `create-thread`, `comment`, or `reopen` the
  Thread waits on the other role; after `resolve` and `delete-thread` on
  nobody; Comment edits and deletions leave it unchanged. A Thread whose latest
  state came from a browser action waits on `both`.
- A batch addresses one Room. An action on an existing Thread is recorded
  against that Thread's origin Snapshot. Each action is validated against the
  state the earlier ones produce, and the whole array is appended under the Room
  write lock in one transaction; any invalid item discards the batch. Thread
  ids are server-generated, so a batch cannot address a Thread it creates.
  There is no submission identity, retry, replay, checkpoint, or recovery
  contract.
