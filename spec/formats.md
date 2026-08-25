# Formats and composed diffs

## Status of this document

This is the living description of the formats subsystem. It is not an approval
gate and not a frozen contract.

Composition, the flatfile terminal, notebook composition, and the composed-diff
wire shape describe running code and are corrected to it. The image and binary
bay kinds, the blob endpoint, and the hybrid-notebook shape are not implemented;
those sections describe intent, and are marked where they appear.

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
  frames: Frame[];
};

type Frame = {
  frame_key: string;
  heading: string | null;
  bays: Bay[];
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

Target shape, not existing code:

```ts
type BayBase = {
  bay_key: string;
  label: string;
  detail: string | null;
  collapsible: boolean;
  default_expanded: boolean;
  change:
    | { kind: "added" | "removed" | "changed" | "unchanged" }
    | { kind: "moved"; from_heading: string | null; to_heading: string | null };
};

type Bay = BayBase &
  (
    | {
        kind: "text";
        left_label: string;
        right_label: string;
        rows: DiffRow[];
        fold_hints: FoldHint[];
        stats: BayStats;
        engine_warning: EngineWarning | null;
      }
    | { kind: "image"; left: BlobRef | null; right: BlobRef | null }
    | { kind: "binary"; left: BlobRef | null; right: BlobRef | null }
  );

type BlobRef = { media_type: string; byte_size: number; digest: string };
```

The intersection keeps the fields every bay carries in one place. It also
matches existing frontend practice: `api.ts` already declares its four
`*DiffParams` types over a shared `RepoBackedDiffParams` this way. Flattening the
union so each variant repeats those fields is a change to make everywhere or
nowhere, and it is not worth a sweep.

| Kind | Contents | Rendered by |
| --- | --- | --- |
| `text` | decorated rows, fold hints, per-bay stats, optional engine warning | the existing `TextDiffGrid` |
| `image` | optional left/right `BlobRef` | the image widget |
| `binary` | optional left/right `BlobRef` | the binary widget |

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

`image` bays carry no bytes. `BlobRef` describes the captured side; the widget
requests the bytes from the blob endpoint below.

`binary` is the defined contract for content the frontend cannot render, not a
fallback: before and after digests and sizes, in the spirit of what `git diff`
prints for binary files. It replaces today's `DirdiffError` from
`decode_text_content` for non-text, non-image content. That is an approved
behavior change: a binary File currently becomes an error `LazyFile` and will
instead become an ordinary composed diff with one `binary` bay.

Future kinds arrive as new bay kinds with their own widget. Nothing about
frames, hunk allocation, or the composed-diff envelope changes to admit them. No
such kind is currently planned; a 3D model viewer is the shape most often
imagined for one, and it is postponed.

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
Classification always reaches an answer because `binary` is terminal.

### What `bays()` yields

A `Bay` here is the same bay the frontend eventually receives, before
rendering and before serialization. It is tagged by what the bay is made of,
because that is the distinction its consumers act on:

- a **text** bay carries its identity, its label, its expansion state, and
  its two decoded sides;
- a **blob** bay carries the same identity fields plus its bytes and media
  type.

Review reconstructs an excerpt from one or the other, the blob endpoint serves
the second, and `compose()` renders the first through the engine and reduces the
second to a `BlobRef`. The `kind` a widget dispatches on rides along; the union
splits on content because content is what the callers need.

Identity includes the frame. Frames are contiguous in document order, so a
consumer that wants frames groups consecutive items by their frame key and
heading, and no second structure or second call is needed.

It is an iterator because its two engine-free consumers are lookups. The blob
endpoint wants one output of one cell of a two-hundred-cell notebook, and review
validation wants to know whether one key exists and what kind it is. Returning a
built structure would make both construct every bay to answer about one. This
is what makes `bays()` genuinely cheaper than `compose()` rather than merely
engine-free.

The decoded sides a text bay carries are not extra work done for review.
They are the same sides `compose()` renders, so nothing is decoded or parsed
twice.

Bytes never reach the wire. A blob bay holds its payload while composing, so
the blob endpoint can serve it; by the time the bay is serialized it carries
only the `BlobRef` describing that payload. The two shapes under **Bay kinds**
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
- the blob endpoint calls `bays()`;
- `/api/file-diff` calls `compose()`.

Purity is the required contract for both methods. The same two byte sides and
the same context produce the same frames, the same bay keys, and the same
order. Composition reads no clock, no database, no Room, and no file outside the
bytes it was given.

Classification is an explicit ordered check inside `bays()`, written in one
place, not a registry, plugin table, or media-type map:

1. notebook, when a path suffix says `.ipynb` and every present side loads as
   notebook JSON;
2. flatfile, the terminal every other File reaches.

A `.ipynb` whose bytes do not load is not a notebook; it falls through to the
flatfile terminal. An absent side is not a failure to load — the File was added
or removed, and the notebook builder reports that side absent. The image check
and the terminal `binary` bay are later stages; when they land they take their
places in this list, before and after `text` respectively.

The check owns the decision completely, and each step hands its bay builder
the value it already validated: the notebook builder takes parsed notebooks, the
flatfile builder takes bytes and decodes them. A builder cannot be handed the
wrong format, because its parameters do not admit one. A binary or non-UTF-8
side raises `DirdiffError` at the flatfile builder's decode boundary, which the
request handler reports as an unsupported file diff.

Cell identity is part of loading. A notebook must give every cell a distinct
`id` in the schema's `cell_id` shape (1 to 64 characters, ASCII letters,
digits, `-`, and `_`), because that id is the bay key review targets and line
pins persist; a document that cannot supply one per cell has no durable
coordinate to store, and inventing one from cell contents would not survive an
edit to those contents. Such a document is not a notebook and takes the same
route as one whose JSON does not parse. `nbformat` 4.5 and later always write
ids.

