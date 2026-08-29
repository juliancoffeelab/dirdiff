# Formats and composed diffs

## Status of this document

This is the living description of the formats subsystem. It is not an approval
gate and not a frozen contract.

Composition, the flatfile terminal, notebook composition, the composed-diff wire
shape, and the media endpoint describe running code and are corrected to it.

Two things are intent rather than description, and are marked where they appear.
The hybrid-notebook shape and symlink composition are unimplemented stages. And
`blob` is a bay kind in the running code, while this document describes it as a
File classification whose bay is `text`; that change is described under
[Blob is a classification, not a kind](#blob-is-a-classification-not-a-kind) and
everything downstream of it is written to the target rather than to the code.

As each remaining stage lands, its sections become ordinary living description
under [`goal.md`](goal.md) and must be corrected to whatever the implementation
actually does at that point. Text that survives a stage without being made true
is stale text, not a requirement.

"uberfile" is the project's informal alias for a composed multi-bay file, the
way Python people say "walrus". The formal names used everywhere else in this
document are the formats subsystem and the composed diff.

## Why the subsystem exists

Notebook support is a one-off. The server routes `.ipynb` by path,
`notebooks.py` builds a `render_kind: "notebook"` payload, and
`NotebookFile.tsx` renders it. Images, 3D models, and hybrid renderings added
the same way would each re-solve hunk identity, line pins, review targets, and
File summaries.

The notebook payload is already compositional without saying so: cell source is
rendered through the shared engine pipeline, while cell metadata and outputs are
reduced to booleans and counts beside it. The formats subsystem names that
structure, makes it the
single `/api/file-diff` shape, and moves the frontend's extension axis from file
formats to bay kinds.

## The composed diff

Every `/api/file-diff` response is one composed diff: File-level metadata plus an
ordered list of frames, each holding an ordered list of bays.

A frame is presentational grouping. A notebook cell's card and its heading are
one frame; an ordinary text file is one frame with no heading. A bay is one
renderable unit with its own identity.

Target shape, not existing code:

```ts
type ComposedDiff = {
  display_name: string;
  left_label: string;
  right_label: string;
  left_path: string | null;
  right_path: string | null;
  file_kind: FileKind;
  summary: DiffSummary;
  default_expanded: boolean;
  frames: FramePayload[];
};

type FramePayload = {
  frame_key: string;
  heading: string | null;
  bays: BayPayload[];
};
```

A frame carries no annotations of its own. Everything a reviewer needs to know
about a change belongs to the bay that changed, because a bay can be
navigated to, collapsed, and commented on, and an annotation on the frame above
it can be none of those. This is why there is no badge list here: every
candidate for one turned out to be either heading text or a bay that should
exist.

A notebook composes a frame for *every* cell, not only the changed ones, for
the same reason a text File is sent whole and folded rather than reduced to its
hunks: a change is read in the context around it, and a cell missing from the
diff cannot be read at all. An untouched cell composes a collapsed body carrying
no hunk, so it costs nothing to skip and is still there to open. Frames are
numbered over the cells the reviewer sees, so the sequence has no gaps.

Bays inside a frame are not peers. One bay is the frame's body and the
rest are attached to it: a notebook cell *is* its source, and its metadata and
outputs hang off that source. The body is always shown — folds already hide the
unchanged runs inside it — while its attachments carry a label and can be
hidden. `collapsible` records which is which, so the frontend renders the
hierarchy the format defines rather than inventing one from bay order.

`summary`, `file_kind`, `default_expanded`, and the label and path fields keep
their current meaning and their current use in the `FullFileHeader`, FileTree,
and manifest statistics. `hunk_count` is not among them: the File's hunk total
is derived by the frontend from the bays it received. `render_kind` disappears: the frame list is
the shape, and there is nothing left to switch on.

Frame headings are backend-authored from a deliberately closed vocabulary. The
frontend renders frames generically and owns only interaction and final drawing.
This continues the project's backend-authoritative rendering style: the frontend
never derives a heading and never maps a format name to presentation.

Beside its heading a frame wears a status: the body bay's `change` rendered
mechanically — the word itself, or the move's two names as
`moved: In [2] -> In [7]` — and nothing for `unchanged`, the same rule the
tint follows. A move names its ends the way frames are headed, so both ends
can be found on screen; a move with an unnamed end says only `moved`. A frame
with no heading still wears its status.

## Bay kinds

Bay kinds, not file formats, are the frontend's extension axis. Adding a
format adds a classification step and a bay builder. Adding a kind of thing
a reviewer can look at
adds a bay kind and its widget.

The shape on the wire, one record carrying one union:

```ts
type BayPayload = {
  bay_key: string;
  label: string;
  detail: string | null;
  collapsible: boolean;
  default_expanded: boolean;
  change:
    | { kind: "added" | "removed" | "changed" | "unchanged" }
    | { kind: "moved"; from_heading: string | null; to_heading: string | null };
  warnings: BayWarning[];
  kind_data: BayKindPayload;
};

type BayKindPayload = TextKindPayload | ImageKindPayload;

type TextKindPayload = {
  kind: "text";
  left_label: string;
  right_label: string;
  rows: DiffRow[];
  fold_hints: FoldHint[];
  stats: BayStats;
};

type ImageKindPayload = {
  kind: "image";
  left: MediaRef | null;
  right: MediaRef | null;
};

type MediaRef = { media_type: string; byte_size: number; digest: string };
```

`BayPayload` is what a consumer receives for one bay: the fields every bay has
whatever it holds, and `kind_data`, the single field that varies. It is a real
declaration on both sides rather than a reading aid. Python declares one
`TypedDict` whose `kind_data` is the kind union; `api.ts` declares one
`strictObject` whose `kind_data` is the `z.discriminatedUnion`. The shared
fields are written once on each side instead of once per kind, and adding a kind
adds one variant and touches nothing else.

Both sides spell these names identically, because the wire shape is one thing
and deserves one name. `Payload` is what crosses the wire, so the pre-render
types under [What `bays()` yields](#what-bays-yields) keep the bare names —
`Bay`, `TextBay`, `ImageBay` — and nothing named `Payload` holds
bytes or decoded text. The frontend has no pre-render stage and adopts the wire
names as they are; it consumes the payload rather than deriving a second
representation from it.

The discriminator sits one level down as a consequence. Placement, identity,
collapse, and status read `BayPayload` directly and never learn the kind; only
the widget dispatch descends into `kind_data` and switches on its `kind`.
`kind_data` is backend-owned like every other wire field: the frontend chooses a
widget from it and never authors or rewrites it. The field keeps the plain name
because it is data inside a payload, not a second payload.

The arms are named `TextKindPayload` and friends rather than `TextBayPayload`,
because they carry only the varying part: a name ending in "BayPayload" would
claim to be a whole bay's payload when it is not.

The union has two arms because there are two things a reviewer can look at:
lines, and a picture. `MediaRef` belongs to `ImageKindPayload` alone. It names
the bytes the widget must fetch, and no other kind fetches bytes.

| Kind | Contents | Rendered by |
| --- | --- | --- |
| `text` | decorated rows, fold hints, per-bay stats, optional engine warning | the existing `TextDiffGrid` |
| `image` | optional left/right `MediaRef` | `ImageBayView`, showing the pictures |

`text` bays are produced by one shared text-bay renderer, extracted from
today's `_render_notebook_text_payload` and `build_text_file_payload`. It calls
the selected diff engine and then `enrich_rows_for_display`. Rows, parts, fold
hints, and hunk boundaries keep exactly the contracts in
[`file-meat.md`](file-meat.md). Engines stay text-only and format-agnostic; they
never learn that bays or formats exist.

Every bay carries `label`, `collapsible`, and `default_expanded`. `label`
names the bay in its collapsed placeholder — "Output 1", "Cell metadata" —
and is backend-authored from the same closed vocabulary as frame headings. It
names the bay as a whole and is not a column heading: a `text` bay's
`left_label` and `right_label` remain the two side headings inside its grid.

`collapsible` is whether the reviewer may hide the bay at all: a frame's body
is not collapsible, its attachments are. `default_expanded` is the initial
expansion state of a collapsible bay's body, the bay-level counterpart of
the File-level field of the same name. All three are authored, never derived;
the frontend does not decide that a bay starts collapsed, that it may be
hidden, or what it is called.

A body bay's `label` names what it holds rather than restating that it is the
body — a notebook cell's source is labelled by its language, because "cell
source" is exactly what the frame's body is already known to be.

`default_expanded`'s first concrete use is a `text` bay whose rows are
entirely unchanged — a
notebook cell whose outputs changed while its source did not. Fold hints cannot
cover that case. `fold_hints_for_path` yields tree-sitter *structural* regions
that happen to be unchanged, `aggressiveFolds` only admits `top_level` and
`class_like` hints rather than creating any, and a prose-only markdown cell, a
raw cell, or a cell that is nothing but a loop yields no hints at all. "This
whole bay is unchanged" is a fact composition already holds once it has
rendered the bay, and it belongs in the payload rather than in a parser.

Serialized `image` bays carry no bytes. `MediaRef` describes a composed image
side; the widget requests the bytes from the media endpoint below. Whole image
Files are classified by `image_media_type()` in `formats/image.py`. Notebook
outputs use the same bay kind when their MIME bundle offers `image/png`.

An image File composes three bays: the `image` bay holding the picture, an
`image-metadata` text bay holding dimensions and EXIF exposed while Pillow opens
the container without decoding its pixel raster, and a `text` bay holding what
is known about the bytes. The picture answers "does it
look different"; the facts answer "did it actually change, and to what". Neither
answers the other — a re-encode that changes every byte can look identical, and
two visually different renderings of one asset have different digests for a
reason worth reading.

Each EXIF value is one bounded line. Values longer than 256 bytes or characters
are represented by their length and SHA-256 rather than copied into the diff.
Pillow refusals and malformed metadata damage only this bay and appear as its
warning; the picture and byte facts remain available.

They stand in the ordinary body-and-attachment relation: the picture is the
frame's body and is always shown, and the facts bay is an attachment beside it,
open by default and collapsible. Three lines cost a reviewer nothing to read and
answer the question the picture cannot, so they are stated rather than hidden
behind a disclosure; a reviewer who does not want them shuts the bay. A blob File
differs only because it has no picture — there its facts bay *is* the body,
shown and not collapsible.

Future kinds arrive as new bay kinds with their own widget. The bar for one is
that the thing genuinely cannot be read as lines — a picture clears it, and
named facts about a file do not, whatever produced them. Nothing about frames,
hunk allocation, or the composed-diff envelope changes to admit a kind that
clears it. No such kind is currently planned; a 3D model viewer is the shape
most often imagined for one, and it is postponed.

### Blob is a classification, not a kind

**Not implemented.** The running code has a third bay kind, `blob`, with its own
payload arm and its own widget. It should not, and the rest of this document is
written as though it does not.

A blob File is content nothing else claimed. What can honestly be shown for it
is its media type, its size, and its digest, in the spirit of what `git diff`
prints for a binary file. Those are lines of text. So a blob File composes one
`text` bay holding them, and the reviewer gets a real diff of the facts — the
size row changed, the digest row changed, the media type row did not — instead of
one undiffed line to eyeball twice.

That deletes rather than adds. There is no `blob` payload arm, no `BlobBay`, no
blob widget, and no pseudo-line for blob targets: the bay has three real lines,
so a comment can land on the digest specifically, and `1..1` stops being the only
range a blob target may hold. Classification still ends at blob — that is what
makes it total — but blob names a File, the way "notebook" and "flatfile" do,
rather than naming a kind of thing to look at.

The same text bay is what an image File's facts bay holds, and later the same
shape carries EXIF for the formats that have it: named facts about a side, which
change, and whose change is the point. None of that needs a kind, because
diffing named facts is what `text` already does.

What this gives up is that a blob File no longer composes any bay carrying
bytes. Today it does, so "download this blob" is one caller away from working;
under the target the media endpoint has no blob bay to be asked about, and a
download would need its own answer to "which bytes, addressed how". Nothing
currently asks — no blob widget requests bytes — so nothing regresses, but the
capability is being spent, not merely deferred.

## Composers

Composition is one class. `Composer` is not a protocol with an implementation per
format: the format-specific part is which bays get built, and that lives in
the ordered check below and the sibling modules it calls.

It has two entry points, because two of its three consumers must not touch a diff
engine:

```python
class Composer:
    def bays(
        self,
        left: bytes | None,
        right: bytes | None,
        context: BayContext,
    ) -> Iterator[Bay]: ...

    def compose(
        self,
        left: bytes | None,
        right: bytes | None,
        context: ComposeContext,
    ) -> ComposedDiff: ...
```

`bays()` yields every bay the file composes into, in document order, with
nothing an engine produces. `compose()` consumes that stream, applies the shared
text-bay renderer to each text bay, and returns the complete composed diff.

Neither method returns `None`, and neither has a "not my format" outcome.
Classification always reaches an answer because `blob` is terminal.

### What `bays()` yields

A bay here is the same bay the frontend eventually receives as a `BayPayload`,
before rendering and before serialization. The union has one member per kind,
matching the wire and the widgets:

```python
Bay = TextBay | ImageBay
```

- a **`TextBay`** carries its identity, its label, its expansion state, and its
  two decoded sides — whether those sides are a file's own text, a notebook
  cell's source, or facts composition stated about bytes;
- an **`ImageBay`** carries the same identity fields plus its two image
  representations, each holding exact bytes and the media type composition
  concluded.

There is no base class and no `kind` field distinguishing cases inside one type:
the type *is* the distinction, so a consumer that must act differently on a
picture writes two branches the type checker enforces, and one that must not
act differently writes none.

Review reconstructs an excerpt from either, the media endpoint serves the image
sides of the second, and `compose()` renders the first through the engine and
reduces the second to `MediaRef` sides. The union splits on content because
content is what the callers need.

Identity includes the frame. Frames are contiguous in document order, so a
consumer that wants frames groups consecutive items by their frame key and
heading, and no second structure or second call is needed.

It is an iterator because its two engine-free consumers are lookups. The media
endpoint wants one output of one cell of a two-hundred-cell notebook, and review
validation wants to know whether one key exists and what kind it is. Returning a
built structure would make both construct every bay to answer about one. This
is what makes `bays()` genuinely cheaper than `compose()` rather than merely
engine-free.

The decoded sides a text bay carries are not extra work done for review.
They are the same sides `compose()` renders, so nothing is decoded or parsed
twice.

Bytes never reach the wire. An `ImageBay` holds its payload while composing, so
the media endpoint can serve it; by the time it is serialized it carries only
the `MediaRef` describing that payload. The shapes
under **Bay kinds**
above are what survives serialization, not a second type. Python and TypeScript
declare that shape independently, as they do for everything else crossing this
boundary; neither declaration is generated from or derived from the other.

### Contexts and the envelope

The contexts split with the methods, and `ComposeContext` holds a
`BayContext` rather than extending one:

```python
@dataclass(frozen=True)
class BayContext:
    left_path: str | None
    right_path: str | None
    left_label: str
    right_label: str


@dataclass(frozen=True)
class ComposeContext:
    bays: BayContext
    renderer: DiffEngineProtocol
```

`BayContext` is what composing bays reads. The paths drive classification
and become each text bay's path hint, which per-format builders derive from —
a notebook cell's is `cell.py`, not the notebook's path. The labels become a text
bay's `left_label` and `right_label`, which no engine produces, so `bays()`
sets them.

Containment rather than extension earns two things beyond avoiding inheritance.
`compose()` passes `context.bays` down verbatim, so the bay inputs are
defined in exactly one place and no field is restated. And the envelope's paths
and labels are read from the nested context, so what composition classified on
and what the frontend receives cannot drift apart.

Only `compose()` can reach a renderer, which makes "this consumer runs no engine"
a type-level fact rather than a convention. One context with an optional renderer
would be the optional parameter that avoids handling an invariant, and is not the
shape.

`compose()` produces the whole envelope except its two pass-through fields. It
composes the frames, allocates the hunk indexes, aggregates the summary, and
decides the File-level expansion state, and it re-emits the paths and labels it
was given.

`display_name` and `file_kind` are not in either context, because composition
never reads them. They are also the only two envelope fields it does not
produce, and they are the same two the manifest already states — a File's name
and kind are settled before anyone asks for its diff. The HTTP boundary attaches
them, which is where naming a File belongs anyway, and when the frontend takes
both from the manifest instead they stop being attached at all rather than
needing to be removed from a context first.

This is deliberate, and it is one of the reasons the subsystem exists. A request
handler should load two byte sides, build one context, call `compose()`, and
return the result. It should not compute a display name, pick a file kind,
decide notebook routing, choose an expansion default, and assemble a payload
field by field, which is what `/api/file-diff` does today. Construction logic
belongs anywhere except a handler, and a context assembled field by field inside
one is the same juggling wearing a type. `ComposeContext` is built by a named
constructor taking plain facts: both paths, both side labels, and the renderer.
It takes no Room type. Composition needs two fields of `SnapshotMeta` and none
of the rest, and `dirdiff.formats` has no business importing the Room's
vocabulary to reach them.

That renderer comes from `engine()` in `dirdiff.engines`, which maps an
`EngineKind` name to a renderer and always returns one. Composition never
selects an engine, never names one, and never learns which one it was handed.

Consumers:

- review validation and line-pin matching call `bays()`;
- the media endpoint calls `bays()`;
- `/api/file-diff` calls `compose()`.

Purity is the required contract for both methods. The same two byte sides and
the same context produce the same frames, the same bay keys, and the same
order. Composition reads no clock, no database, no Room, and no file outside the
bytes it was given.

Classification is one path-only decision inside `bays()`. `.ipynb` is a
notebook, the image extension table names images, the blob extension table
names explicitly unsupported formats, and everything else is presumed text. A
one-sided File takes its present path's classification. Two present paths keep
a specialized classification only when they agree; a mixed rename is presumed
text. No parser participates in this decision.

Each classification calls one builder. Presumed text that contains NUL or is
not UTF-8 composes as blob facts with a warning. A claimed notebook remains a
notebook when one part is malformed: valid cells and outputs remain structured,
and the smallest rejected JSON value becomes its own text bay with a warning.
Only damage before a usable cell list exists produces one raw notebook bay.

A valid distinct `nbformat` cell id remains the cell's public key. A cell
without one uses `pseudocell:<source-sha256>:<source-occurrence>` as its frame
key, with `:src`, `:metadata`, and `:output:<index>` bay suffixes. Occurrence is
zero-based among cells with the same source hash and exists only to keep keys
unique. Source is the identity: finding the same source is a correct landing,
while an edited source changes its key and takes ordinary `bay_not_found`
placement. This is an explicit degradation: a source-derived coordinate is
weaker than an `nbformat` id, so the source bay warns, but preserving readable
cells is more useful than reducing the whole notebook to raw JSON.

Shape is part of loading the same way. The loader checks every field composition
reads, including `cell_type`, `source`, a code cell's `outputs`, text output
fields, and an offered `image/png` value. PNG data must be a string or string
list containing valid base64. The loader is silent about every field it does not
read and keeps document fields, cell metadata, and each raw output entry
verbatim. A malformed cell or output is preserved as canonical raw JSON in its
own warned bay; valid siblings remain structured. Missing or invalid execution
count merely removes the prompt number and warns. Only invalid UTF-8, invalid
JSON, a non-object document, or a missing/non-list `cells` value prevents a
usable cell list: that whole notebook side is then shown as one warned raw-text
bay, or as warned byte facts when it cannot decode. Nothing is coerced or
silently dropped.

### Notebook outputs

A cell's `outputs` is a list, and each entry is a `stream`, an `execute_result`,
a `display_data`, or an `error`. A `stream` carries its `text` and an `error`
its `traceback` strings; only `execute_result` and `display_data` carry a mime
bundle: `data` maps media type to payload, and one output holds several
representations of the same thing. A matplotlib figure is `image/png` beside a
`text/plain` summary line; a DataFrame is `text/html` beside its ASCII
rendering. Loading keeps each entry as the variant its `output_type` declares;
what a variant shows is decided while composing, not while loading.

That bundle is a ranked set of alternative representations by the notebook
format's own design, so choosing the richest representation the frontend can
render is that format's contract, not a fallback in the sense this project
otherwise rejects. The notebook builder chooses one representation per output and
emits the bay kind matching it. Stage 1 restricts that choice to text
representations, stage 2 adds `image`, and stage 3 widens it to prefer
`image/png` wherever an output offers one.

The choice is made once, while composing, and is never exposed as a frontend or
user selection. The backend emits one representation and that is what the
reviewer sees. If some output ever genuinely needs both, the builder emits two
bays, each with its own key and its own zero-based hunks. What must not
happen is the reviewer switching representations: that would replace a bay's
rows underneath the hunk coordinates derived from them, so a coordinate taken
before the switch would name a different row after it.

`text/html` outputs are arbitrary author-controlled HTML and `image/svg+xml` can
carry script. Neither is ever rendered as markup. Jupyter carries its
trusted-notebook signature machinery for exactly this reason, and dirdiff has no
equivalent notion of trust.

Output changes are admitted as they are today: raw output lists are compared and
any inequality makes the cell changed. Re-running a notebook does change every
executed cell, and saying so is correct. The builder does not suppress output
changes, filter `execution_count`, or otherwise rule that a difference is
uninteresting.

## Identity and coordinates

### Bay keys

`bay_key` is the universal sub-file coordinate. It extends the existing bay
concept already present in `LinePinTarget.bay` and in review text targets.

Bay identity extends a line coordinate inside the file; it is not a second
file identity.

One manifest entry remains one stable `FileCard` with one file index. Ordinary
a flatfile uses the bay key `"flatfile"`. Notebook cell source keeps its existing
public cell key. Bay keys are non-empty and unique within one composed diff.

A bay key names the classification that produced the bay, not the kind of the
bay. A blob File's facts bay is keyed `"blob"` and an image File's facts bay is
keyed `"image-facts"`, though both are `text` bays holding the same three facts.
Keying both `"facts"` would let a target survive a File changing classification,
landing a comment written about a picture on the bytes that replaced it — which
is the same reason `"image"` and `"flatfile"` are distinct keys.

A notebook cell's bays are keyed by that cell's key together with what the
bay is: its source, its metadata, or its position in the cell's output list.
All of those are facts about the notebook's structure, so a key does not depend
on which representation the builder chose and does not move when a later stage
chooses a different one. `nbformat` gives cells an `id` but gives outputs
no identity of their own, so an output index does shift when a cell gains or
loses an earlier output. That is ordinary target drift, handled by the review
matching rules, and needs no mechanism of its own.

### Hunk indexes

A hunk is a stop for Next and Previous, so what counts as one is a navigation
decision and the frontend owns it. Composition publishes the two facts the
decision reads and nothing more: which rows begin a changed run, numbered
bay-locally from zero, and what happened to each bay.

`change` is the semantic answer and only a format builder can give it. A cell
that moved and a cell whose output changed beyond its rendered text both produce
rows that are identical on both sides; nothing downstream can tell those apart.
The frontend colours from this value and infers nothing from row shapes.

`composedHunks()` in `FrameView` walks frames and bays in document order and
produces each bay's stop list. A hunk coordinate is the pair of the owning
bay's key and the bay-local index; there is no file-wide numbering on
either side:

- a `text` bay contributes the wire's own `hunk_index` values verbatim — one
  per row that begins a changed run, numbered from zero in row order;
- a bay whose `change` is anything but `unchanged`, contributing no such row,
  takes one stop of its own at index zero. This covers `image`, future kinds,
  and a `text` bay whose rendered text is identical on both
  sides. The two rules are exclusive per bay, so the stop cannot collide
  with a row's index.

The second rule is the reachability invariant, not a convenience: a change the
reviewer cannot land on with Next hunk is a hidden change. `FileCard` already
answers this one level up, and by the same hand — a File with no hunks exposes
its own header as a pseudo-hunk of `kind: "zero"`, and a collapsed File writes
`kind: "skip"` anchors. Bays follow the same rule from the same place.

Nothing renumbers the payload. A row keeps the bay-local index composition
gave it, and `TextDiffGrid` receives its bay's key so the DOM carries
`(bay_key, row.hunk_index)` exactly as the wire said it.

This replaced a backend allocator that shipped `hunk_count`, and briefly a
frontend base-offset scheme that flattened the bays into one file-wide
numbering. The flattening died because it forced every renderer to agree on
the same arithmetic — the virtual representation once anchored bay-local
indexes untranslated beside base-offset rich anchors, which is exactly the
class of bug a verbatim coordinate cannot have. The remaining safeguard is the
wire contract itself: `FullFileRenderer` asserts that a bay whose rows carry
the stops numbers them zero through n-1 in row order.

A bay that changed without producing a changed row — an output whose raw JSON
differs while its chosen text representation does not — must say so in its own
`label`. The composer knows why it emitted the bay, and the label is the
backend-authored text the reviewer reads when they land on it. Landing on a
collapsed unchanged grid with no explanation is the failure this rule exists to
prevent.

Bays therefore participate in Next and Previous hunk navigation like any other
target. They introduce no new selection path: `selectHunk()` keeps exactly the
five lint-enforced direct callers named in [`navigation.md`](navigation.md),
and no bay widget calls it.

## Worked shapes

| File | Frames | Bays |
| --- | --- | --- |
| flatfile | one, no heading | one `text` bay keyed `"flatfile"` |
| notebook | one per cell, plus one for notebook metadata | cell source `text` keyed by cell key, plus one bay for changed cell metadata and one per changed output |
| image | one, no heading | one `image` bay, a metadata `text` bay keyed `"image-metadata"`, and a facts `text` bay keyed `"image-facts"` |
| blob | one, no heading | one `text` bay of its facts keyed `"blob"` |
| hybrid notebook | one per cell, plus one for notebook metadata | the same shape, with `image` bays for the outputs whose bundle offers one |
| symlink | one | one `text` bay holding the link target |

The notebook shape is not held to today's visible output. Notebook support
predates this project's pivot to ordinary code review, has no users, and is
covered by a single smoke test asserting `render_kind == "notebook"` — a field
this design deletes. Cell keys stay stable because review targets and pin URLs
name them; the cards, chrome, and grid arrangement do not, and stage 1 is free to
emit whatever frame and bay shape a notebook actually wants.

The hybrid case is the point of the design, and if stage 1 chooses output
representations as described it costs almost nothing later: showing a rendered
plot beside its source is that same choice preferring `image/png` over
`text/plain` for one output. It is not a new format and not a new frontend
renderer. It does need the media addressing recorded below.

Symlinks are stage 4. Their shape needs nothing from this design that images
and notebooks do not already need; what they need is the recorded file mode,
which capture does not carry today.

## Media transport

One endpoint serves composed image bytes:

```text
GET /api/file-media?snapshot_id=...&bay_key=...&side=left|right&left_path=...&right_path=...
```

The Snapshot id and nullable File-path pair identify the same composed File as
`/api/file-diff`. The required bay key selects one image bay inside that File,
and the side selects its old or new representation. A renamed File needs both
paths, and a notebook needs the bay key because several outputs can carry PNGs.
The response is exactly the selected `MediaSide.data` under the media type
composition concluded. For an image File those are the captured File bytes. For
a notebook they are the bytes strictly decoded from the captured output's
base64. No second opinion about media type is formed at the boundary and no
engine runs to serve a picture. Snapshot ids are never reused, so the response
is declared immutable and cached outright: `Cache-Control: private,
max-age=31536000, immutable`.

The endpoint reads through the existing Room interface over the capture store
that already holds exact bytes on disk, then asks `bays()` which `ImageBay` the
File composes into — the same engine-free call review validation makes, not a
second mechanism. Capture and publication stores remain private behind Room, as
described in [`rooms.md`](rooms.md). No copy, materialized directory, or second
content store is introduced.

It serves `image` bays and nothing else, because they are the only bays that
carry bytes. Its caller is the `<img src>` in the image widget. A blob File
composes no bay it can be asked about, which is the cost recorded under
[Blob is a classification, not a kind](#blob-is-a-classification-not-a-kind).

A missing bay, a bay that is not an image, and a side with no image
representation are refused. None is answered with empty bytes: an empty response
would be a believable picture of nothing, and there is no such thing.

Bay payloads never inline bytes. A `MediaRef` digest identifies the content the
endpoint will serve for that side.

The bay key closes the ambiguity that existed before hybrid notebooks. A cell's
frame may hold an image bay beside its source bay, and several cells' outputs
share one File pair. The endpoint selects among the same `bays()` enumeration
that produced the diff payload; it does not parse notebook outputs a second way.

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
then collapsed set of bays for the original file.

**Not implemented.** Today `image` and `blob` are two kinds sharing one
`MediaBayView` and one Python `MediaBay` with a `kind` field, and each composes
exactly one bay for a whole File. What follows is the target, and the rest of
this document is written to it.

Taking the paragraph above seriously removes a kind rather than adding one. A
blob File's metadata bay is a text bay, so blob is a File classification and not
something the frontend renders; an image File's metadata bay is a text bay too,
beside its picture. That leaves two kinds, and the frame walk is the only place
either is examined:

```tsx
// FrameView hands each widget its own arm of the union, already narrowed.
switch (bay.kind_data.kind) {
  case "text":
    return <TextDiffGrid bay={bay} text={bay.kind_data} {...rest} />;
  case "image":
    return <ImageBayView bay={bay} image={bay.kind_data} {...rest} />;
}
```

Every widget takes the same two things: the `BayPayload`, for identity, label,
collapse state, and `change`, and its own arm, for content. None of them can ask
what kind it is, because the question was answered before it mounted and there
is no `kind` prop to read. A new kind is a `case` and a module under
`hud/fileCard/grids/<kind>/`, and nothing else moves.

There is no `MediaBayView` and no `BlobBayView`. `ImageBayView` calls
`fileMediaUrl()` to fill an `<img src>` and is the only widget that fetches
anything; a blob File's facts arrive as rows in a `text` bay and `TextDiffGrid`
draws them like any other rows, folding, highlighting, and hunk-marking included
because there is nothing to exempt.

The shared-code question the paragraph above raises answers itself for now.
`TextDiffGrid` and `ImageBayView` share the line-host DOM every bay hosting a
review line writes — a `data-bay-key` wrapper, a `data-review-bay` grid, a line
container, and a `.line-no` beside its `.line-code` — because `ImageBayView`
hosts the pseudo-line a comment on the picture lands on. Whether that is a
shared component or two writers is decided when the split lands; if a component
appears, it is a review-line host any bay kind may use, never a media base
class.

## Review and line pins

Every bay kind must eventually be commentable. "You must be able to comment on
everything" is the standard this subsystem is held to.

### Text bays

Unchanged. A text review target keeps its File pair, bay, selected side, and
one-based inclusive line range, exactly as specified in
[`reviews.md`](reviews.md). Line pins keep their URL identity and their
`bay` field. Ordinary text supplies the ordinary bay; notebook cell source
supplies its cell key.

### Composer replay

Review validation calls `bays()` on the captured Snapshot bytes and recomputes
exactly the bay keys that were emitted. This generalizes today's
`rendered_notebook_cell_pairs` bridge into one rule that holds for every format:
the bay keys a review target may name are the bay keys the composer
produces, never an independent approximation of renderer visibility.

`reviews.md`'s rule is preserved rather than weakened. It states that
original-excerpt reconstruction does not call or expose a diff engine, and
`bays()` cannot reach one: a `BayContext` carries no renderer. The two
entry points exist for exactly this reason. Validation gets composer-authored
bay identity without an engine, and there is still one implementation of that
identity rather than a validation-side approximation of it, which is what
`rendered_notebook_cell_pairs` already achieves for notebooks today.

### Image review targets

An `image` bay exposes exactly one pseudo-line. A review target against it is
an ordinary text target: the same File pair, the same bay, the same selected
side, and the one-based inclusive range `1..1`. Line pins on image bays use
the same coordinate.

`image` is the only bay kind that needs this, and that is its whole
justification: a picture has no lines of its own, and "this icon is too dark"
belongs on the picture rather than on the digest row of the facts bay sitting
beside it in the same frame. Every other bay a File composes — a blob File's
facts, an image's facts, a link's target, a cell's source — is a `text` bay with
real lines and needs nothing from this section.

That pseudo-line is a placeholder, but review renders it from the image side's
own facts — `<media type>, <n> bytes, sha256 <digest>` — so the ordinary origin
machinery does real work on it: replacing the content changes the line, the
region hash retained at creation stops matching, and the Thread is reported
outdated, which is what a comment on a replaced image deserves. Its parser path
hint is `media`, which claims no language, so structural matching stays over the
whole line rather than following whatever the File's real extension implies.

One target shape therefore runs through validation, placement, History, and every
frontend path that handles a target. The pin URL shape and the whole Comment input
machinery are unchanged, and mounting a comment trigger on its widget is the only
review work a new bay kind owes.

The costs are real, and are accepted rather than hidden:

- the persisted target carries a line number that describes no line in the file;
- a range other than `1..1` against an `image` bay is invalid, and validation
  rejects it — rather than clamping it — using the bay's kind from `bays()`.
  Review branches on kind twice, and nowhere else: once to build the
  pseudo-line, once to decide which ranges are valid. Composition does not
  branch at all — it hands over bays, and review reads them. Everything between
  those two branches — origin matching, placement, excerpt reads, History, and
  the frontend — sees one line of text and asks nothing about where it came
  from.

The alternative was a tagged bay target addressing a whole bay with no line
range. It persists honestly, at the price of a second target variant through
validation, placement, History, and the frontend, a per-bay-kind meaning for
`region_changed` in review matching, and a second empty-range case in excerpt
reads. If the fictional coordinate ever leaks somewhere it does damage, that is
the shape to revisit.

## Module layout

### Backend

A new package `dirdiff.formats`, following the project's package rules:

- `base.py` holds the bay and frame contracts, the contexts, the shared
  text-bay renderer, and the hunk allocator. Sibling modules import those
  internals from `base.py`.
- `composer.py` holds the `Composer` class and path classification. It is
  the one module that imports the per-format siblings, which keeps `base.py` free
  of them and the import graph acyclic.
- one sibling module per format holding that format's bay builder:
  `flatfile.py`, `notebook.py`, `image.py`, `blob.py`, and later ones. None of
  them is a `Composer`. What each owns is the classification question it answers
  — "does the repository call this a picture" against "nothing else claimed it"
  — of which only the second is terminal, and neither imports the other.
- `ImageBay`, `MediaSide`, and `MediaRef` belong to `base.py`, not to
  `image.py`. `ImageBay` is an arm of the `Bay` union `base.py` defines, and the
  facts text and the `MediaRef` reduction both take a `MediaSide`; putting the
  types in `image.py` would make `base.py` import a sibling, which the rule
  above forbids. `image.py` constructs whole-File pictures and `notebook.py`
  constructs PNG output bays; the media endpoint reaches both through the
  facade.
- the facts text — media type, size, digest, one per line — is built in
  `base.py`. `image.py` uses it for the facts bay beside its picture and
  `blob.py` for its only bay, which is two callers in sibling modules and
  therefore belongs there rather than in either of them.
- `__init__.py` is a facade of re-exports only.
- `dirdiff.server.diff` calls the facade and never a submodule.

`dirdiff.notebooks` dissolves into `dirdiff.formats.notebook`. The server's
`build_text_file_payload` and `build_notebook_file_payload_if_applicable`
dissolve into the shared text-bay renderer and path classification.

### Frontend

The `frontend/src/hud/fileCard/` directory, behind its `FileCard.tsx` facade:

- `FrameView.tsx`, the generic frame renderer, which walks frames and dispatches
  each bay to its widget by `kind`;
- one widget per bay kind, in `hud/fileCard/grids/<kind>/`: `TextDiffGrid` in
  `grids/text/` and `ImageBayView` in `grids/image/`. One directory per kind and
  none shared by two, under
  [Frontend representation](#frontend-representation).

`FileBody` stopped switching on `render_kind` and mounts the frame renderer.
`NotebookFile.tsx` dissolved into it.

A widget needing a third-party rendering library would be the first thing in this
frontend to carry that weight. No planned bay kind does. If one is ever
proposed, the vendoring question is decided before the kind is, not after.

## Preserved invariants

- Engines never learn about formats, frames, or bays.
- Rendering enrichment is unchanged: the same weaving of engine tokens and syntax
  spans, the same fold hints, the same lossless parts.
- One manifest entry is one `FileCard` is one file index.
- Bay identity extends a line coordinate inside the file; it is not a second
  file identity.
- Bay identity never depends on engine output. Every format's frames and
  bays are enumerated by `Composer.bays()`, which has no renderer in reach.
  A format that cannot be enumerated without rendering rows is a reason to stop
  and reconsider, not to widen what validation may call.
- Backend row order is authoritative.
- `selectHunk()` keeps exactly five lint-enforced direct callers. No builder,
  frame renderer, or bay widget introduces a hunk-selection path.
- Hunk indexes are bay-local. Composition numbers each bay's own stops from zero
  and stops there; the frontend walks bays in document order to build the File's
  navigable sequence. There is no file-wide numbering on either side.
- Every change is reachable. A changed bay always consumes at least one hunk
  index, so Next and Previous hunk visit it. Nothing is dropped, filtered, or
  left unreachable because it looks like noise.
- Rich and virtual alternation applies to every composed diff, one text bay
  at a time. The older rules confined it to ordinary text and then to whole
  files, not because the representation is text-specific: a virtual bay is
  that bay's rows as searchable plain text plus its row-carried hunk
  coordinates, and every text bay has both. A bay whose change produced no
  row keeps the stop in its always-mounted chrome, so virtualizing never
  drops the changes that are hardest to see.
- Damage boundaries are unchanged: `FileRendererBoundary` still contains one
  File body, and an unexpected widget failure is terminal local damage for that
  File, not a backend File error.
- The media endpoint serves only bytes carried by the selected composed image
  bay. It creates no store, copy, or mutable path.

## Implementation stages

### Stage 1 — the composed-diff shape and simple notebooks

Reshape text and notebook responses into the composed diff. Extract
`dirdiff.formats` from `notebooks.py` and the server's payload builders. Dissolve
`NotebookFile` into the generic frame renderer. Generalize the review bridge to
`bays()`, which is where `rendered_notebook_cell_pairs` ends up.

The notebook builder emits one frame per cell — every cell, in document order —
headed by that cell's Jupyter prompt — `In [4]`, or `In [ ]` for a cell never
executed: the new document's count when the cell exists there, the old one for
a removed cell. Markdown and raw cells have no prompt and no heading. The
frame's body is a `text` bay holding the cell source, which is
always shown; attached to it are a `text` bay for changed cell metadata as
canonical JSON and a `text` bay per changed output carrying that output's
text representation, both collapsible and collapsed by default.

A moved cell reports the move as its `change`, carrying the prompt it wore on
each side — both `null` for prose, which has no prompt. When it moved without
an edit its source composes no changed row, so it carries a stop itself and
its `detail` says the rows show nothing. Nothing here needs bytes, so this
stage adds no endpoint.

This stage opens the `notebook/` preset set described under **Goals and
expectations** and fills the `basic` cases it can reach without media — cell
source, structure, metadata, and the text outputs — and the `invalid` case. The
plot case is added here too, showing its text representation, and changes shape
rather than appearing when stage 3 lands.

### Stage 2 — media, images, binaries

Landed, with one part of it since decided to be wrong. `/api/file-media` and the
`image` bay kind exist and are described above as running code. Everything this
stage serves is the whole content of one captured side, so File-pair-and-side
addressing is sufficient here. The text question changed behavior with it:
non-text, non-image content stopped raising and made classification total.

What landed as a third bay kind should be a `text` bay of a blob File's facts,
and an image File should compose a facts bay beside its picture. That is
[Blob is a classification, not a kind](#blob-is-a-classification-not-a-kind),
and it is a correction to this stage rather than a stage of its own: it removes
a kind, a payload arm, and a widget, and adds no endpoint and no coordinate.

The stage filled the `formats/basic` image and blob cases; the symlink cases
in that set wait for stage 4, which is the stage that carries the recorded file
mode. Those fixtures were unreachable from the HUD's preset picker when the
stage landed, and two separate changes to the preset path made them reachable:
the backend now reads a fixture holding only `new.*` as an addition and one
holding only `old.*` as a deletion, and the catalog set is now the directory
listing under the presets root rather than a closed set spelled in code.

### Stage 3 — hybrid notebooks

Landed. The notebook loader strictly decodes `image/png` MIME entries and the
builder prefers those bytes over `text/plain`. When only one output side offers
a PNG, the shared image bay keeps the other side explicitly absent rather than
substituting text or the opposite picture.

`/api/file-media` now requires the image bay key in addition to Snapshot, File
pair, and side. This selects among several notebook outputs through the same
`bays()` enumeration used for composition. Stage 3 added no bay kind, widget,
endpoint, frame rule, or hunk-allocation path.

### Stage 4 — symlinks

A symlink composes as one frame holding a `text` bay whose content is the link
target.

The work is not in the composer. `GitBackend.load_version` loads a symlink as Git
records it — a blob holding the raw target string — so the captured bytes of a
symlink and of an ordinary file containing that same string are identical, and
nothing the composer receives distinguishes them. Stage 4 is the stage that
carries the recorded mode from capture through to `BayContext`. Labelling the
bay and its frame is the composer's small part, and it cannot happen first.

### Postponed — 3D models

A 3D model viewer is not a planned stage. Nothing in the design depends on it, no
other stage is blocked by it, and it is the only prospective bay kind needing
a vendored third-party library. It is recorded in this document as a shape the
bay model admits, not as work.

### Documents each stage must update

This document lists them; it does not edit them.

| Stage | Document | Section |
| --- | --- | --- |
| 1 | `file-meat.md` | FileBody dispatch; Notebook rendering; Rich and virtual bays |
| 1 | `backend_index.md` | the `dirdiff.notebooks` entry and the application-flow diagram |
| 1 | `frontend_index.md` | source layout, the `NotebookFile.tsx` entry, and the direct-interface table |
| 1 | `reviews.md` | the notebook-specific bridge, now the format-independent `bays()` rule |
| 2 | `backend_index.md`, `frontend_index.md` | the media endpoint and the bay widgets |
| 2 | `file-meat.md` | the blob File behavior that no longer produces an error `LazyFile` |
| 3 | `backend_index.md` | the media endpoint's addressing, once it serves sub-file bytes |
| 2 | `navigation.md` | non-text hunk targets, first visited when image bays exist |
| 2 | `reviews.md` | the pseudo-line target for `image` bays |
| 4 | `rooms.md`, `backend_index.md` | the recorded file mode that capture carries to composition |

## Goals and expectations

The subsystem is done when a reviewer can open each case below and see something
correct and useful. They are written as preset sets so the goals are runnable
rather than aspirational, and they follow the existing preset layout —
`<set>/<category>/<case>/old.<ext>` and `new.<ext>`.

Two sets, because notebooks alone outnumber everything else.

### Presets must be real

Every fixture is a real artifact. Nothing is generated to look plausible.
Every piece of code is hand-written and shows a real example of code
that a human would write in a jupyter notebook.
No procedurally generated nonsense.

**Notebooks are multi-cell.** A case is a realistic notebook — prose, imports,
data, computation, a plot, notes — not a minimal pair holding the single cell
under test. Most cases edit real code and carry the outputs produced by that
edit. A few clearly named edge cases isolate metadata, malformed content, or
execution state when a code edit would obscure the behavior under review. A
one-cell fixture cannot show that frames follow document order, that only
changed cells compose a frame, or that an untouched cell stays out of the way,
which is most of what the notebook shape has to get right. The unchanged cells
are the point.

**Notebooks are executed.** The cells hold real code doing something small and
real — a bit of arithmetic, a plot, a table, a deliberate exception — and the
outputs come from running it on a kernel. How the file gets authored does not
matter, and nobody has to open the Jupyter UI to do it: `jupyter nbconvert
--execute` and `nbclient` both execute a notebook headlessly. This project
exists partly so that nobody has to sit in that interface, and requiring it here
would be a poor joke.

What must be real is the result. The fixture's outputs are the ones the kernel
produced: its execution counts, its base64 PNG, its traceback with the escape
codes still in it, its mimebundles carrying whatever media types the libraries
actually emitted. Whatever is needed to run a notebook goes in the `dev`
dependency group.

`uv --no-cache run tests/presets/notebook/execute.py` is the ordinary way to
refresh the set. The script discovers the notebook pairs, executes each included
file once with `nbclient`, omits execution timing metadata, and writes the result
in place. Its short `EXCLUDED_NOTEBOOKS` table names every fixture whose intended
state forbids execution: malformed or invalid content, and the deliberately
unexecuted side of an output-presence case. The expected-error fixture executes
with only its deliberate `IndexError` admitted; every other execution error
stops the script. Notebook code seeds its own random inputs. Ordinary plot cells
use pyplot imports and end with `plt.show()`; backend magics remain only where a
hand-edited history recorded one. The script does not generate cells, normalize
results, retry failures, or hide kernel errors.

**Images are downloaded, and their licence permits redistribution.** Public
domain or CC0, with the source and licence recorded beside the fixture. The same
goes for any other binary content the `blob` cases need.

**Symlinks are symlinks**, created with `ln -s`, not files describing a link.

The reason is the whole point of the exercise. A hand-written mimebundle
contains what its author expected notebooks to contain, so a fixture built that
way can only confirm an assumption. A real one can contradict it, which is the
only thing a fixture is for. This subsystem exists because nobody here has used
notebooks in anger yet; fabricating their contents would preserve exactly the
ignorance it is meant to remove.

### `notebook/`

Notebooks get their own set: they carry the most cases, and they are the format
this subsystem was written to make usable.

| Category | Case | What it must show |
| --- | --- | --- |
| `basic` | a code cell's source edited | an ordinary text diff, folded and highlighted like any Python |
| `basic` | a markdown cell's prose edited | the same, for a cell with no heading to fold on |
| `basic` | a raw cell and nearby code edited | both ordinary diffs, plus the code's changed output |
| `basic` | a cell added, a cell removed | one frame each, in document order, with correct hunk indexes |
| `basic` | cells reordered and a plot adjusted | frames follow the notebook, cell keys stay put, and the code-driven PNG changes |
| `basic` | an untouched cell beside a changed one | present, collapsed, carrying no hunk, and still openable |
| `basic` | stream output changed | a text bay holding the changed output |
| `basic` | an error traceback appears | the traceback as text, its escape codes not interpreted |
| `basic` | cell metadata changed | a collapsed canonical-JSON bay without claiming source changed |
| `rich` | plot type changed | the code diff and its bar-to-line PNG change |
| `rich` | wettest day highlighted | data-driven colors and the resulting PNG change |
| `rich` | rolling average added | a second plotted series, legend, and the resulting PNG change |
| `unchanged` | plot outputs added, plot outputs removed | source-identical execution-state changes with the present PNG on the correct side and an explicit absent representation opposite it |
| `invalid` | missing cell ids and malformed output | readable degraded frames instead of losing valid source |
| `invalid` | `.ipynb` that is not valid notebook JSON | an ordinary text diff of the file, because that is what the bytes are |

The `rich` group contains only code-driven image changes, separate from the
source, metadata, and text-output cases in `basic`. Each plot's image difference
has a visible source difference beside it. These cases changed shape across
stages: text bays in stage 1 and `image` bays from stage 3.

The `unchanged` group holds source-identical execution-state edge cases. Its
added and removed cases pair the same deterministic cell before and after
execution, so they exercise a missing image representation without fabricating
substitute content. Malformed notebook structure belongs in `invalid`, whether
the loader can preserve readable cells or must show the whole file as text.

### `formats/`

Everything whose composition is decided by what the file is rather than by what
is inside it. The name is imperfect — a notebook is a format too — and it is
chosen for being the least misleading available, not for being right.

| Category | Case | What it must show |
| --- | --- | --- |
| `basic` | an image changed on both sides | both sides rendered, the facts bay showing size and digest changed, two stops consumed |
| `basic` | an image added, an image removed | the one captured side, with the other absent rather than blank |
| `basic` | non-text, non-image content changed | a diff of the facts, size and digest marked changed, and no error `LazyFile` |
| `basic` | the link target changed | the target as text |
| `basic` | a symlink replaced by a regular file, and the reverse | that the kind changed, not only that bytes did |
| `invalid` | a broken symlink | its recorded target, because that is what Git stores |

The four non-symlink cases exist, each with its source and licence recorded
beside it, and `tests/formats/test_media.py` composes them and checks the bytes
it gets back. The three symlink rows wait for stage 4.

Two fixture problems belong to whoever builds those rows. The preset README says
both sides of a case share an extension, which suits images and binaries and
does not suit symlinks. And a symlink fixture is only a fixture if nothing in
the test path follows it, which is a property of the loader, not of the file.

A third problem was making this set browsable, and it is solved: the preset
backend reads a one-sided case as an addition or a deletion, and the catalog a
`preset.toml` beside the group directories names is listed by `/api/presets`,
so `Format Presets` is one of the buttons the Preset Tab draws.

### Handlers should be handlers

This one is a judgement, not a measurement, but it is statable. A route body
should read as HTTP work and nothing else.

For `/api/file-diff` that is: recover the Room, load the two captured byte
sides, build one `ComposeContext`, call `compose()`, and return what it gives
back. Attaching the display name and file kind stays until the TODO in
`dirdiff.server.diff` is resolved, so the target is minimal rather than empty.

`render_loaded_snapshot_file` now reads as that: it checks the capture error,
loads the two byte sides, builds one `ComposeContext`, calls `compose()`, and
attaches the display name and file kind. Decoding, engine selection, and payload
assembly moved into composition. The two attached fields stay until the TODO in
`dirdiff.server.diff` is resolved, so the handler is minimal rather than empty.

The same standard applies to the route this design added. `/api/file-media`
resolves a Snapshot, calls `bays()`, selects the required key, and writes one
side's bytes with the media type composition concluded. Review validation asks
`bays()` whether a key exists and what kind it is. Neither grew a second notion
of what a file is.

### What none of these may require

Frontend remains responsible for visuals and interaction, backend remains
responsible for data, structure and semantics.
Hence formats/ folder in backend code, but grids/ folder in frontend code.
Backend parses files, frontend paints bays.

## TODO

Work that is not built. Nothing below describes the current implementation.

## Follow ups

### Shrink row parts on the wire

Parts are 81.5% of a notebook response and 85.3% of a text one. Four of a part's
five keys almost always hold their default, so one `{` costs 121 bytes. Omitting
defaults is lossless and takes a notebook 460 KiB → 209 KiB and a large source
file 4065 KiB → 1706 KiB; deduplicating equal `left_parts`/`right_parts` reaches
186 KiB and 1226 KiB.

Not a formats concern and it predates the subsystem; it changes the wire for
every File of every kind. End-to-end time is unmeasured: time request to first
painted row on one fixture before deciding.

### Write sane documentation
Adopt Rust styleguide, and rewrite all comments around that style-guide
