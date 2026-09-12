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

formats ──► engines
        └─► rendering

rendering ──► engines
backend ──► engine contracts
```

Dependencies do not point from engines into rendering.

## `dirdiff.backend`

Provides repository and preset data.

It resolves refs, lists changed paths and stable File metadata including each
present side's Git-compatible mode, reports
backend-reported aggregate added/removed line counts when available, loads exact
materialized file contents without decoding their format, and prepares pull requests. Pull Request
preparation parses the URL, identifies the marked repository and forge remote,
retains the forge's recorded target and head SHAs, and fetches those object ids
directly without moving tracking refs. Content-addressed local refs retain every
prepared revision against Git garbage collection, including after a refresh.
Preparation computes the recorded commits' merge base locally, never selecting
sides from tracking refs after fetching. Missing objects, unavailable forge
metadata, and missing history fail explicitly. The result includes the URL,
exact merge-base and head commits, and observed branch names for HUD display.
Additional untracked Files need not participate in those backend totals. Loading a listed
File either returns its complete contents or raises `DirdiffError` with the
backend failure reason. Workspace backends do not select Rooms, retain state
between HTTP requests, classify contents for renderers, or invent
renderer-dependent line counts.

Git loading expands canonical LFS v1 SHA-256 pointers from local storage,
including shared worktree storage and configured `lfs.storage`. It bounds reads
and verifies payload size, trusting the local LFS store's content-addressed
objects without recomputing their digest. Missing or wrong-sized objects and
recognized unsupported versions or transformation extensions use the ordinary
backend loading error. Nonmatching pointer-like text stays ordinary content.
Missing objects include a manual fetch hint. No download or filter runs.
Single and batch loading share this behavior. Size inspection reads small
candidate pointers to report payload size without loading that payload.

Loading stops at bytes. A link loads as its raw target bytes, and the backend can
report the mode and materialized size, then load any normalized
repository-local path reached while Snapshot capture walks that link. What
final bytes are — text, a notebook, an image — is `dirdiff.formats`' decision,
and the definition of what this project calls text lives there with the
classification that asks it.

Its public interface is exported from `dirdiff.backend`.

## `dirdiff.engines`

Compares two already-loaded text sides.

The package defines `DirdiffError`, the shared failure contract used when
dirdiff cannot safely produce a requested result.

An engine produces aligned rows, inline diff tokens with any structural move
endpoints, summary counts, and an engine warning when applicable. It does not load files or attach syntax
highlighting, folds, or hunk indexes. Difftastic keeps its degraded textual
diff construction inside its own implementation.

Git no-index accepts Myers, Minimal, Patience, or Histogram. Myers is its
declared default. Each Git invocation passes `--diff-algorithm` explicitly;
algorithm selection changes line alignment without changing subsequent decoration.
Its `ignore_space_change` boolean adds `--ignore-space-change` when enabled.
Git then ignores trailing whitespace and changes in the amount of existing
whitespace. Adding whitespace where there was none still counts as a change.
Equal rows retain both original source spellings, including ignored spacing in
patch context and omitted gaps.

The concrete engines are difftastic, Git no-index, GumTree, Diffsitter, the
legacy line-first prototype (`textdiff`, engine param `dirdiff`), and the
next-generation token-first prototype (`tokendiff`, engine param `tokendiff`).
Tokendiff
diffs whole token streams anchored on lstrip-equal lines, so content moving
across line boundaries diffs at word granularity, whitespace is always
diffed, and oversized regions degrade to one-sided rows with an explicit
`tokendiff_region_limit` warning. Its row guarantees are asserted by the
corpus property tests in `tests/tokendiff/`.

GumTree aligns the sequences of unchanged complete lines and pairs changed
intervening lines in source order. It preserves every line exactly once per
side. GumTree actions classify inline changes, and row statuses summarize
those token semantics independently of alignment gaps. Context-only rows are
equal; pure insertions, deletions, and moves retain their kind only without
unchanged non-whitespace context. Mixed rows are replacements. Summary counts
follow these row classifications.
Narrower actions win overlapping status; edits beat equally wide moves.
The innermost containing move supplies
both old and new source spans even when a descendant is edited. Move spans use
one-based lines and zero-based Unicode columns, with an exclusive end.

The adapter converts each generator's external coordinate convention against
the exact supplied source, including tree-sitter-ng's mixed UTF-16 line starts
and UTF-8 columns and its normalized line breaks. Ambiguous Unicode offsets produce local unified-diff rows with a
`gumtree_ambiguous_offsets` warning explaining why structural rendering failed.
Invalid ranges, unknown actions, and contradictory matches fail explicitly.
A missing language generator builds local `difflib.unified_diff` rows with a visible
`gumtree_unsupported_language` warning. Parser errors, missing executables,
and malformed JSON remain failures. GumTree's logic module contains row
construction and is the sole consumer of its private `_logic` driver package.
The package facade re-exports the engine and raw-fact interface.

Diffsitter runs the `diffsitter` executable with `--no-config --renderer
json` over temporary files named after the path hints, which pass through
unchanged, mixed or absent. `DIRDIFF_DIFFSITTER_BIN` selects an exact
executable and a missing configured file is an error; otherwise `diffsitter`
comes from `PATH`. Diffsitter reports only the graphemes of each side without
a match on the other side; it reports no alignment, pairing, replacement, or
move. The adapter therefore maps every `Old` entry to one `delete` token and
every `New` entry to one `insert` token in reported order, fills the gaps on
mentioned lines with unchanged tokens, and never merges graphemes, infers a
replacement, or reads syntax kinds. Whitespace never appears in a report
because Diffsitter strips it. Physical rows come from a local
`difflib.unified_diff` over `\n`-split lines inside the Diffsitter
implementation, so every line of both sides appears exactly once in order and
differing lines are one-sided rows. Row status follows the Difftastic rule
over the row's tokens: no changed non-whitespace token means equal, a single
kind of change without unchanged non-whitespace context keeps that kind, and
any other mix is a replacement. A one-sided line Diffsitter never mentioned
carries no tokens and stays equal, except a blank line, whose presence is the
change and which takes its side's status. Summary counts follow row statuses.
Column coordinates are UTF-8 byte offsets; the adapter validates every line
index, row, column, and entry text against the exact supplied source and
rejects the echoed documents when they differ from dirdiff's inputs.
Identical texts render as equal rows and an empty or absent side renders as
one-sided rows, both without running the executable or warning. An
unsupported file type builds unified-diff rows with the
`diffsitter_unsupported_language` warning; differing texts without any
reported entry build them with `diffsitter_empty_result`. Missing executables,
other process failures, malformed JSON, and unsatisfiable coordinates are
`DirdiffError`s. Diffsitter's logic module contains row construction and is
the sole consumer of its private `_logic` driver, which contains executable
discovery, temporary files, and raw JSON validation.

Difftastic's `ignore_comments` option passes `--ignore-comments`. An upstream
`unchanged` result with this option uses local line alignment to retain both
source texts as equal rows, including comment-only edits. Other structural
results retain the normal alignment and span validation.

GumTree's `matcher` option passes an explicit `-m` ID: Simple, Classic, Hybrid,
Stable, or Auto. Simple is the initial preference.

The package also defines engine selection. `EngineConfig` is an object-shaped
Pydantic discriminated union keyed by `engine`. Git, Difftastic, and GumTree
variants carry their required options; Dirdiff, Diffsitter, and Tokendiff
variants carry only their engine tag. `engine()` exhaustively maps a validated
configuration to its concrete renderer. Both are exported from the package
root. `engine()` is defined in `__init__.py` rather than `base.py` because
selection must import the concrete engine classes, whose modules depend on
contracts from `base.py`.

Its public interface is exported from `dirdiff.engines`.

The facade also exports `DifftasticJson`, `GumTreeJson`, `DiffsitterJson`
and their `is_difftastic_json()` / `is_gumtree_json()` /
`is_diffsitter_json()` validators for consumers of raw tool facts, including
the test JSON cache. `build_gumtree_rows_from_json()` accepts cached facts
together with exact source texts and filename hints for alignment without
running the executable; `build_diffsitter_rows_from_json()` accepts cached
facts and the exact source texts alone, because Diffsitter's alignment is
local and needs no hints. The validators check the consumed JSON shape without
executable discovery or row construction.

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
`composer.py` holds the class, path-only classification, and symbolic-link
coordination and is the sole caller into `_composer`. That private package
contains independent blob, flat-file, notebook, image, and symbolic-link
modules. `base.py` contains the shared byte-facts bay operation used when
several builders reject readable text. The symbolic-link leaf prepares its
outer bay and normalized target; `composer.py` selects the target builder. No
private builder imports another private builder.

Classification reads paths only and is total. `.ipynb` paths are notebooks,
the image extension table names images, the blob extension table names formats
dirdiff explicitly treats as bytes, and every other path is presumed text. A
two-sided File keeps a specialized classification only when both paths agree;
a mixed rename is presumed text. A presumed text File that does not satisfy the
text contract composes as blob facts with a warning.

Notebook loading preserves valid cells and outputs when a sibling is malformed.
The rejected part becomes canonical JSON in its own text bay with a warning;
damage before a usable cell list exists produces one raw notebook bay. Missing
or duplicate cell ids use source-derived pseudo-cell keys. A display bundle's
valid base64 `image/png` representation is preferred over `text/plain` and
becomes an image bay. An image File composes a picture bay, a Pillow-derived
metadata text bay for dimensions and EXIF, and a byte-facts text bay. Blob Files
compose one text bay keyed `blob` containing their byte facts.

A File with a captured link side composes before ordinary path classification.
Its first text bay contains only the outer link payload. A collapsed second bay
contains safely reached target bytes or a stopped-walk diagnosis: text and blob
facts stay text, images keep the picture, and notebooks flatten to script-like
text without outputs. Nested textual links become `# %% path` sections in that
target document. Link traversal and the repository jail belong to Snapshot
capture, never the composer.

