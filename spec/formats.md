# File formats and composition

Formats turn a pair of captured File sides into the structure the HUD reviews.
The backend decides what a File contains (frames and bays); the frontend
decides how each bay is painted, by payload kind. This split lets a notebook
mix source text, textual output and pictures without "notebook" being a
frontend widget.

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
File path and the captured bytes only, never the repository entry behind them,
so a side whose bytes stand for a non-file (a reference materialized as its
commit id) composes by its path's format.

Frames and bays are asymmetric across the two sides: a cell may be added,
removed, moved or changed, and a bay may exist on one side only. Composition
keeps those facts explicit instead of inserting blank content.

Composition is total for captured content. Bytes that cannot produce a
specialized representation still yield a reviewable text bay describing the
damage or the bytes. Unexpected programming failures remain errors.

Payload kinds (text, image) are independent: neither renderer is the base of
the other, and a new kind is a new backend payload plus a new frontend
renderer, not a hierarchy. "Blob" is a File classification, not a payload
kind; its bays are text byte facts.

The backend does not decide layout, fetch timing, scroll behavior or how a
picture is painted.

## Bays

A bay key is File-local identity, not a display index. Replaying composition
over the same captured facts must produce the same keys and order. Line pins
and review Threads use the key the HUD renders; the frontend must not derive or
rewrite keys.

Bay order is document order within a File and presentation order within a
frame. The frontend must not regroup bays by kind.

Collapsing changes only what the HUD paints. It does not remove review
coordinates, changed state or navigation stops.

Each text bay numbers its own hunks from zero. The frontend derives File-wide
navigation order by walking frames, bays and their local stops in payload
order; the backend does not flatten them.

A changed bay must stay reachable through navigation even when it has no
changed text row. A bay whose rows carry no stop (an image, a moved cell)
contributes one bay-carried stop; an unchanged bay contributes none.

An image bay has one reviewable pseudo-line because it has no source rows. Its
bytes stay in the Snapshot and are served by the media endpoint; the composed
response carries references, not bodies.

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

The direct bay holds the raw outer target spelling only; repository paths,
arrows, hops and resolution failures stay out of it. The target is a separate
collapsed bay. A text target is text, an image target stays an image, opaque
content is byte facts, and a notebook target becomes one script-like text
document so nested frames never appear inside the outer File. Nested textual
links join into the target document behind comment walls; a loop or failed
resolution ends it with an explicit diagnosis. Each link in a walk appears at
most once.

Snapshot capture resolves links before composition, so neither the composer nor
the media endpoint ever follows a live link. Resolution stays inside the
repository and stops before revisiting a path. A reached non-link target is
checked against a 1 MiB bound by size before reading and by byte count after;
larger targets stop with a diagnosis. In a link-to-regular transition the
reached content and the regular File meet in the same target bay.

One relational row per link side is the authoritative statement that the side
is a symbolic link. It stores exact absolute paths and SHA-256 digests for the
metadata sidecar and the optional target sidecar; Room reads those paths,
authenticates both byte sequences and parses the metadata before composition.
The metadata holds hops and either a diagnosis or the final target path, and
no duplicate target digest.

## Review and media

A review target names the Snapshot File pair, bay key, side and a one-based
inclusive line range. Validation recomposes bays from the same captured
Snapshot through `Composer.bays()` and accepts only a key that composition
produces. It never searches live contents or guesses a replacement key, and it
keeps no separate table of format-specific targets.

`/api/file-media` recomposes the File with the same `Composer.bays()`, which
runs no diff engine, and serves the exact image bay's retained bytes. The bay
key is required because one File (a notebook) may hold several images. The
response is cacheable as immutable because Snapshot ids are never reused and
the endpoint never consults a worktree.

Ordering: Room lookup establishes immutable captured state before parsing; bay
composition establishes media identity before bytes are served. The HTTP layer
translates errors and attaches request-level facts but never classifies Files
or builds bays.

## Package boundaries

`composer.py` is the sole importer of `_composer`; the private leaf builders do
not import one another, the HTTP layer or frontend code. The parent picks the
builder for a link's normalized target. Review validation and media serving use
the typed-bay view (`bays()`) rather than the serialized one (`compose()`), so
there is a single classification path.
