# Backend modules

## Purpose

This document maps the Python modules and the responsibilities between them.

## Application flow

```text
CLI
 └─ server
     ├─ change_source
     ├─ db
     ├─ engines
     ├─ formats
     └─ rendering

formats ──► engines
        └─► rendering
        └─► change_source data

rendering ──► engines
change_source ──► engine contracts
```

Dependencies do not point from engines into rendering.

## `dirdiff.util`

Exports `JsonValue` and the optional-value contracts `Option`, `Some`, `Nothing`,
and `maybe`. `Option[T]` is the union of `Some[T]` and `Nothing`; each variant
provides `map` and `and_then` for transformations. `Some.present` narrows either
branch to its variant. `must` requires presence, while `get`, `get_map`, and
`get_or` leave the Option API for a nullable or default-accepting consumer.
`map_or` maps and extracts with an eager absence default. `Some.when` wraps an
eager value conditionally; `Some.then` calls its factory only when true.
`Some.then_bind` supplies a tuple of positional callback arguments conditionally.
`map_bind` supplies its payload followed by an explicit tuple of arguments;
absence skips the callback. Both evaluate supplied arguments eagerly and invoke
callbacks synchronously.
`maybe` converts native nullable input. The wrappers are immutable and retain
payloads by reference; they do not catch errors or perform I/O.

Captured File sides, side paths, link facts, and format representations carry
Options through their internal contracts. SQL columns, serialized payloads,
and native library inputs keep their own nullable representations. Unrelated
optional metadata and engine contracts are not converted by these File flows.

## `dirdiff.change_source`

Provides repository and preset data. `ChangeSourceProtocol` has two operations for one
selected Tab capture: `changes()` observes ordered `FileChange` records and
optional aggregate line totals; `load(files)` accepts observed `FileChange`
records and supplies ordered
`LoadedFile` results retaining each exact change and named left/right `FileData`
outcomes. Absent sides remain absent; callers never reconstruct File pairing.
`FileChange.left/right` and `LoadedFile.left/right` use `Option`. Loaded link
facts and the captured link's diagnosis and target use Options as well, with
equal target-path/data presence and exclusive diagnosis/target presence checked
by their contracts. Manifest path records retain Option side paths until the
manifest or lazy-info payload is built.
A present `FileSide` carries a source-local
`FileKey`, semantic `FileProperties`, and an opaque `content_identity`.
Properties distinguish regular Files, symbolic links, and references to another
repository revision; only regular Files can be executable. The key names a
source slot and canonical repository-local path for semantic File addressing,
while the opaque identity is stable across captures under the same
materialization rules. Raw Git modes and object addresses remain private.

Observation obtains complete identities, acquiring any data required to do so.
The source retains those acquired results for its one capture lifetime. Later
`load` calls return the observed data or read the exact listed immutable version;
they do not reread mutable sides. Acquisition order and storage optimizations
remain private to the source. Expected outer failures are contained by side
and have their own final content identities. Sources do not compute whole
Snapshot equality.

`GitChangeSource` and `PresetChangeSource` fix selected inputs at construction.
A `FileChange` also carries change classification, display label, whether it is
additional untracked content, and explicit lazy policy with the complete source
text supplying it. The separate `GitRepository` and `PresetCatalog` interfaces
prepare refs and picker groups; neither exposes capture storage operations.
Pull Request
preparation parses the URL, identifies the marked repository and forge remote,
retains the forge's recorded target and head SHAs, and fetches those object ids
directly without moving tracking refs. Content-addressed local refs retain every
prepared revision against Git garbage collection, including after a refresh.
Preparation computes the recorded commits' merge base locally, never selecting
sides from tracking refs after fetching. Missing or non-directory marks are
logged and skipped while searching for the matching remote; other inspection
failures remain visible. Missing objects, unavailable forge metadata, and
missing history fail explicitly. The result includes the URL,
exact merge-base and head commits, and observed branch names for HUD display.
Additional untracked Files need not participate in those backend totals. An
outer-side loading failure occupies its own `FileData.content` as `DirdiffError`;
successful siblings remain available. Sources do not select Rooms, retain state
between captures, classify contents for renderers, or invent
renderer-dependent line counts.