The facts are the same three lines in both builders — `type:`, `size:`, and
`sha256:`, one per line — and the ordinary engine diffs them, so a reviewer
reads the type, size, and lowercase-hex digest changing line by line and can
comment on any of them. Those lines are real lines: they count toward the File
summary the way any other bay's do.

The private image and notebook builders produce `ImageBay` values, which hold two optional
sides of exact media bytes. Whole-File images carry captured bytes; notebook
outputs carry the bytes strictly decoded from their base64 MIME entry.
`compose()` never serializes them: each present side becomes a `MediaRef` of
media type, byte size, and lowercase-hex SHA-256, and the bytes themselves are
served only by `/api/file-media`. Whole-File image change is read from the bytes;
notebook output change remains the raw output entry's semantic change.

The private notebook builder contains everything notebook-shaped: parsing, cell
pairing, public cell keys, and each bay's content. A valid distinct `nbformat`
id is durable identity. An id-less or duplicate-id cell visibly degrades to a
source-derived pseudo key, including an occurrence among identical sources, so
review can still store one unambiguous bay key.

`try_decode_text()` is the single definition of what this project calls text:
no NUL byte, and valid UTF-8 with an optional BOM. It returns decoded text or a
typed rejection naming the invalid byte boundary. The text arm turns rejection
into blob facts and a visible bay warning, and decoded text reaches the engine
without a second decode.



