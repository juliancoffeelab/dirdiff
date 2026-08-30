# File formats and composition

## Purpose

Formats turn a pair of captured File sides into the structure the HUD reviews.
They answer two different questions:

- What logical parts does this File contain?
- How should each part be presented?

The backend answers the first question by composing frames and bays. The
frontend answers the second by rendering each bay according to its payload
kind. This split lets a notebook contain ordinary source text, textual output,
and pictures without making "notebook" a frontend widget.

This document describes the current design and the invariants that make formats
compose. It deliberately does not record implementation stages, a preset
catalog, or every field of the HTTP payload.

## Composition model

The hierarchy is:

    File
      Frame
        Bay

A File is the changed repository entity. A frame is a logical part of that
File, such as a notebook cell. A bay is the physical two-sided unit the HUD can
render, collapse, navigate to, and review.

Composition always starts with the complete captured left and right sides. A
format may parse them, align their internal parts, and choose their frame and
bay order. It must not reread the repository or derive structure from frontend
state.

Files without internal structure still use the same model. They compose one
heading-less frame with one or more bays. There is no separate flat-file wire
shape.

Frames and bays are asymmetric across the two sides. A cell may be added,
removed, moved, or changed. A bay may exist on one side only. Composition keeps
those facts explicit rather than inserting blank content.

## Frontend representation

The idea of formats is to split the concept of a file into two *asymmetric*
representations.
One is about the structure of a file, what it composed of and what its
higher-level data.
The other is about how that file is visually presented to a human.

The highest level is a file, that's what largely managed by backend.
Then backend splits the file into frames.
For simplest case, flatfile, the frames are just one text segment.
For notebooks, frames are the cells of the notebook.

When it gets interesting and what highlights the concept of formats are bays.
Each frame ultimately gets split into bays.
Notebook cell is split into multiple bays, the bay for source text, then bays
for output text, and potentially, other shapes, like image.

That's where the backend ends.

Now each bay payload, in frames, in files gets sent to a frontend.
These are independent:
- there's text
- there's image

There should be no hierarchy, and honestly, probably not a lot of shared code
between them, since they would get complicated pretty fast.

Notice how these are independent.
Text file can have text bay, image can have image bay.
But notebook has all of them.
Hell, image can have an image bay, and then text bay for metadata.
Blob, while being strictly non-textual, will have text bay for metadata, cause
that's all we have.
Link can have two textual bays, one for path metadata (points to X), and
then collapsed bay or set of bays for the original file.

## Backend boundary

The format subsystem receives immutable captured bytes, File paths, side
labels, and format-specific capture facts such as resolved symbolic-link data.
It produces:

- File-level existence and line summaries;
- frames in document order;
- bays in presentation order;
- semantic change, warnings, labels, and collapse policy for each bay;
- either text rows or image references for each bay.

The subsystem decides what a File means. It parses notebooks, chooses output
representations, extracts image and byte facts, and flattens linked targets.
The frontend does none of that work.

The backend does not decide layout, fetch timing, scroll behavior, or how a
picture is painted. Once composition has produced a bay payload, those concerns
belong to the HUD.

Composition is total for captured File content. Bytes that cannot produce a
specialized representation still produce a reviewable text bay describing the
damage or the bytes. Unexpected programming failures remain errors.

## Bays

Every bay has a stable File-local key, labels for its two sides, semantic
change, collapse policy, warnings, and one payload kind. The key is identity,
not a display index. Replaying composition over the same captured facts must
produce the same keys and order.

Bay order is meaningful. It follows document order within a File and
presentation order within a frame. The frontend must not regroup bays by kind.

Collapsed bays remain part of the composed File. Collapse changes only what the
HUD currently paints. It does not remove review coordinates, changed state, or
navigation stops.

### Text

A text bay contains aligned rows produced from two accepted text sides. Text
rendering may add token differences, syntax classes, folds, and bay-local hunk
indexes. It must preserve the engine's text and alignment.

Text bays cover more than source code. Notebook prose and output, image
metadata, byte facts, link diagnostics, and rejected structured input all use
the same payload kind when their useful representation is text.

Each text bay numbers its own hunks from zero. The frontend derives File-wide
navigation order by walking frames, bays, and their local stops in payload
order. The backend does not flatten those indexes into one File-wide sequence.

### Image

An image bay contains immutable references describing each present side. The
actual bytes stay in the Snapshot and are fetched through the media endpoint.
The reference and served bytes must agree on media type, size, and digest.

An image bay has one reviewable pseudo-line because it has no source rows. Its
semantic change supplies its navigation stop. Image rendering does not inherit
from text rendering and does not manufacture rows to imitate it.

Text and image are independent payload kinds. Adding another kind means adding
a new backend payload and a frontend renderer. It must not turn the existing
kinds into a class hierarchy.

## Format shapes

### Flat files

A presumed text File composes one heading-less frame with one text bay. Paths
select syntax presentation, but bytes must satisfy the shared text contract
before they enter a text engine.

If presumed text contains NUL or invalid UTF-8, composition presents byte facts
and the rejection reason. It does not decode with replacement characters or
silently discard bytes.

### Notebooks

A notebook frame represents a cell. Cell identity, rather than its current
position, keeps a frame stable when cells move. Source is the primary bay.
Changed metadata and outputs become attached bays in notebook order.

Code, markdown, and raw cells retain their distinct semantics. Source remains
reviewable even when the cell itself is unchanged beside another change.
Execution outputs do not replace source.

