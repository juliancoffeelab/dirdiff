# Comment Threads

A Snapshot fixes captured code; `through_activity_id` fixes the discussion
outcome a read sees. Placement derivation and its ordering with publication
are in [`rooms.md`](rooms.md#publication); target validation is in
[`formats.md`](formats.md#review-and-media).

## Placement

- An `image` or `blob` bay holds no lines; a target against one names the single
  line `1..1`, and every other range is refused rather than clamped. That line
  is reconstructed from the media side's own facts (type, size, digest), so
  replacing the content changes the line and the ordinary origin machinery
  reports the Thread outdated.
- A File whose capture failed lands its Threads as `file_unreadable`, with no
  code location: its capture directory holds dirdiff's placeholder text, so
  every coordinate it could offer would describe text dirdiff wrote.
  Derivation contains the failure at that one placement and logs the captured
  error.
- A whole-review Thread has a placement in every Snapshot of its Room:
  creation writes one into each existing Snapshot and capture derives one into
  each new Snapshot, both under the Room write lock. Every Snapshot can
  therefore read and write it as `displayed-snapshot`; Comment actions
  reference a placement, so an earlier Snapshot without one could not.
- The stored `bay_not_found` covers two browser kinds: `bay-lost` has a bay to
  mark and navigate to, `side-lost` has none.
- Derivation chooses and stores every landing while it already holds the
  target File's composed bays. Reads publish stored fields verbatim: listing
  Threads reads no captured bytes and performs no composition.
- Private source coordinates exist only in the text origin row, are decoded only
  by the Thread implementation, and never cross the HTTP, frontend, draft or
  cache boundaries.

## Original context

Original code is not copied into review persistence. A text origin references
its immutable Snapshot File, and `Thread.discussion()` reconstructs the excerpt
from the named bay's own text. Creation performs the same reconstruction before
the origin and first action are written, so an accepted target cannot leave a
discussion that later reads cannot render.

## Reads and writes

- Paging: the first page passes no pivot; the read picks the greatest current
  Room activity in the same database session and returns it as
  `through_activity_id`, and every later page repeats it. Everything a page
  includes is limited to actions at or before the pivot, so concurrent writes
  wait for the next refetch instead of moving a Thread between pages. The agent
  `threads` read follows the same rule.
- The browser supplies no operation, Thread, Comment, or revision identifiers;
  repeating an HTTP action performs it again. There is no retry or replay
  protocol, in the browser or the agent API.
- Immediate writes target a bounded response within 50 ms. Slower measurements
  are inspected for avoidable work, not hidden by the frontend.

## Browser HUD

- `ReviewProvider` lives for one Snapshot, outside engine-keyed File rendering.
  The pages of the canonical query stay immutable; successful writes publish
  their responses as activity beside the pages, applied by discussion revision
  and ignored once a query result holds an equal or newer one. Query Threads
  are never copied into another store.
- Before page one arrives, or after the query fails, code markers and File
  actions are disabled rather than reading unavailable Threads as an empty set.
- Marker facts are one memoized derivation from the canonical query and the
  new-Thread draft target, never a writable review store. Markers use only
  coordinates the backend stored; the frontend mints no bay key.
- Open History covers the code without removing its layout: a change set out
  of layout clamps the document scroll position, turns bays virtual and fails
  the text grid's finite-height assert. The covered section is `inert`,
  because focusing a control scrolls the document even while its scroller is
  locked. A shell with no Snapshot has no History, cannot toggle it and is
  never inert; a failure inside the review boundary closes History before its
  panel shows.
- A History action that lands in code closes History first, then navigates.
- A Comment body is built from its parsed Markdown tree, never from HTML, so
  raw HTML stays text and rendering fetches no URL in a body; a node outside
  the allow-list shows its source text rather than vanishing.
- The History panel has one host for both code views and stays mounted while
  closed, so neither a view switch nor closing can destroy per-Thread UI
  state, warmed Thread heights, or the reading position.
- Unexpected failures are contained around the Comment input, inline Thread
  panel, and History; the File lane is a sibling of that boundary and stays
  mounted. A line-marker failure disables only the affected grid's review
  decorations, for that grid's lifetime.