Git loading expands canonical LFS v1 SHA-256 pointers from local storage,
including shared worktree storage and configured `lfs.storage`. It bounds reads
and verifies payload size, trusting the local LFS store's content-addressed
objects without recomputing their digest. Missing or wrong-sized objects and
recognized unsupported versions or transformation extensions use the ordinary
backend loading error. Nonmatching pointer-like text stays ordinary content.
Missing objects include a manual fetch hint. No download or filter runs.

*Implementation detail:* Single and batch loading share this behavior. Size
inspection reads small candidate pointers to report payload size without
loading that payload.

Loading stops at bytes. The source walks links inside the same selected side,
retains the raw outer bytes and nested link facts, and checks the exact
materialized terminal size before and after loading against the caller's bound.
Expected target failures become a terminal diagnosis on a successful
`FileData`; unexpected failures propagate. What
final bytes are — text, a notebook, an image — is `dirdiff.formats`' decision,
and the definition of what this project calls text lives there with the
classification that asks it.

Manifest File classification describes VCS tracking. The `tracked` variant
carries a File change status; `untracked` identifies content not tracked by the
VCS. Neither variant identifies a particular VCS or implies successful capture.

*Implementation detail:* preset fixtures use the tracked variant because VCS
tracking does not apply to them.

Its public interface is exported from `dirdiff.change_source`.

## `dirdiff.engines`

Compares two already-loaded text sides.

The package defines `DirdiffError`, the shared failure contract used when
dirdiff cannot safely produce a requested result.

An engine produces aligned rows, inline diff tokens with any structural move
endpoints, summary counts, a required `interleave_inline_rows` statement, and an
engine warning when applicable. `interleave_inline_rows` is false when rows are
aligned physical source lines and true when each row copies one line the
engine's tool printed, so inline view must interleave a changed equal row's
sides back into that line; only Git word mode sets it true. It does not load files
or attach syntax highlighting, folds, or hunk indexes. Difftastic keeps its degraded textual
diff construction inside its own implementation.

Git no-index accepts Myers, Minimal, Patience, or Histogram. Myers is its
declared default. Each Git invocation passes `--diff-algorithm` explicitly;
algorithm selection changes line alignment without changing subsequent decoration.
Its `ignore_space_change` boolean adds `--ignore-space-change` when enabled.
Git then ignores trailing whitespace and changes in the amount of existing
whitespace. Adding whitespace where there was none still counts as a change.
Equal rows retain both original source spellings, including ignored spacing in
patch context and omitted gaps.

Its `word_diff` boolean requests Git's porcelain word output. Its
`word_boundary` is consulted only in word mode and has three modes: `default`
passes nothing and Git splits at whitespace; `language` maps the temporary
file's suffix to one of Git's built-in diff drivers through an attributes
file, and a suffix without a driver keeps the whitespace boundary; `custom`
hands the user's pattern to Git unvalidated, so Git's rejection surfaces as
`DirdiffError`. The locale differs per mode for the reason documented in the
driver; the host must provide the `C.UTF-8` locale, which current glibc, musl,
and macOS ship, for a custom pattern to match whole codepoints. On a glibc 2.17
era host such as RHEL 7, which lacks that locale, Git matches bytes instead.
The adapter copies the porcelain patch rather than reconstructing a
physical-line diff. Every `~` record ends one row. A space
record becomes one unchanged token on both sides; `-` and `+` records become one
token on their reported side. Tokens retain the complete record payload, including
Git's whitespace spelling. Hunk coordinates locate unchanged source gaps, and
the non-whitespace character count of each record, which any word boundary
preserves, places a row on the source line its text starts on. The adapter
trusts Git's text; the fast and slow suites prove the round trip. Hunk
coordinates do not align changed rows.
Git may split one physical source line across several rows or combine fragments
of several physical lines in one row; the HUD shows that upstream behavior.
Word mode therefore reports `interleave_inline_rows` as true, and the text bay
carries that statement to the HUD so inline view interleaves each changed
equal row back into the one line Git printed.
A nonempty row whose every token, including whitespace, is deleted or inserted
retains that status. Every other mixture is an equal aligned row carrying Git's
token facts. Summary categories count those row statuses; equal rows carrying a
changed token count as modified. Git itself suppresses moved-line colors while
word diff is active.

