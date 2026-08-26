# Backend modules

## Purpose

This document maps the Python modules and the responsibilities between them.

## Application flow

```text
CLI
 └─ server
     ├─ backend
     ├─ db
     ├─ engines
     ├─ formats
     └─ rendering

formats ──► backend contracts
        ├─► engines
        └─► rendering

rendering ──► engines
backend ──► engine contracts
```

Dependencies do not point from engines into rendering.

## `dirdiff.backend`

Provides repository and preset data.

It resolves refs, lists changed paths and stable File metadata, reports
backend-reported aggregate added/removed line counts when available, loads exact
file contents without decoding them, and prepares pull requests. Pull Request
preparation parses the URL, identifies the marked repository and forge remote,
fetches the required refs, and returns the canonical URL with the merge-base and
Pull Request head commits. Additional
untracked Files need not participate in those backend totals. Loading a listed
File either returns its complete contents or raises `DirdiffError` with the
backend failure reason. Workspace backends do not select Rooms, retain state
between HTTP requests, classify contents for renderers, or invent
renderer-dependent line counts.

Loading stops at bytes. What those bytes are — text, a notebook, an image — is
`dirdiff.formats`' decision, and the definition of what this project calls text
lives there with the classification that asks it.

Its public interface is exported from `dirdiff.backend`.

## `dirdiff.engines`

Compares two already-loaded text sides.

The package defines `DirdiffError`, the shared failure contract used when
dirdiff cannot safely produce a requested result.

An engine produces neutral aligned rows, inline diff tokens, summary counts,
and an engine warning when applicable. It does not load files or attach syntax
highlighting, folds, or hunk indexes. Difftastic keeps its degraded textual
diff construction inside its own implementation.

The concrete engines are difftastic, Git no-index, GumTree, the native
line-first text engine (`textdiff`, engine param `dirdiff`), and the
token-first text engine (`tokendiff`, engine param `tokendiff`). Tokendiff
diffs whole token streams anchored on lstrip-equal lines, so content moving
across line boundaries diffs at word granularity, whitespace is always
diffed, and oversized regions degrade to one-sided rows with an explicit
`tokendiff_region_limit` warning. Its row guarantees are asserted by the
corpus property tests in `tests/tokendiff/`.

The package also owns engine selection. `EngineKind` is the complete set of
engine names, and `engine()` maps one to its renderer; both are exported from
the package root. `engine()` is defined in `__init__.py` rather than `base.py`
because selection must reach the concrete engine classes, whose modules import
their contracts from `base.py`.

Its public interface is exported from `dirdiff.engines`.

## `dirdiff.rendering`

Enriches neutral engine rows for display.

It defines `DiffRow`, `SyntaxClass`, `SyntaxSpan`, `DecoratedPart`, and
`FoldHint`. It combines engine inline tokens with syntax spans into one lossless
part sequence per row side, then attaches fold hints and hunk indexes without
changing the engine's alignment or summary.

Its public interface is exported from `dirdiff.rendering`.

## `dirdiff.formats`

Composes two captured byte sides into one composed diff: File-level metadata
plus an ordered list of frames, each holding an ordered list of bays. It is
the single shape behind `/api/file-diff`; there is no `render_kind`.

`Composer` has two entry points. `bays()` yields every bay a File composes
into, in document order, with nothing an engine produces; it is the engine-free
lookup review validation and the media endpoint call. `compose()`
consumes that stream, renders each text bay through the shared text-bay
renderer, reduces each image bay to its two `MediaRef` descriptions, aggregates the
summary, and returns the envelope minus the two fields
the HTTP boundary attaches (`display_name`, `file_kind`). It assigns no hunk
numbering: rows keep the bay-local boundaries enrichment gave them, and the
frontend turns those into the File's navigable sequence. `base.py` holds those
contracts (contexts, the text-bay renderer, the serialized shapes);
`composer.py`
holds the class and the ordered classification; per-format sibling builders live
beside them.

Classification is an ordered check, and it is total — every File reaches an
answer, so none arrives at the frontend as an error where a diff was expected.
A `.ipynb` whose bytes load as notebook JSON
composes through `notebook.py` into one frame per cell — every cell, so the
notebook reads as the document it is — plus a `notebook:metadata` frame. A File
whose every captured side's path names one of the image types the browser
displays natively composes through `image.py` into one heading-less frame
holding two bays: an `image` bay keyed `image`, which is the frame's body, and
an `image-facts` text bay beside it, open by default and collapsible;
classification there
is by filename extension, which is what the repository asserts the file is. A
File whose every captured side decodes as text composes as one heading-less
frame holding one `flatfile` text bay, which is also where a `.ipynb` that does
not load and an `.svg` land. Everything left composes through `blob.py` into
one `text` bay keyed `blob`, which is that frame's body: a blob is a
classification, not a bay kind, and what it composes is a diff of the facts.

The facts are the same three lines in both builders — `type:`, `size:`, and
`sha256:`, one per line — and the ordinary engine diffs them, so a reviewer
reads the type, size, and lowercase-hex digest changing line by line and can
comment on any of them. Those lines are real lines: they count toward the File
summary the way any other bay's do.

`image.py` produces an `ImageBay`, which holds two optional sides of exact
captured bytes. `compose()` never serializes those bytes: each present side
becomes a `MediaRef` of media type, byte size, and lowercase-hex SHA-256, and
the bytes themselves are served only by `/api/file-media`. An image bay's
`change` is read from the bytes, so two byte-identical sides are `unchanged`
and take no navigation stop.

