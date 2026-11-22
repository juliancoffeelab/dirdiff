# File formats and composition

Formats turn a pair of captured File sides into the structure the HUD reviews.
The backend decides what a File contains (frames and bays); the frontend
decides how each bay is painted, by payload kind. This split lets a notebook
mix source text, textual output and pictures without "notebook" being a
frontend widget.

`formats` depends on `engines`, `rendering` and change-source data; `rendering`
depends on `engines`. Nothing points from `engines` into `rendering`.

## Composition model

    File
      Frame
        Bay

A File is the changed repository entity. A frame is a logical part of it, such
as a notebook cell. A bay is the physical two-sided unit the HUD renders,
collapses, navigates to and reviews. Every File, even a flat one, composes
through this model; there is no separate flat-file wire shape.

Composition starts from the complete captured sides. It must not reread the
repository or derive structure from frontend state. Classification reads the
File path and the captured bytes only, never the repository entry behind them.
A notebook or a blob is named by its path. An image is named by its bytes,
whatever the path says: a side is an image when `image_media_type()` names a
type browsers display natively. Only the header is read, so an image with a
damaged body is still an image and its metadata bay reports the damage. Bytes
Pillow will not open are not an image: unrecognised bytes, a header cut short,
or a picture past its decompression-bomb limit. So a side whose bytes stand for
a non-file (a reference materialized as its commit id) is never an image. A
two-sided File keeps a specialized classification only when both sides agree; a
mixed rename, or an image on one side only, is presumed text.
`try_decode_text()` is the single definition of text: no NUL byte, valid UTF-8
with an optional BOM.

Frames and bays are asymmetric across the two sides: a cell may be added,
removed, moved or changed, and a bay may exist on one side only. Composition
keeps those facts explicit instead of inserting blank content.

Composition is total for captured content. Bytes that cannot produce a
specialized representation still yield a reviewable text bay describing the
damage or the bytes, so a File never arrives as an error where a diff was
expected. Unexpected programming failures remain errors.

Payload kinds (text, image) are independent: neither renderer is the base of
the other, and a new kind is a new backend payload plus a new frontend
renderer, not a hierarchy. "Blob" is a File classification, not a payload
kind; its bays are text byte facts. `BayBody` is the single place the frontend
examines a bay `kind`.

The backend does not decide layout, fetch timing, scroll behavior or how a
picture is painted.

## Engines and enrichment

- An engine compares two already-loaded text sides; it does not load files or
  attach syntax highlighting, folds, or hunk indexes.
- Enrichment does not change the engine's alignment or summary.
- A fragment-bearing row stream (Git word diff) is parsed for syntax exactly as
  sent to the HUD so offsets stay valid; fold discovery still uses the complete
  new source.

## Bays

A bay key is File-local identity, not a display index. Replaying composition
over the same captured facts must produce the same keys and order. Line pins
and review Threads use the key the HUD renders; the frontend must not derive or
rewrite keys. A notebook cell's key is its own cell id, so it survives edits to
that cell.

Bay order is document order within a File and presentation order within a
frame. The frontend must not regroup bays by kind.

Collapsing changes only what the HUD paints. It does not remove review
coordinates, changed state or navigation stops.

Each text bay numbers its own hunks from zero. The frontend derives File-wide
navigation order by walking frames, bays and their local stops in payload
order; `compose()` assigns no File-wide numbering.

A changed bay must stay reachable through navigation even when it has no
changed text row. A bay whose rows carry no stop (an image, a blob, a moved
cell) contributes one bay-carried stop at index zero; an unchanged bay, or an
empty text bay, contributes none. A File made only of such bays is zero-hunk.

An image bay has one reviewable pseudo-line because it has no source rows. Its
bytes stay in the Snapshot and are served by the media endpoint; the composed
response carries references, not bodies. It shows no changed-line counts:
zeroes beside a replaced picture would claim an engine looked and found
nothing.

## Format rules

- A presumed-text File with NUL or invalid UTF-8 on either side becomes byte
  facts plus the rejection reason for the whole File. It is never decoded with
  replacement characters and never partially shown as text.
- A notebook frame's identity is the cell's, not its position, so moves stay
  moves even when the source also changed. Execution outputs do not replace
  source.
- Each notebook output picks one representation, so a plot does not appear
  twice because its MIME bundle also carries text.
- Damage stops at the smallest part that cannot satisfy its contract: a bad
  output does not discard its cell, a bad cell does not discard its siblings.
  Only damage before a usable cell list exists turns the whole notebook into
  one raw representation. Warnings attach to the smallest damaged bay and say
  what was lost.
- Added and removed sides differ from present empty content.

### Symbolic links

The direct bay holds the raw outer target spelling only; the target is a
separate collapsed bay composed by the target's own format, except that a
notebook target becomes one text document so nested frames never appear inside
the outer File.

Snapshot capture resolves links before composition, so neither the composer nor
the media endpoint ever follows a live link. Link traversal and the source-path
boundary belong to the change source. Resolution stays inside the repository,
stops before revisiting a path, and bounds the size of a reached target. In a
link-to-regular transition the reached content and the regular File meet in the
same target bay.

One relational row per link side is the authoritative statement that the side
is a symbolic link. It stores exact absolute paths and SHA-256 digests for the
metadata sidecar and the optional target sidecar; Room reads those paths,
authenticates both byte sequences and parses the metadata before composition.

## Review and media

A review target names the Snapshot File pair, bay key, side and a one-based
inclusive line range. Validation recomposes bays from the same captured
Snapshot through `Composer.bays()` and accepts only a key that composition
produces. It never searches live contents or guesses a replacement key, and it
keeps no separate table of format-specific targets. Review validation and media
serving both use `bays()` rather than `compose()`, so there is a single
classification path and no diff engine runs.

`/api/file-media` serves the exact image bay's retained bytes. The bay key is
required because one File (a notebook) may hold several images. The response is
cacheable as immutable because Snapshot ids are never reused and the endpoint
never consults a worktree.

Ordering: Room lookup establishes immutable captured state before parsing; bay
composition establishes media identity before bytes are served. The HTTP layer
translates errors and attaches request-level facts but never classifies Files
or builds bays.