Shape is part of loading the same way. The loader is strict about every field
composition reads — `cell_type`, `source`, a code cell's `outputs` and the
output fields it reads text from — and silent about every field it does not,
keeping the document mapping, cell metadata, and each raw output entry
verbatim. Strict means the shape the `nbformat` v4.5 schema gives the field,
its closed cell and output type sets included. Nothing is coerced and nothing
is dropped: a document violating a read field's `nbformat` shape is not a
notebook, and falls through to text, where the difference stays visible as
raw JSON instead of being hidden behind an invented default.

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
  takes one stop of its own at index zero. This covers `image`, `binary`,
  future kinds, and a `text` bay whose rendered text is identical on both
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
four direct callers named in [`navigation.md`](navigation.md), and no bay
widget calls it.

## Worked shapes

| File | Frames | Bays |
| --- | --- | --- |
| flatfile | one, no heading | one `text` bay keyed `"flatfile"` |
| notebook | one per cell, plus one for notebook metadata | cell source `text` keyed by cell key, plus one bay for changed cell metadata and one per changed output |
| image | one, no heading | one `image` bay |
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
renderer. It does need the blob addressing recorded below.

Symlinks are stage 4. Their shape needs nothing from this design that images
and notebooks do not already need; what they need is the recorded file mode,
which capture does not carry today.

## Blob transport

A new endpoint serves captured bytes:

```text
GET /api/file-blob?snapshot_id=...&side=left|right&path=...
```

It is addressed by Snapshot id, side, and repository path — the same addressing
`/api/file-diff` already uses — and returns the exact captured Snapshot bytes
with the correct `Content-Type` for the media type. Snapshots are immutable, so
responses are stable and cacheable.

The endpoint reads through the existing Room interface over the capture store
that already holds exact bytes on disk. Capture and publication stores remain
private behind Room, as described in [`rooms.md`](rooms.md). No copy, materialized
directory, or second content store is introduced.

Bay payloads never inline bytes. A `BlobRef` digest identifies the content the
endpoint will serve for that side.