Git's required `color_moved` and `color_moved_ws` settings pass the exact
upstream choices, including `default`, independently of diff whitespace.
Both initially use `no`. Controlled Git color slots become optional
unpaired `move` row data at the engine boundary. The record stores its
`unpaired` kind, primary or alternative zebra group, and dimmed state without
a counterpart identity. The populated source coordinate supplies the old or
new side. Each highlighted row is classified and counted once as moved. The
adapter validates colored payloads against the original source; escapes never
become decoration markup. Unsupported envelopes and failed Git execution
remain local failures.

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
side. GumTree actions classify inline changes. The adapter maps update, insert,
delete, and move actions directly to replace, insert, delete, and move tokens.
A nonempty row whose every token, including whitespace, is inserted or deleted
retains that status; every other mixture is an equal aligned row. Summary
categories remain derived from GumTree's meaningful token facts rather than the
neutral row status.
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
differing lines are one-sided rows. A nonempty row whose every token, including
whitespace, is inserted or deleted retains that status; every other mixture is
an equal aligned row. A one-sided line Diffsitter never mentioned
carries no tokens and stays equal, except a blank line, whose presence is the
change and which takes its side's status. Summary categories remain derived
from the reported meaningful graphemes.
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
results retain the normal alignment and span validation. Novel left spans map
to delete tokens and novel right spans map to insert tokens; the adapter does
not infer replacements from paired spans. Source gaps omitted from Difftastic's
novel spans remain private `synthesised` partition slices while row status is
derived. Every synthesised gap becomes a public unchanged token because
Difftastic reported no classification for it. A row with at least one public
token takes the inserted or deleted status when every public token has that
status. An aligned source line with no reported spans has an empty token list and
contributes no implicit unchanged status. Other mixtures remain equal. Summary
categories retain meaningful-span accounting.

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
changing the engine's alignment or summary. Complete physical-line rows reuse
syntax parsed from their source. A fragment-bearing row stream, such as Git
word diff, is instead parsed exactly as sent to the HUD so its offsets remain
valid. Fold discovery continues to use the complete new source.

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

Captured byte inputs and context side paths/link facts use `Option`, as do
image sides and notebook document, cell, output, and representation selection.
Builders extract native nullable text and hints when constructing the engine's
`DiffSide`; composition extracts nullable values when constructing payloads.
Typed text rejection and diagnosed notebook parts remain explicit results.

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
target document. Link traversal and the source-path boundary belong to the
change source, never the composer or Snapshot publication.

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
VCS tracking, backend change classification, and an optional capture
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
Store records expose validated placement variants, tagged action payloads,
and nested regular/link File sides. Review action insertion inputs omit global
activity order; persisted outputs require it. Nullable shape columns remain
inside the SQL mapping boundary.
Hydrated `SnapshotFileRecord` side fields and the symlink descriptor's reached
target are Options. Hydration constructs them once from SQL row presence, and
publication extracts values only when constructing SQL parameters.