## `dirdiff.db`

Persists repository marks, profiles, preferences, repository defaults, Rooms,
Snapshots, Snapshot metadata, Files, and present left/right File sides in
SQLite. Snapshot metadata retains human labels and nullable aggregate
added/removed counts. A File records its absolute capture-directory path,
tracked provenance, backend change classification, and an optional capture
error. Separate non-null side rows record repository paths and content digests;
an optional per-side symbolic-link relation records the exact metadata and
target-capture paths plus their content digests. Its presence, rather than a
filesystem probe, identifies a link side. Other optional relations record a
lazy override and its complete source metadata when present. Review persistence
adds one placement row per Thread/Snapshot pair and
append-only actions authored through one existing ordinary Profile. The
shared internal Profile table contract lets Profile and Room persistence
reference the same identity. Profile usernames are globally unique and select
one existing local identity without credentials. Agent registration adds a one-to-one UUID binding
without changing that Profile shape. `db.room` contains these relations,
immutable records, and transactions through `RoomStore`.

Profile preferences store the folding policy, Git algorithm and whitespace
policy, Difftastic comment policy, and GumTree matcher in one row per Profile. Reads create initial values only when no
preference row exists; saves replace all values. The HTTP boundary validates the closed algorithm choice.

Its public interface is exported from `dirdiff.db`.