This addressing has a known gap, and it is a stage 3 problem rather than a stage
2 one. An `image` bay for an image File names bytes that really are a file at
a path, which this endpoint already serves. A notebook's rendered plot is base64
inside one output of one cell of the `.ipynb` and has no path of its own.
Serving notebook-embedded bytes therefore needs a sub-file coordinate in the
endpoint, resolved by calling `bays()` on the captured bytes and reading the
payload the named bay holds. That is the same engine-free call review
validation makes, not a second mechanism. The endpoint shape belongs to stage 3,
the first stage that must serve such bytes.

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

### Non-text review targets

A non-text bay exposes exactly one pseudo-line. A review target against it is
an ordinary text target: the same File pair, the same bay, the same selected
side, and the one-based inclusive range `1..1`. Line pins on non-text bays use
the same coordinate.

One target shape therefore runs through validation, placement, History, and every
frontend path that handles a target. The pin URL shape and the whole Comment input
machinery are unchanged, and mounting a comment trigger on its widget is the only
review work a new bay kind owes.

The costs are real, and are accepted rather than hidden:

- the persisted target carries a line number that describes no line;
- a range other than `1..1` against a non-text bay is invalid, and validation
  rejects it using the bay's kind from `bays()`;
- excerpt reads still branch on kind. Review context for a non-text bay is
  reconstructed from the blob digests rather than from decoded text, so the
  pseudo-line buys one target variant, not kind-blindness.

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
- `composer.py` holds the `Composer` class and the ordered classification. It is
  the one module that imports the per-format siblings, which keeps `base.py` free
  of them and the import graph acyclic.
- one sibling module per format holding that format's bay builder:
  `notebook.py`, `image.py`, and later ones. None of them is a `Composer`.
- `__init__.py` is a facade of re-exports only.
- `server.py` calls the facade and never a submodule.

`dirdiff.notebooks` dissolves into `dirdiff.formats.notebook`. The server's
`build_text_file_payload` and `build_notebook_file_payload_if_applicable`
dissolve into the shared text-bay renderer and the ordered classification.

### Frontend

The `frontend/src/hud/fileCard/` directory, behind its `FileCard.tsx` facade:

- the generic frame renderer, which walks frames and dispatches each bay to
  its widget by `kind`;
- one widget per bay kind. The `text` widget delegates to the existing
  `TextDiffGrid` in `hud/fileCard/grids/text/`.

`FileBody` stops switching on `render_kind` and mounts the frame renderer.
`NotebookFile.tsx` dissolves into it. Bay widgets start small and share one
module; a widget earns its own module by the ordinary size rule, and none of the
planned widgets is expected to.

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
- `selectHunk()` keeps exactly four direct callers. No builder, frame renderer,
  or bay widget introduces a hunk-selection path.
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
- The blob endpoint serves captured Snapshot bytes only. It creates no store,
  copy, or mutable path.

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
expectations** and fills the `basic` cases it can reach without blobs — cell
source, structure, metadata, and the text outputs — and the `invalid` case. The
plot case is added here too, showing its text representation, and changes shape
rather than appearing when stage 3 lands.

### Stage 2 — blobs, images, binaries

Add `/api/file-blob`, the `image` bay kind, and the `binary` bay kind. Every
blob this stage serves is a file at a path, so path addressing is sufficient here.
The `decode_text_content` behavior change lands here: non-text, non-image content
stops raising and becomes a `binary` bay.

### Stage 3 — hybrid notebooks

The notebook builder prefers `image/png` for the outputs that offer it. If stage
1 chose output representations as described and stage 2's `image` bay exists,
that part is one changed rule and adds no bay kind and no widget.