`dirdiff.formats.notebook` owns everything notebook-shaped: parsing, cell
pairing, public cell keys, and each bay's content. A cell's public key is its
`nbformat` id, which makes the key durable identity — that is what lets review
store a bay key and nothing else.

`text_content_or_none()` is the single definition of what this project calls
text — no NUL byte, and valid UTF-8 with an optional BOM — and it lives here
because classification is the only thing that asks. It is a question rather than
a boundary: "these bytes are not text" selects the blob terminal instead of
failing, and the value it returns is the text the flatfile builder renders, so a
File is decoded once.



## `dirdiff.db`

Persists repository marks, profiles, preferences, repository defaults, Rooms,
Snapshots, Snapshot metadata, Files, and present left/right File sides in
SQLite. Snapshot metadata retains human labels and nullable aggregate
added/removed counts. A File records its absolute capture-directory path,
tracked provenance, backend change classification, and an optional capture
error. Separate non-null side rows record repository paths and content digests;
optional relations record a lazy override and its complete source metadata when
present. Review persistence adds one placement row per Thread/Snapshot pair and
append-only actions authored through one existing ordinary Profile. The
shared internal Profile table contract lets Profile and Room persistence
reference the same identity. Profile usernames are globally unique and select
one existing local identity without credentials. Agent registration adds a one-to-one UUID binding
without changing that Profile shape. `db.room` contains these relations,
immutable records, and transactions through `RoomStore`.

Its public interface is exported from `dirdiff.db`.

## `dirdiff.room_lord`

Exposes `RoomLord` and `Room`. `RoomLord.corresponding_room` applies a Tab's law
for the shared manifest/agent-review capture and returns the Room and current
Snapshot key separately;
`RoomLord.find_room` recovers a Room from an existing Snapshot key for follow-up
operations. RoomLord does nothing beyond handing out Rooms. Room methods
require that key explicitly while providing metadata, manifested filepath
iteration, direct captured-file lookup, and Thread access through `threads`,
`get_thread`, `thread_for_comment`, and `create_thread`; Comment and lifecycle
writes belong to the returned bound Threads. The agent boundary also uses the
Room's explicit activity reads, atomic review batch, persisted-Tab
`capture_context` and `recapture`, and read-only `path_for_snapshot` access to the
already published Snapshot directory. Capture and
publication stores remain private. The module calls `dirdiff.db` and does not
issue SQL.

The Room and Snapshot lifecycle is described in
[`rooms.md`](rooms.md).

## `dirdiff.review`

Implements persistent review discussions. Its public `Thread` is bound to one
exact `(snapshot_id, thread_id)` pair and performs Comment and lifecycle
operations through that placement, returning each write's bounded update view.
The Thread is a lightweight handle: it loads its referenced Snapshot Files only
when a read interprets placement, so writes never pay for File hydration. It
selects the latest persisted lifecycle and attention outcome, folds Comment
content, and reconstructs original code context as a bounded selected-side
excerpt from immutable captured Files, independently of every rendering engine.

The module privately derives missing placements only for a genuinely new
Snapshot; selecting an equal retained Snapshot performs no derivation. Private
source coordinates do not cross Room, HTTP, frontend, draft, or cache
boundaries. Public matching outcomes are limited to `region_changed`,
`region_not_found`, and `file_missing`. The review model is described in
[`reviews.md`](reviews.md).

## `dirdiff.server`

Defines FastAPI routes and request-level rendering orchestration.

It validates HTTP inputs and outputs, constructs the concrete workspace backend,
calls `RoomLord` for manifests and follow-up Snapshot lookup, asks
`dirdiff.engines` for the renderer a request names, and builds one composed diff
per File through `dirdiff.formats`, attaching the manifest's display name and
file kind to the returned envelope. Snapshot-keyed browser review
routes read one bounded Thread page and apply Profile-authored Thread and
Comment actions through the Room's bound Threads. Existing-Thread writes
return only current state and the changed Comment.
Agent review routes register an ordinary Profile with its agent UUID, capture a
logical Tab into the same Snapshot identity, expose captured changed Files on
disk, page open Threads, recapture the persisted Tab with File and authored
Thread deltas, role-filtered attention inboxes, and one atomic batch of the
role-specific review instruments.
The agent-visible Snapshot filesystem contract is documented in
[`reviews.md`](reviews.md) and mirrored by both project-local agent skills;
changes to capture layout must update those operational references together.
Browser review failures expose stable structured codes and messages.
Profile routes explicitly select an existing exact username, create a unique
username, or rename one Profile to another unique username.
Unexpected HTTP failures are logged with their method, path, and traceback at
the application boundary before the generic internal-error response is sent.
`/api/file-media` serves one captured image side as the exact Snapshot bytes,
addressed by Snapshot id, side, and the same nullable File-path pair
`/api/file-diff` uses, because a renamed image is unaddressable by one path
alone. It recovers the File through the Room, asks `bays()` which image bay the
File composes into, and writes that side's bytes under the media type composition
concluded — so no engine runs to serve a picture and no second opinion about
the media type is formed at the boundary. A File that composes no image bay —
a blob among them, since its bytes are stated as facts rather than shown — and
a side that was never captured are both refused rather than answered with empty
bytes. Snapshot ids are never reused, so the response is declared immutable and
cacheable outright.
`/api/manifest` receives the
complete selected Tab parameters, shows that state, and provides Snapshot/File
keys; it performs no Pull Request preparation. `/api/pull-request/prepare` is the
sole HTTP boundary for Pull Request preparation.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, builds runtime configuration, starts the server, opens
the browser, and exposes repository-mark commands.
