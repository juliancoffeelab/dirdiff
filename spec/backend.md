# Backend modules

Cross-module contracts and invariants the Python code does not state locally.

## Dependency direction

`formats` depends on `engines`, `rendering`, and `change_source` data;
`rendering` depends on `engines`. Dependencies do not point from `engines` into
`rendering`.

## `dirdiff.change_source`

- Observation obtains complete content identities, acquiring any data needed to
  do so. The source retains those results for its one capture lifetime; later
  `load` calls return the observed data or read the exact listed immutable
  version and never reread mutable sides.
- Callers never reconstruct File pairing: absent sides stay absent.
- Sources do not compute whole-Snapshot equality, select Rooms, or retain state
  between captures.
- Loading stops at bytes. What bytes are (text, notebook, image) is decided by
  `dirdiff.formats`.
- Git LFS pointers are expanded from local storage only; no download or filter
  runs, and the local store's content-addressed objects are trusted without
  recomputing their digest.
- Pull Request preparation computes the merge base locally from the recorded
  commits and never selects sides from tracking refs after fetching.

## `dirdiff.engines`

- An engine compares two already-loaded text sides; it does not load files or
  attach syntax highlighting, folds, or hunk indexes.
- `interleave_inline_rows` is false when rows are aligned physical source lines
  and true when each row copies one line the engine's tool printed (only Git
  word mode), so inline view must interleave a changed equal row's sides back
  into that line.
- Git word mode with a `custom` word boundary hands the user's pattern to Git
  unvalidated; Git's rejection surfaces as `DirdiffError`. The host must provide
  the `C.UTF-8` locale for a custom pattern to match whole codepoints.
- `engine()` lives in `__init__.py` rather than `base.py` because selection must
  import the concrete engine classes, whose modules depend on `base.py`.

## `dirdiff.rendering`

- Enrichment does not change the engine's alignment or summary.
- A fragment-bearing row stream (Git word diff) is parsed for syntax exactly as
  sent to the HUD so offsets stay valid; fold discovery still uses the complete
  new source.

## `dirdiff.formats`

- `/api/file-diff` has one shape, a composed diff of frames of bays; there is no
  `render_kind`.
- `compose()` assigns no hunk numbering; the frontend builds the File's
  navigable sequence from bay-local boundaries.
- `Composer.bays()` is the engine-free lookup used by review validation and the
  media endpoint.
- `compose()` never serializes image bytes; they are served only by
  `/api/file-media`.
- Classification reads paths only. A two-sided File keeps a specialized
  classification only when both paths agree; a mixed rename is presumed text.
- `try_decode_text()` is the single definition of what this project calls text:
  no NUL byte, valid UTF-8 with an optional BOM.
- Link traversal and the source-path boundary belong to the change source, never
  the composer or Snapshot publication.
- `composer.py` is the sole caller into `_composer`; no private builder imports
  another private builder.

## `dirdiff.db`

- A Profile's preference row is created with initial values only when absent;
  saves replace all values.
- The custom Git word pattern persists regardless of the selected mode, and
  NULL means none was entered. The HTTP boundary refuses an empty string and
  does not compile the pattern.
- A symbolic-link side is identified by the presence of its link relation, not a
  filesystem probe.
- Persistent engine bootstrap requires an explicit Alembic configuration path.

## `dirdiff.room_lord` and `dirdiff.review`

- The Room and Snapshot lifecycle is described in [`rooms.md`](rooms.md); the
  review model in [`reviews.md`](reviews.md).
- `RoomLord` calls `dirdiff.db` and issues no SQL.
- Missing Thread placements are derived only for a genuinely new Snapshot.
- Private source coordinates do not cross Room, HTTP, frontend, draft, or cache
  boundaries.
- `thread.py` is the sole caller into `_thread`.

## `dirdiff.server` and `dirdiff.cli`

- Snapshot ids are never reused, so `/api/file-media` responses are immutable
  and cacheable outright.
- The agent-visible Snapshot filesystem contract is documented in
  [`reviews.md`](reviews.md) and mirrored by the reviewer skill's
  `references/snapshot_structure.md` and, in short form, by the `round-review`
  and `babysit-patch` skills; changes to capture layout must update them
  together.
- Agents reach `/api/agent/*` through the `dirdiff agent` commands, never
  through hand-written HTTP.
- `app.py` is the sole caller into `_app`; `base.py` is the sole caller into
  `_base`. The installation and launch contract is in [`release.md`](release.md).