This stage also settles the blob addressing gap recorded above, because a
notebook-embedded plot is the first blob that is not a file at a path. That work
is expected and is not evidence of an earlier shortcut. Anything beyond those two
— a new bay kind, a new widget, a second endpoint, a change to frames or hunk
allocation — is such evidence, and should be investigated rather than absorbed.

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
| 2 | `backend_index.md`, `frontend_index.md` | the blob endpoint and the bay widgets |
| 2 | `file-meat.md` | the binary File behavior that no longer produces an error `LazyFile` |
| 3 | `backend_index.md` | the blob endpoint's addressing, once it serves sub-file bytes |
| 2 | `navigation.md` | non-text hunk targets, first visited when image and binary bays exist |
| 2 | `reviews.md` | the pseudo-line target for non-text bays |
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
data, computation, a plot, notes — with one targeted change, not a minimal pair
holding the single cell under test. A one-cell fixture cannot show that frames
follow document order, that only changed cells compose a frame, or that an
untouched cell stays out of the way, which is most of what the notebook shape
has to get right. The unchanged cells are the point.

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
dependency group when the set is built; nothing there covers it today.

**Images are downloaded, and their licence permits redistribution.** Public
domain or CC0, with the source and licence recorded beside the fixture. The same
goes for any other binary content the `binary` cases need.

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
| `basic` | a raw cell edited | the same, with no language to highlight |
| `basic` | a cell added, a cell removed | one frame each, in document order, with correct hunk indexes |
| `basic` | cells reordered | frames follow the notebook, and cell keys stay put |
| `basic` | an untouched cell beside a changed one | present, collapsed, carrying no hunk, and still openable |
| `basic` | stream output changed | a text bay holding the changed output |
| `basic` | an error traceback appears | the traceback as text, its escape codes not interpreted |
| `basic` | a plot re-rendered (AS TEXT), source untouched | source collapsed, the output bay reachable with Next hunk |
| `basic` | cell metadata changed | a collapsed canonical-JSON bay |
| `invalid` | `.ipynb` that is not valid notebook JSON | an ordinary text diff of the file, because that is what the bytes are |

The plot case is the one that changes shape across stages: a text bay in
stage 1, an `image` bay from stage 3. Both are correct for their stage, and
the preset outlives the change.

### `formats/`

Everything whose composition is decided by what the file is rather than by what
is inside it. The name is imperfect — a notebook is a format too — and it is
chosen for being the least misleading available, not for being right.

| Category | Case | What it must show |
| --- | --- | --- |
| `basic` | an image changed on both sides | both sides rendered, one hunk index consumed |
| `basic` | an image added, an image removed | the one captured side, with the other absent rather than blank |
| `basic` | non-text, non-image content changed | digests and sizes, and no error `LazyFile` |
| `basic` | the link target changed | the target as text |
| `basic` | a symlink replaced by a regular file, and the reverse | that the kind changed, not only that bytes did |
| `invalid` | a broken symlink | its recorded target, because that is what Git stores |

Two fixture problems belong to whoever builds this set. The preset README says
both sides of a case share an extension, which suits images and binaries and
does not suit symlinks. And a symlink fixture is only a fixture if nothing in
the test path follows it, which is a property of the loader, not of the file.

### Handlers should be handlers

This one is a judgement, not a measurement, but it is statable. A route body
should read as HTTP work and nothing else.

For `/api/file-diff` that is: recover the Room, load the two captured byte
sides, build one `ComposeContext`, call `compose()`, and return what it gives
back. Attaching the display name and file kind stays until the TODO in
`server.py` is resolved, so the target is minimal rather than empty.

`render_loaded_snapshot_file` now reads as that: it checks the capture error,
loads the two byte sides, builds one `ComposeContext`, calls `compose()`, and
attaches the display name and file kind. Decoding, engine selection, and payload
assembly moved into composition. The two attached fields stay until the TODO in
`server.py` is resolved, so the handler is minimal rather than empty.

The same standard applies to the two routes this design adds. The blob endpoint
resolves a Snapshot, calls `bays()`, and writes bytes with a media type.
Review validation asks `bays()` whether a key exists. Neither grows a second
notion of what a file is.

### What none of these may require

No case above may be made to pass by adding a bay kind for one file type, by
letting the frontend decide what a format looks like, by hiding a change that
has no rows, or by making a reviewer expand something to discover that it
changed.

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