Each output chooses one useful representation. A valid PNG representation is
an image bay. Stream text, display text, and tracebacks are text bays. Selecting
one representation prevents a plot from appearing twice merely because its MIME
bundle also contains a textual description.

Damage stops at the smallest notebook part that cannot satisfy its contract. A
bad output does not discard its cell, and a bad cell does not discard valid
siblings. Damage before a usable cell list exists turns the whole notebook into
one reviewable raw representation.

### Images

An image File composes one frame containing the picture and textual bays for
useful metadata and byte facts. Parsed metadata may degrade independently of
the picture. Exact bytes remain available even when metadata parsing fails.

Byte facts state the declared type, byte size, and SHA-256 digest. These are
ordinary text rows so they can be diffed and reviewed.

### Unsupported bytes

Content with no text or image representation composes as byte facts. "Blob" is
a File classification, not a frontend payload kind. The HUD needs no blob
renderer.

### Symbolic links

A link File keeps its raw outer target spelling as the direct bay. Repository
paths, arrows, nested hops, and resolution failures do not belong in that bay.

The target is a separate collapsed bay. A text target is text, an image target
remains an image, and opaque target content remains byte facts. A notebook
target becomes one script-like text document so nested frames do not appear
inside the outer File.

Nested textual links join into the target document behind comment walls. A loop
or failed resolution ends that document with an explicit diagnosis. Each link
in a walk appears at most once.

Snapshot capture resolves links before composition. Resolution stays inside the
repository and stops before revisiting a path. It checks a reached non-link
target's exact size before reading it: targets through 1 MiB are retained, and
the returned byte count is checked against the same bound. Larger targets stop
with an explicit diagnosis. The composer and media endpoint use those captured
facts and never follow live links. In a link-to-regular transition, the reached
content and regular File content meet in the same target bay.

One relational row per link side is the authoritative statement that the side
is a symbolic link. It stores exact absolute paths and SHA-256 digests for the
metadata sidecar and optional target sidecar. Room reads those paths directly,
authenticates both byte sequences, and parses the metadata before composition.
No format or HTTP code probes for a conventional sibling filename. The metadata
stores chain hops, a terminal diagnosis or final repository target path, but no
duplicate target digest.

## Change and damage

Bay change is semantic and comes from composition. Added and removed sides are
different from present empty content. Moved notebook cells remain moves even
when their source also changed. Two byte-identical image sides are unchanged
even though both pictures render.

Warnings attach to the smallest bay whose representation is damaged. They
describe the lost or rejected representation. They do not pretend that damaged
content was successfully parsed.

A changed bay must remain reachable through navigation even when its renderer
produces no changed text row. Text bays can expose a bay-level stop for that
case. Image bays use their pseudo-line. An unchanged bay contributes no stop.

## Review coordinates

A review target names the Snapshot File pair, bay key, side, and a one-based
inclusive line range. Text ranges address rendered text lines. Non-text bays
accept only their single pseudo-line.

Review validation recomposes bays from the same captured Snapshot. It accepts a
key only when that composition produces the key and the requested payload kind
supports the range. It never searches live repository contents or guesses a
replacement key.

Line pins and review Threads use the same bay identity the HUD renders. A
format-specific parser may choose keys, but the frontend must not derive or
rewrite them.

## Media transport

The composed response carries image facts, not base64 image bodies. A media
parameter names the Snapshot, File pair, bay key, and side. The endpoint
recomposes the File, finds that exact image bay, and serves its retained bytes.

Several image bays may exist in one File, especially notebook outputs. The bay
key is therefore required. Missing sides, unknown keys, non-image bays, and
damaged retained bytes are errors rather than empty responses.

Snapshot identity makes media immutable. The endpoint may cache a successful
response as immutable because it never consults a worktree after capture.

## HTTP flow

The File diff path is:

    /api/file-diff
      find the Room by snapshot id
      load authenticated captured sides and link facts from the Room
      build composition context
      call Composer.compose()
      attach request-level File presentation facts
      return the composed diff

The media path deliberately reuses the same classification:

    /api/file-media
      find the Room by snapshot id
      load authenticated captured sides and link facts from the Room
      build bay context
      call Composer.bays()
      select the exact image bay and side
      return its retained bytes with the composed media type

Review validation follows the second path through Composer.bays(). It asks the
same composition whether a bay exists and whether its payload kind supports the
reviewed range. It does not keep a separate table of format-specific review
targets.

These flows are ordered boundaries. Room lookup establishes immutable captured
state before format parsing starts. Bay composition establishes media identity
before bytes are served. The HTTP layer may translate errors and attach
request-level facts, but it does not classify Files or build bays itself.

## Module map

The Python formats package contains the shared frame and bay contracts, the
top-level composer, and sibling builders for flat files, notebooks, images,
opaque bytes, and symbolic links. Builders depend on the shared contracts. They
do not call the HTTP layer or frontend code.

The composer has two views of the same operation. One yields typed bays with
exact media bytes for backend consumers. The other serializes those bays for
the HUD, renders text through the selected engine, and reduces image bytes to
references. Review validation and media serving use the first view so they do
not invent a second classification path.

The frontend frame renderer walks the backend-authored order and dispatches
each bay to the renderer for its payload kind. Text and image renderers may
share review-line conventions, but neither is the base implementation of the
other.

The HTTP handlers follow the ordering above. They do not parse formats or
rebuild their payloads.