Persistent engine bootstrap requires an explicit Alembic configuration path.
Development passes the canonical checkout file; release passes the migration
resource installed under `dirdiff/db`. Wheel configuration maps the canonical
root migration files into that release resource.

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
already published Snapshot directory. Capture walks symlink sides iteratively,
stops before a repeated normalized path, refuses targets outside the repository,
and asks the backend for final-target size before reading it. Targets through
1 MiB are published beside the raw link side; capture also checks the returned
byte count so a concurrently changed target cannot escape the bound. Larger
targets stop with a visible diagnosis. Capture and publication stores remain
private. `Room.get`
authenticates the raw side and the exact link sidecars named by persistence,
returning parsed link facts with the captured path. The module calls
`dirdiff.db` and does not issue SQL.

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
boundaries. A read publishes each Thread as its immutable origin plus one
placement, and the placement vocabulary is limited to `region-kept`,
`region-changed`, `region-lost`, `bay-lost`, `side-lost`, `file-absent`,
`file-unreadable`, and `whole-file`.
The review model is described in [`reviews.md`](reviews.md).

The package facade exports the review commands, views, bound `Thread`, and
external-agent batch instruments used by `Room` and the HTTP boundaries.
`base.py` contains shared contracts and Room write invariants. `thread.py`
contains ordinary Thread operations and is the sole caller into `_thread`.
The private `placement.py` leaf contains origin construction and later-Snapshot
placement derivation; the independent `external_agent.py` leaf contains batch
instrument and result contracts. `thread.py` coordinates the batch operation
across them. Snapshot publication, SQL, and HTTP serialization remain outside
the package.

## `dirdiff.server`

Defines FastAPI routes and request-level rendering orchestration.

The package facade preserves the application factory, runtime configuration,
response contract, and branch-selection imports used by the CLI and tests.
`base.py` contains startup, shared HTTP, and Snapshot-capture contracts used by
multiple route groups. It is the sole caller into `_base/magic.py`, whose
`ClassRoutes` collector is shared by those groups. `app.py` is the sole caller into
`_app`, whose private modules contain class-local route declarations and the
external-agent, browser-review, repository, diff, and media routes. `app.py`
keeps Profile and preference routes,
the application-wide error boundary, frontend-independent application
composition, and the development and release uvicorn factories. Both factories
share one runtime-construction operation. Development adds the missing-Vite
diagnostic. Release requires and serves the installed HUD entry and asset
directory. The complete installation and launch contract is documented in
[`release.md`](release.md).

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
role-specific review instruments. The pre-session onboarding route translates a
complete supported HUD Tab to the join-review shape and returns the running
server origin with exact installed agent-skill entry paths; it creates no review
state.
The agent-visible Snapshot filesystem contract is documented in
[`reviews.md`](reviews.md) and mirrored by both project-local agent skills;
changes to capture layout must update those operational references together.
Browser review failures expose stable structured codes and messages.
Profile routes explicitly select an existing exact username, create a unique
username, or rename one Profile to another unique username.
Unexpected HTTP failures are logged with their method, path, and traceback at
the application boundary before the generic internal-error response is sent.
`/api/file-media` serves one composed image-bay side, addressed by Snapshot id,
the same nullable File-path pair `/api/file-diff` uses, the required bay key,
and side. The File pair handles renames; the bay key distinguishes several image
outputs inside one notebook and the image target of a symlink. It recovers the
File through the Room, asks
`bays()` for that exact image bay, and writes the selected `MediaSide.data` under
the media type composition concluded. Whole-File images return their captured
bytes; notebook images return the bytes strictly decoded from the captured MIME
entry; symlink images return the final target bytes retained during Snapshot
capture. No engine runs and the boundary forms no second opinion about media
type.
A missing bay, non-image bay, or absent image side is refused rather than
answered with empty bytes. Snapshot ids are never reused, so the response is
declared immutable and cacheable outright.
`/api/manifest` receives the
complete selected Tab parameters, shows that state, and provides Snapshot/File
keys; it performs no Pull Request preparation. `/api/pull-request/prepare` is the
sole HTTP boundary for Pull Request preparation.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, reads standardized installation metadata once, selects
mode-specific implicit persistent paths, builds runtime configuration, starts
the selected server topology, opens the browser, and exposes repository-mark
commands. Editable installations run Vite and reloadable uvicorn; standard
installations run the bundled HUD and API through one non-reloading uvicorn
process. `base.py` is the sole caller into `_base`, whose private modules
implement registry operations and process launch. See [`release.md`](release.md).