Profile preferences store the folding policy, Git algorithm and whitespace
policy, Difftastic comment policy, and GumTree matcher in one row per Profile. Reads create initial values only when no
preference row exists; saves replace all values. The HTTP boundary validates the closed algorithm choice.
The same row stores Git moved-color and move-whitespace choices. Migration
initializes both to `no` for existing Profiles; subsequent writes require both.
It also stores the Git word boundary mode and the custom word pattern, migrated
to `default` and NULL. NULL means no pattern has been entered; the HTTP
boundary refuses an empty string. The pattern persists whatever the
mode so switching back to `custom` restores it, and the HTTP boundary does not
compile it.

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
operations. RoomLord selects Rooms and publishes supplied source observations.
Room methods require that key explicitly while providing metadata, manifested filepath
iteration, direct captured-file lookup, and Thread access through `threads`,
`get_thread`, `thread_for_comment`, and `create_thread`; Comment and lifecycle
writes belong to the returned bound Threads. The agent boundary also uses the
Room's explicit activity reads, atomic review batch, complete persisted
`capture_context` and read-only
`path_for_snapshot` access to the already published Snapshot directory.
Concrete source setup supplies the 1 MiB reached-link limit at construction. RoomLord captures and persists the source's raw
bytes and link facts. The source stops before
a repeated normalized path, refuses targets outside the repository, and checks
the terminal size before and after reading. Larger targets stop with a visible
diagnosis. Capture and publication stores remain
private. `Room.get`
authenticates the raw side and the exact link sidecars named by persistence,
returning parsed link facts with the captured path. The module calls
`dirdiff.db` and does not issue SQL.

The Room and Snapshot lifecycle is described in
[`rooms.md`](rooms.md).

Room correspondence receives only prepared law facts, an already constructed
`ChangeSourceProtocol`, and display/diagnostic labels. Head and Refs state
identities are opaque; Branch Review symbolic identities are caller-owned
non-null JSON values; Pull Request uses its URL; Preset uses catalog/group ids.
`Room.capture_context` decodes every retained fact for concrete continuation
setup. Source setup validates revisions and physical placement outside this
module. Every capture passes these prepared facts to `corresponding_room`;
persisted facts select the same Room without a separate capture operation.

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
boundaries. Agent reads require a selected-Snapshot placement. Browser History
reads every Room Thread; when the displayed Snapshot has no placement, the
Thread stays bound to its real origin and reports a captured-File comparison for
navigation. A read publishes each Thread as its immutable origin plus one real
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

It validates HTTP inputs and outputs, prepares the selected concrete source
and correspondence facts, calls `RoomLord` for manifests and follow-up Snapshot lookup, asks
`dirdiff.engines` for the renderer a request names, and builds one composed diff
per File through `dirdiff.formats`, attaching the manifest's display name and
file kind to the returned envelope. Snapshot-context browser review
routes read one bounded Room Thread page and apply Profile-authored Thread and
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
complete selected Tab as one JSON `selection` query value, shows that state,
and provides Snapshot/File keys; it performs no Pull Request preparation.
`/api/pull-request/prepare` is the sole HTTP boundary for Pull Request preparation.

`server.base.ManifestSelection` validates the per-Tab required fields before
source setup. Repository Tabs carry a positive Mark `project_id`; Preset carries
`catalog` and `subset`. Branch Review keeps local/remote selections structured.
Each validated selection's explicit `prepare()` returns `PreparedCapture`:
source, correspondence facts, nullable Mark id, and side labels. Manifest and
agent initial handlers pass these explicitly to `RoomLord.corresponding_room`; preparation does not observe
Files or publish Snapshots. Manifest and lazy-info share captured File metadata
conversion and display ordering; lazy-info reads only the addressed Snapshot.

## `dirdiff.cli`

Defines terminal commands and process startup.

It parses CLI options, reads standardized installation metadata once, selects
mode-specific implicit persistent paths, builds runtime configuration, starts
the selected server topology, opens the browser, and exposes repository-mark
commands. Editable installations run Vite and reloadable uvicorn; standard
installations run the bundled HUD and API through one non-reloading uvicorn
process. `base.py` is the sole caller into `_base`, whose private modules
implement registry operations and process launch. See [`release.md`](release.md).
