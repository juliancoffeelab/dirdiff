# How to document dirdiff

Documentation should tell a reader what the current system promises, why its
boundaries exist, and where to look next. Keep each fact in the narrowest place
that can state it completely.

## Choose the right place for the right purpose

- README.md - a new user deciding what dirdiff does and how to run it.
Authored by human only, for other humans.
- spec/ - a developer learning the current architecture, contracts and
non-obvious invariants
- Module docstring - a developer learning the module interface, resources,
guarantees and exclusions.
- Item docstring - a caller using a function, type, class, method or value
- Inline comment - a maintainer reading local mechanics, ordering, lifetimes,
and other non-obvious implementation details.
- spec/testing.md - a developer choosing, locating and running a test.

Do not repeat a fact across these places. Link to its authoritative explanation
when another document needs it.

## What *this* document is about
This document is mainly about how to write docs in code. Anything else is
governed by existing documents like [`spec/goal.md`](../spec/goal.md),
[`AGENTS.md`](../AGENTS.md) and other documents.

## Package docstrings

Dirdiff code is organised into packages, which are obviously modules, but just
as obviously have a different function when it comes to documentation.
When module docstring describe things in depth, the main goal of package
docstring is to familiarize the reader with the purpose of the package and its
boundaries.

Package is defined by having an `__init__.py` which should hold nothing, except
for `__all__` variable, and in some authorized exceptions, a facade function.
Package `__init__.py` docstrings describe the package facade. They should make
the supported import path and the split between public and package-internal
contracts obvious.


### Bad example
```python
"""Git no-index-backed diff engine.

This package renders already-loaded text sides by writing temporary files,
running `git diff --no-index`, and projecting Git's unified patch text into
dirdiff engine rows.  Code outside the package imports `GitDiffEngine` from this
package root; subprocess execution lives in `git.py`, and patch-to-row
projection lives in `logic.py`.
The engine is intentionally repo-agnostic.  Backend code loads text from Git
refs, presets, or the worktree before this package is called.  This package must
not discover repositories, resolve refs, build manifests, decide HTTP modes, or
attach display-only syntax/fold enrichment."""
```

The first line of the docstring is fairly good, and the last paragraph surely
has good intentions, but also confusing to the point of being useless.
And the middle is an anti-pattern.
Let's improve it.

### Good example
```python
"""Git-diff backed diff engine.

This package implements a version of diff engine using native functionality
of git.
As all engines, it exports `GitDiffEngine` with a method to turn two `DiffSide`
handles into structured diff representation, this one by using `git diff`.
If you want to check how we call and get info from git subprocess, go to
`dirdiff.engines.git.git`, if you want to see what we do with that later, go
to `dirdiff.engines.git.logic`.

This engine is, maybe surprisingly, agnostic to repo-backend, and can be used
with any kind of version storage, even if it doesn't use git at all (like
presets): the secret is in using `git diff --no-index`. This allows us to use
real functionality with any option it gives (i.e. different diff algorithm,
even if we don't use any at the moment), on any two files we want.

Because of that, none of the code in this package should read any git refs,
branches, or commits. That's the responsibility of `dirdiff.backend.git` module.
Nor should it manage semantic representation such as folds or syntax, that
is the job of `dirdiff.rendering`.
"""
```

## Module docstrings

Start with one sentence that states what the module provides. Then explain:

- its public interface;
- why the module exists as a boundary;
- shortly the type of data structures it holds;
- the responsibilities excluded from the module.

Write the contract that exists. Do not narrate refactors, apologize for the
implementation, or use a docstring to make a confused boundary sound settled.
If the contract is hard to state, reconsider the boundary before adding prose.

### Bad example
```python
"""Persistence for per-user dirdiff UI preferences.

`PreferencesStore` reads and writes the preferences used by FastAPI preference
routes in `dirdiff.server`.  The exported `PreferencesRecord` is the read model
returned to that route layer.  The private `UserPreferences` SQLAlchemy table
stores the actual row keyed by `user_profile_id`.

This module owns the persisted shape of preference rows only.  It does not
decide which user profile is active, does not manage repository marks, and does
not know how preferences are rendered in the frontend.
"""
```

That is almost a good example, yet it fails in details:
- First of all, it's formatted badly.
- Second, it for some reason mentions a private class.
- Third, having that, it doesn't explain what's even the purpose of the
module.
Let's fix it.

### Good example
```python
"""Persistence for per-user dirdiff UI preferences.

## Classes
`PreferencesStore` reads and writes the preferences of user profiles we have.

The exported `PreferencesRecord` represents preferences of one user.

## Purpose and boundaries

This module owns the persisted shape of preference rows, if any caller
(currently `dirdiff.server`), wants to do something about stored preferences,
that's the module to use.

Note, this module shouldn't interperet anything besides that, that is the
responsibility of the callers, but if the callers need more intricate data-access
patterns, the `PreferencesStore` class should be extended. Nor does it know
about users per se, for example, if they are active or not.

This must not be turned into generic ORM layer, it must provide functionality
callers need and no more, in a most efficient way possible, minimising redundant
SQL round-trips.
"""
```

This one contains a durable contract and values, instead of pinning down current
implementation exactly, as these should belong to item-level docstrings, or
even inline comments inside them.

## Type docstrings

In the end, every package is constructed of invidual items, one of these are
types. This includes type aliases for unions, `TypedDict`s, dataclasses,
protocols, simply put, everything that can be used as a type.

Despite being similar, they each have their nuances, so let's pick them one by
one.

### Example to improve

```python
class Room:
    """Own one correspondence-selected Room's Snapshots and hand out Threads.

    Snapshot reads (`meta`, `manifested`, `get`, `file_delta`, the captured-file
    lookups) and Thread access (`threads`, `get_thread`, `thread_for_comment`,
    `create_thread`, `apply_review_batch`, the activity reads) require the exact
    Snapshot key on every call. Comment and lifecycle writes are the returned
    bound Threads' responsibility; Room only locates and constructs them.
    `capture_context`, `recapture`, and `path_for_snapshot` continue this Room's
    persisted Tab with new captures. The class never stores a selected Snapshot
    id or exposes its private publication store.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        staging_path: Path,
        snapshots_path: Path,
        lock_path: Path,
        thread_lock: Lock,
    ) -> None:
        """Create a Room over one correspondence identity.

        Only `RoomLord` constructs this object. The supplied identity limits
        every relational read to this Room but does not select a Snapshot;
        callers must pass the exact key to every public read. The store paths
        locate this Room's durable Snapshot directories and staging area.
        """
        self._database = database
        self._identity = identity
        self._staging_path = staging_path
        self._snapshots_path = snapshots_path
        self._lock_path = lock_path
        self._thread_lock = thread_lock
```

It's the documentation for `Room` class in `room_lord` module.
It's the public API for callers such as routes in server.py.
I can't call it *bad*  per se, because while it does checks all the marks, but
let's rewrite it to make more sense and highlight important accents.

### Good example, class
```python
class Room:
    """Own one correspondence-selected Room's snapshots and hand out `Thread`s.

    Room represents a chunk of continuous work in the workspace.

    Since it owns multiple snapshots, most methods here will require
    `snapshot_id` key, as `Room` doesn't store any of them, and must not store
    any of them.
    *Implementation note: it does store its own hidden identity to ensure
    validity of operations, to avoid handing access to unrelated information.*

    # Thread boundary
    Room is not and must not be responsible for creating or managing comments
    on `Thread`s, that is the responsibility of `Thread` class, `Room`
    only locates and creates threads.
    Exception to this rule is `apply_review_batch`, because it spans multiple
    threads, and is a forced performance optimization.

    # Entrypoints

    The most basic usage is to get a `Room` from `RoomLord.corresponding_room`,
    then call `Room.manifested` to get list of files this room governs,
    and when needed `Room.get` to get exact physical handles for filepaths.

    If you need to create or get a thread, use `get_thread` or `create_thread`.

    For more, read the documentation for individual methods.
    """

    def __init__(
        self,
        *,
        database: RoomStore,
        identity: RoomIdentity,
        staging_path: Path,
        snapshots_path: Path,
        lock_path: Path,
        thread_lock: Lock,
    ) -> None:
        """Create a Room over one correspondence identity.

        # Parameters

        - `database`: Persistence interface for this Room's Snapshot and review
          records.
        - `identity`: Exact correspondence identity that bounds every Room read.
        - `staging_path`: Root for incomplete process-private captures.
        - `snapshots_path`: Root for complete published Snapshot directories.
        - `lock_path`: Cross-process lock file shared by publication and review
          writes.
        - `thread_lock`: In-process lock shared by publication and review writes.

        # Usage note
        Only `RoomLord` constructs this object. The supplied identity limits
        every relational read to this Room but does not select a Snapshot;
        callers must pass the exact key to every public read. The store paths
        locate this Room's durable Snapshot directories and staging area.

        @private
        """
        self._database = database
        self._identity = identity
        self._staging_path = staging_path
        self._snapshots_path = snapshots_path
        self._lock_path = lock_path
        self._thread_lock = thread_lock
```

This example clarifies the purpose and boundaries of a class, doesn't expose
implementation details except for what is *absolutely* necessary to explain the
contract, and gives a short guide over the usage.
In the end, `__init__` method explains how to construct the class, but is
marked `@private` as users of a class are not supposed to construct it directly.

### Another bad example
```python
class FileMeta(TypedDict):
    """Stable backend facts accompanying one captured filepath pair.

    The metadata preserves tracked provenance, Git/preset change
    classification, an explicit backend lazy override, and the exact capture
    failure when publication could not retain the File contents. It contains
    no renderer output or per-File line counts.
    """

    tracked: bool
    change_type: ChangeType
    lazy_reason_override: Optional[LazyReason]
    capture_error: Optional[str]
```

This one is from the same file, and it's useful example to explain how docs for
types should be written.
The main sin is lack of field docs, and lack of purpose.
One useful idea is that it properly uses literal types, since it serves as
a kind of documentation too.

### Good example, record type
```python
class FileMeta(TypedDict):
    """Facts that go with capture file pair

    Provided by the workspace backends, and then snapshot machinery and used by
    callers mainly to display information to the user, or to pick which files
    to produce.
    """

    tracked: bool
    """
    Whether a particular change is tracked in VCS or not.

    *Implementation detail: presets always has it as True, since for them it is
    irrelevant.*
    """
    change_type: ChangeType
    """
    What kind of change the file has.
    """
    # TODO: should this be an *override* over *derived* values?
    # Can't backend report lazy reason on its own?
    #
    # At the very least, maybe pick a better name.
    lazy_reason_override: Optional[LazyReason]
    """
    WorkspaceBackend reason to make a file lazy that can't be derived.

    Callers expected to combine it with `tracked`, `change_type` and other
    strategies (like filtering for generated files) to produce a final
    `lazy_reason`.
    """
    capture_error: Optional[str]
    """
    Reported and persisted when snapshot capture couldn't load a file side.

    *Implementation detail: Set by snapshot machinery to avoid aborting entire
    snapshot, while still signaling the error.*
    """
```

This example shows how to type record types, types whose main purpose is to
store data together.
The main section explains the purpose of the type and how to use it, then
each field has detailed documentation on its nuances.
Also shows how to distinguish public information the user expected to know,
and notes for the implementor using inline comments.
It does give an impression of being unfinished, but that's the nature of
software development, it's a constant reconsideration of assumptions.
That's why the documentation should respect that instead of pinning down
the current implementation.

### Another bad example
```python
LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
```

Why it is bad is fairly obvious, as it doesn't have documentation at all.
But it's a good example to show how to document.

### Good example, union literal
```python
LazyReason = Literal[
    "too_big", "generated", "deleted", "untracked", "pure_renamed"
]
"""
A reason to mark the file as lazy, which in the end means that we won't
produce the file's contents to the user instantly and instead wait on the user
to ask for it.
*Implementation detail: currently it means that frontend won't ask us to diff and
process the file alongside other files when loading the page*

Reasons for that are different, and here they are:
- `too_big` is used when file is generally too big, and might not be useful
to review anyway. It's a user call whether to load such file.
- `generated` is used for generated files, whose source of truth lies elsewhere
and hence not useful to review.
- `deleted` is used in cases where mere notion of file being deleted serves
more purpose than what content the file had.
- `untracked` is for files that are not yet added into VCS and hence might
not be desired in diff UI.
- `pure_renamed` similar to `deleted`, mere notion of a file being renamed
is more useful than its content.

It shall be said, that while lazy file's contents are not shown to the reviewer
instantly, such files must have a quick and obvious way to show them anyway.
"""
```
Key concept this example introduces:
- Lists. If you describe a collection, dont just bundle it all into one
senseless paragraph.
- Guidance on usage and expectations.
- Dont show more implementation details than necessary. In the future,
we could store diffs on disk, or cache them, or provide a different UI,
but the concept of "show not to the user until asked" is more important than
that.

### Another example to improve
```python
class DiffEngineProtocol(Protocol):
    """Contract implemented by diff engines.

    The important word here is "render". A diff engine does not own refs,
    branch-review semantics, preset catalog traversal, lazy file discovery, or
    notebook routing. Those are request/input concerns. By the time a caller
    reaches this protocol, it has already loaded the two sides and decided that
    they should be treated as ordinary text for this engine.

    This boundary is what keeps GumTree, difftastic, git-style rendering, and
    native text rendering comparable: each receives the same logical inputs and
    returns the same dirdiff rendered result shape. Engines may use path hints
    for language detection or temporary file names, but the text arguments are
    the content source of truth.
    """

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render an already-loaded left/right pair.

        The caller supplies two `DiffSide` values after resolving refs and
        loading file contents. The returned result is the rendered core of one
        composed bay; which bays a File has, and the HTTP envelope around them,
        are built by `dirdiff.formats` and the API layer around this call.
        """
        ...


@final
class DifftasticDiffEngine(DiffEngineProtocol):
    """Structural renderer backed by difftastic.

    The renderer entrypoint runs difftastic on already-loaded text and projects
    its structural output into the row model shared by the rest of dirdiff.

    Difftastic can fail or decline to produce rows for some inputs. That is
    represented as an engine warning plus a textual fallback, keeping the REST
    response renderable while still being honest about the engine result.
    """

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render an already-loaded pair with difftastic.

        The only inputs this method trusts are the text strings, existence
        flags, labels, and path hints supplied by the caller. Path hints are
        passed to difftastic for language/parser selection, but this method does
        not load those paths.

        If difftastic cannot produce usable rows, the renderer falls back to a
        Git-style textual alignment so the API still returns a renderable file
        diff. Notebook detection is intentionally outside this method and
        happens in server orchestration before an engine is selected.
        """
        ...
```

These type docstrings have the right facts, but don't make the relationship
between the protocol and its implementation easy to see. The protocol spends
most of its space listing unrelated work. The implementation repeats the
shared input and result contract, then reaches past the engine boundary to talk
about the REST response.

### Good example, protocol and implementation
```python
class DiffEngineProtocol(Protocol):
    """Render an already-loaded pair of text sides into a common diff result.

    All diff engines accept the same `DiffSide` inputs and return
    `DiffEngineResult`, so callers can select an engine without changing the
    rest of the rendering flow.

    Engines compare the supplied text. They may use path hints if diff is, for
    example, language-dependent, but must not use them to load file contents.
    Loading text and deciding their format happen outside this interface.

    # Usage
    The user is expected to obtain the right engine using `engine()` function,
    extract and select text chunks with the help of `Composer.bays` and then
    feed them one by one into `render_diff`.

    That said, unless you're implementing `Composer`, you should use its
    `compose` method, which does that all for you.

    # Links
    - `dirdiff.engines.engine` - dispatcher function to pick an implementation
    - `dirdiff.formats.Composer` - intended entrypoint for this interface
    """

    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Process two text handles and return the representation of the diff.

        # Parameters

        - `old`: Already-loaded text, existence, and path hint for the old side.
        - `new`: Already-loaded text, existence, and path hint for the new side.
        """
        ...
#
# ... later in its own file ...
#

@final
class DifftasticDiffEngine(DiffEngineProtocol):
    """Compare text structurally using Difftastic.

    Uses `DiffSide.path_hint` to pick a right language parser for difftastic.
    When Difftastic cannot produce structural rows, the
    `DiffEngineResult.engine_warning` contains a specific warning, while the
    difftastic engine itself falls back to different kinds of textual alignment.
    Callers still receive a valid `DiffEngineResult` without mistaking it for a
    successful structural comparison.

    The engine has no workspace or request state. Obtain it through `engine()`
    when selecting an engine by name.
    """

    @override
    def render_diff(
        self,
        *,
        old: DiffSide,
        new: DiffSide,
    ) -> DiffEngineResult:
        """Render the supplied sides with Difftastic's structural comparison.

        Path hints select the parser but are never read as files. If Difftastic
        cannot produce structural rows, return textual rows with an
        `engine_warning` that explains the degraded result.

        If one side doesn't exist, produces a trivial result of all
        insert/delete rows.

        # Parameters

        - `old`: Already-loaded old text and path hint used for parser selection.
        - `new`: Already-loaded new text and path hint used for parser selection.
        """
        # TODO: figure out if `DiffSide.exist` should exist and whether the
        # engine should be called on unpaired files in the first place.
        ...
```

The protocol documents the shared contract and the limits every implementation
must respect. The concrete class doesn't repeat it.
Instead, it documents what selecting Difftastic changes and how callers can
recognize its unsuccessful structural result. The same split applies to their main
methods: the protocol defines what every caller may provide and receive, while
the implementation documents Difftastic's parser selection and degraded result.
As always has an inline comment, with a note to the maintainer.

A few important technical rules all protocols in this project must respect:
- The protocol itself must subclass `typing.Protocol`
- Every implementation must subclass the defined protocol.
- Every implemented method must be marked with `@typing.override`.

## Function and method docstrings
As all docs, the first line must be a short explanation of what this function
does.

Then goes the contract:
- required inputs and their valid forms;
- observable results and side effects;
- exceptions or rejected states the caller must handle;
- ordering, lifetime, or disposal obligations;
- invariants the value guarantees and states it must not represent.

When callable sections are present, keep this order:

1. `# Parameters` or the TypeScript `@param` block;
2. `# Usage`;
3. `# Returns`;
4. `# Failures`.

Parameters are always the first section. Omit sections that do not apply without
changing the relative order of the remaining sections.

When a Python callable has two or more parameters besides `self` or `cls`,
document each parameter separately. Use the exact parameter name:

```python
# Parameters

- `left`: Rendered row from the diff's left side.
- `right`: Rendered row from the diff's right side.
```

TypeScript callables use one `@param` tag per parameter:

```ts
/**
 * Return whether two rendered rows have the same identity.
 *
 * @param left Row rendered for the diff's left side.
 * @param right Row rendered for the diff's right side.
 */
declare function rowsEqual(left: RenderedRow, right: RenderedRow): boolean;
```

Return annotations also decide when a dedicated return contract is required.
An atomic return is one named type, primitive, scalar literal, or type guard.
It does not need a separate section when the callable's opening sentence already
states its result. A list of atomic values is also simple enough to omit the
section. This applies to `list[Item]`, `Item[]`, `Array<Item>`, and their readonly
TypeScript forms.

Top-level absence is different. A return such as `Item | None`, `Optional[Item]`,
`Item | null`, or `Item | undefined` must explain what the absent value means.
Do not make the caller infer whether absence means not found, not applicable,
not mounted, explicitly stopped, or failed. Both languages put this in a
`# Returns` section with Markdown bullets. Python must give `None` its own
bullet. TypeScript must give every absent variant in the annotation its own
bullet. Another bullet documents the present value.

```python
def selected_item() -> Item | None:
    """Return the item selected in the current Snapshot, if one is selected.

    # Returns

    - `Item`: the item selected in the current Snapshot.
    - `None`: the Snapshot has no selection; callers may continue rendering it
      without an item panel.
    """
```

```ts
/**
 * Returns the item selected in the current Snapshot, if one is selected.
 *
 * # Returns
 *
 * - `Item`: the item selected in the current Snapshot.
 * - `null`: the Snapshot has no selection; callers may continue rendering it
 *   without an item panel.
 */
declare function selectedItem(): Item | null;
```

Structured returns also need dedicated prose. This includes tuples, mappings,
records written inline, non-optional unions, and parameterized return types
other than a list of atomic values. Explain how a caller reads the shape, not
how Python or TypeScript spells it. Describe tuple items in order, mapping keys
and values, ordering, relationships between members, and variant meaning when
they matter. Use “first,” “second,” and so on for raw tuple items instead of
zero-based integer positions or invented field names.

```python
def indexed_items() -> tuple[dict[str, Item], int]:
    """Index the items admitted at one activity boundary.

    # Returns

    - First, a mapping from each stable item id to the admitted `Item`.
    - Second, the item count before the caller's page bound was applied.
    """
```

```ts
/**
 * Indexes the items admitted at one activity boundary.
 *
 * # Returns
 *
 * - `items` maps each stable item id to the admitted `Item`.
 * - `total` is the item count before the caller's page bound was applied.
 */
declare function indexedItems(): { items: Map<string, Item>; total: number };
```

When a structured return is also optional, explain both the present shape and
the exact meaning of absence in the same return contract. `Promise<T>` is
transparent for this rule because an async caller receives `T`. A prose
paragraph or a one-item list under `# Returns` does not satisfy the contract.
Use at least two meaningful bullets divided by tuple items, object fields,
mapping key and value roles, variants, or present and absent cases. Raw tuple
bullets use ordinals, not zero-based integer positions or invented names. Do not
manufacture a split merely to satisfy the lint.

Do not enumerate type fields in the type-level prose. Put each field's contract
beside that field. Keep type-level field references for an actual relationship
between fields, not a sentence that walks through the object one property at a
time.

The function also most probably should have a usage section, unless it's
trivial from the context, and optionally an example.
Usage section would explain what you are expected to do before calling the
function, where to get the input parameters, etc.

Failures section goes separate and explains what can go wrong.

Broadly speaking, if all of the previous parts explained concepts and ideas,
these ones should be precise and explanatory, targeting both the caller and
the implementor to help establish the contract and ensure that it doesn't
get broken.
A good rule of thumb is the question: "let's say I want to change something in
a function, how likely the documentation will need fixing"?
Obviously, the trivial answer is "let's write a documentation that says
nothing", but that would be useless too.
The art of writing docs is to find balance of giving the information, yet
witholding non-important details that are likely to change.

### Bad example

````python
def get_or_create(self, user_profile_id: int) -> PreferencesRecord:
    ...
````

This method is part of `PreferencesStore`'s public interface, but tells the
caller nothing. Its name doesn't explain what gets created, which values a new
record contains, the db behaviour, where `user_profile_id` comes from, or what
happens when that Profile doesn't exist.

### Good example

````python
def get_or_create(self, user_profile_id: int) -> PreferencesRecord:
    """Return a Profile's preferences, creating them if absent.

    An existing record is returned unchanged. If none exists, the method
    creates one with aggressive folding enabled and returns it.
    Everything happens in one SQL query.

    # Usage

    You would most probably get a `profile_id` from somewhere else, like
    from a frontend that got it previously.

    ```python
    profile = user_profile_store.get(profile_id)
    preferences = preferences_store.get_or_create(profile_id)
    ```

    # Failures

    - `user_profile_id` must identify an existing Profile; otherwise the
    database rejects the write and propagates an exception.
    """
    # TODO: make get_or_create return None for a user that doesn't exist,
    # so that callers dont need to validate it to produce a useful error
    with Session(self.engine) as session, session.begin():
        # NOTE: we use sqlite upcert with conflict mechanic here to
        # do the work in one atomic query
        row = session.execute(
            sqlite_insert(UserPreferences)
            .values(
                user_profile_id=user_profile_id,
                aggressive_folds=True,
            )
            .on_conflict_do_update(
                index_elements=[UserPreferences.user_profile_id],
                # on conflict, set `aggressive_folds` back to old row
                # instead of writing down the default `True`
                set_={
                    "aggressive_folds": UserPreferences.aggressive_folds,
                },
            )
            .returning(
                UserPreferences.user_profile_id,
                UserPreferences.aggressive_folds,
            )
        ).one()
        return PreferencesRecord(
            user_profile_id=row[0],
            aggressive_folds=row[1],
        )
````

Now, this function actually explains its contract and what is especially useful
for database accessors, it says that all is happening in one query.
And, useful for the caller, it has example usage code and documents the failure.

For the implementor or a person who is trying to understand the code, it has a
proper inline comments.
Lastly, for the maintainer, it has a TODO about possible improvement to the code.

### Another bad example

Private functions have callers too, but those callers are fixed inside the
codebase. Their docs should explain the shared local contract and the reason
for non-obvious mechanics.
While they don't need to teach about general API usage, it would be helpful
to state why these function exists and what is their role for future readers
and reviewers.

```python
def _repo_key_from_git_url(url: str) -> str:
    stripped = url.strip()
    if stripped == "":
        raise DirdiffError("Remote URL is empty.")
    if stripped.startswith("git@"):
        without_user = stripped.removeprefix("git@")
        host, separator, path = without_user.partition(":")
        if separator == "":
            raise DirdiffError(f"Unsupported Git remote URL: {url}")
        return _repo_key(host=host, path=path)

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = parsed.hostname or ""
        path = parsed.path
        return _repo_key(host=host, path=path)
    raise DirdiffError(f"Unsupported Git remote URL: {url}")
```

It shouldn't need much explanation for why this code is badly documented, since
well, it's not documented at all.
The code hints at which strings it can parse, but not where those strings come
from or why this function exists. The `git@host:path` branch is also unexplained
even though it deliberately bypasses `urlparse`.
In addition, being a pure function, it could really benefit from having a simple
doctest.

### Good example, private utility

```python
def _repo_key_from_git_url(url: str) -> str:
    """Return a repository identity used to match forge and local remotes.

    GitHub preparation passes this function its `base.repo.html_url`. Both
    GitHub and GitLab preparation pass it each URL reported by the marked
    repository's Git configuration. GitLab constructs the forge-side key from
    its parsed host and project path through `_repo_key`. Different URL
    spellings for the same host and repository produce the same key.

    # Example

    >>> github_base_url = "https://github.com/openai/codex"
    >>> configured_remote_url = "git@github.com:openai/codex.git"
    >>> _repo_key_from_git_url(github_base_url)
    'github.com/openai/codex'
    >>> _repo_key_from_git_url(configured_remote_url)
    'github.com/openai/codex'
    >>> (
    ...     _repo_key_from_git_url(github_base_url)
    ...     == _repo_key_from_git_url(configured_remote_url)
    ... )
    True

    # Failures

    - Raises `DirdiffError` when a value cannot identify a supported Git host
      and repository path.
    """
    stripped = url.strip()
    if stripped == "":
        raise DirdiffError("Remote URL is empty.")
    # Git accepts this SCP-like spelling, which `urlparse` does not separate
    # into a hostname and repository path.
    if stripped.startswith("git@"):
        without_user = stripped.removeprefix("git@")
        host, separator, path = without_user.partition(":")
        if separator == "":
            raise DirdiffError(f"Unsupported Git remote URL: {url}")
        return _repo_key(host=host, path=path)

    parsed = urllib.parse.urlparse(stripped)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = parsed.hostname or ""
        path = parsed.path
        return _repo_key(host=host, path=path)
    raise DirdiffError(f"Unsupported Git remote URL: {url}")
```

The two input examples come from function's real callers, which along
with doctest clarifies its intended usage and reason for existence.

### Bad example, property

```python
class WorkspaceBackendProtocol(Protocol):
    @property
    def repo_root(self) -> Path | None:
        """Filesystem root used for display and path validation."""
        ...


# ... in dirdiff.backend.git ...

class GitBackend(WorkspaceBackendProtocol):
    @property
    @override
    def repo_root(self) -> Path | None:
        """Expose the repository root used for path normalization."""
        return self._repo_root
```

Properties hide the difference between a stored value and a calculation, so
their docs must make that difference safe for callers. 
This says roughly what the value is called, but leaves every property-specific
question unanswered.

A caller can't tell whether the value changes, whether reading it touches the
filesystem, why it can be `None`, or what guarantee a present path provides.
The implementation repeats the same shallow description instead of explaining
what Git contributes to the protocol contract.

### Good example, property

```python
class WorkspaceBackendProtocol(Protocol):
    @property
    def repo_root(self) -> Path | None:
        """The stable filesystem root from which this backend reads its input.

        The value is fixed when the backend is constructed.

        `RoomLord` uses a present root to keep its database and Snapshot store
        outside the reviewed files.

        # Invariants
        Reading it must perform no filesystem work and must not fail.

        # Returns
        - `pathblib.Path` to an absolute path containing this backend's source.
        - `None` means the backend is not bound to a repository root.
        """
        # TODO: should it even be None?
        ...


# ... in dirdiff.backend.git ...

class GitBackend(WorkspaceBackendProtocol):
    @property
    @override
    def repo_root(self) -> Path | None:
        """The Git worktree root supplied to this backend, if any.

        Uses a stored variable, never changes and never does any work.

        # Usage
        You will want to construct `GitBackend` using `GitBackend.discover`
        class method. It uses explicit `repo_root` when provided, or calls git
        to infer a repository root when omitted.

        Then you can just access the field.

        # Returns
        - An absolute path to the repository root.
        - `None` if we we failed to find repository root.
        """
        # TODO: probably extremely unsafe.
        # We must ensure that `GitBackend.discover` returns a real root we
        # expect, and asserts invalid combinations.
        # TODO: we should not accept None in `GitBackend.discover`.
        return self._repo_root
```

Here the useful guarantees are stability, an absolute path, and no filesystem
work on access, all of which protocol *demands* from implementation.
Inline comments for maintainer highlight uncovered gap, the nullable case has
near-zero domain meaning: it is an incidental missing value.

### Bad example, callback

```tsx
type SelectProps = {
  // ... display inputs ...
  selectedValue: string;
  onChange: (value: string) => void;
};

export function Select(props: SelectProps): JSX.Element {
  let trigger!: HTMLButtonElement;
  const [open, setOpen] = createSignal(false);

  // ... popup behavior ...

  /**
   * Selects one exact option value and returns focus to the trigger.
   *
   * Re-selecting the existing value closes the popup without emitting a
   * redundant change.
   */
  function select(value: string): void {
    setOpen(false);
    if (value !== props.selectedValue) {
      props.onChange(value);
    }
    trigger.focus();
  }

  // ... option buttons call `select(option.value)` ...
}
```

The type tells the caller how to satisfy TypeScript, but not what implementing
the callback means. To learn when it runs, what `value` represents, or which
interactions don't call it, the caller has to inspect `Select`'s implementation.
The nested helper has its own documentation, which is good, but that documentation
is not the contract presented to a caller implementing `onChange`.

### Good example, callback

```tsx
type SelectProps = {
  // ... display inputs ...
  selectedValue: string;

  /**
   * Handles activation of an option different from `selectedValue`.
   *
   * `value` is the activated option's exact `SelectOption.value`. The callback
   * may update caller state, navigate, or perform another domain action. If
   * this `Select` remains mounted and should reflect the choice, pass the
   * accepted value back as `selectedValue`.
   *
   * After the callback completes, `Select` closes its options popup and
   * focuses its trigger button.
   */
  onChange: (value: string) => void;
};

export function Select(props: SelectProps): JSX.Element {
  let trigger!: HTMLButtonElement;
  const [open, setOpen] = createSignal(false);

  // ... popup behavior ...

  /**
   * Selects one exact option value and returns focus to the trigger.
   *
   * Re-selecting the existing value closes the popup without emitting a
   * redundant change.
   */
  function select(value: string): void {
    setOpen(false);
    if (value !== props.selectedValue) {
      props.onChange(value);
    }
    trigger.focus();
  }

  // ... option buttons call `select(option.value)` ...
}
```

Now, the callback documentation is right there, when you read the props typing.
It clarifies what to expect of the function, and what contract you are expected
to fullfil, as well as example of usage.

## Global value documentation

Module-level runtime values need documentation when their initializer does not
explain their contract. Document why the value is shared, which code relies on
its identity or contents, whether it may change, and what must remain true when
it is replaced.

### Bad example, global value

```ts
const NO_THREADS: readonly ReviewThread[] = [];
```

The type says that callers cannot mutate the array through this binding. It
does not explain why the module reuses one empty array instead of writing
`review.data ?? []` where the value is read.

### Good example, global value

```ts
/**
 * Identity-stable empty array used while review data is unavailable.
 *
 * `reviewThreads` returns this exact array for every empty read. Creating a new
 * array there would make `markerRevision` treat unchanged Thread state as
 * changed. This module never mutates the array.
 */
const NO_THREADS: readonly ReviewThread[] = [];
```

The documentation states why the value has module lifetime, names the consumer
that depends on its identity, and forbids mutation. A title such as "empty
Threads" would only repeat the name and initializer.

### Another bad example, regular expression

```python
_ATOM_PATTERN = re.compile(
    r"\n"
    r"|[^\S\n]+"
    r"|\d+"
    r"|_+"
    r"|[A-Z]+(?![a-z])"
    r"|[A-Z]?[a-z]+"
    r"|[^\W\d_]+"
    r"|[^\w\s]"
)
```

The expression exposes its alternatives but not the boundaries they produce,
why their order matters, or whether matching preserves the complete input.

### Bad example, doctest

```python
_ATOM_PATTERN = re.compile(
    r"\n"
    r"|[^\S\n]+"
    r"|\d+"
    r"|_+"
    r"|[A-Z]+(?![a-z])"
    r"|[A-Z]?[a-z]+"
    r"|[^\W\d_]+"
    r"|[^\w\s]"
)
"""Split source text into atoms.

# Example

>>> _ATOM_PATTERN.findall("café Δvalue")
['caf', 'é', ' ', 'Δvalue']
>>> _ATOM_PATTERN.findall("Привіт\\t  \\n")
['Привіт', '\\t  ', '\\n']
"""
```

These record Unicode and whitespace edge cases without teaching that dirdiff
uses the expression to show which parts of changed source lines differ. They
promote incidental behavior into the first thing a reader learns and make the
documentation less useful even though the examples pass.

### Good example, doctest

```python
_ATOM_PATTERN = re.compile(
    r"\n"
    r"|[^\S\n]+"
    r"|\d+"
    r"|_+"
    r"|[A-Z]+(?![a-z])"
    r"|[A-Z]?[a-z]+"
    r"|[^\W\d_]+"
    r"|[^\w\s]"
)
r"""Split source text into the ordered atoms consumed by `_tokenize`.

The alternatives recognize newlines, other whitespace runs, digits,
underscores, acronym and ASCII word parts, remaining Unicode word runs, and
single punctuation characters. Their order is significant: the acronym branch
splits `HTTPServer` into `HTTP` and `Server`, while the underscore and digit
branches split `value_2` into three atoms.

Every alternative consumes at least one character. Together they cover the
complete input, so joining all matches must reproduce the original text.

# Examples

>>> before = "retry_count = parseHTTPServer(userID, 42)\n"
>>> after = "retry_count = parseHTTPServer(userID, 43)\n"
>>> _ATOM_PATTERN.findall(before)  # doctest: +NORMALIZE_WHITESPACE
['retry', '_', 'count', ' ', '=', ' ', 'parse', 'HTTP', 'Server', '(',
 'user', 'ID', ',', ' ', '42', ')', '\n']
>>> _ATOM_PATTERN.findall(after)  # doctest: +NORMALIZE_WHITESPACE
['retry', '_', 'count', ' ', '=', ' ', 'parse', 'HTTP', 'Server', '(',
 'user', 'ID', ',', ' ', '43', ')', '\n']

# Lossless join invariant

>>> source = "userID = 42\n"
>>> atoms = _ATOM_PATTERN.findall(source)
>>> "".join(atoms) == source
True

# Warning

ASCII word branches run before the remaining Unicode word branch. Identifiers
that mix ASCII and non-ASCII letters may therefore split asymmetrically:

>>> _ATOM_PATTERN.findall("café = Δvalue\n")
['caf', 'é', ' ', '=', ' ', 'Δvalue', '\n']
"""
```

The first section shows the expression applied to two versions of changed
source, its expected use in dirdiff. The source exercises newline, other
whitespace, digits, underscores, acronym and ASCII word parts, and punctuation;
the output makes the snake_case and camelCase boundaries explicit. The second
section checks separately that no source text disappears during tokenization.
The warning documents the remaining Unicode branch and the surprising behavior
callers may encounter while reviewing Unicode identifiers.

## Other JavaScript and TypeScript documentation

Not every contract belongs to a declared item. Effects, listeners, observers,
and cancellation often need comments at the statement that creates them. These
comments document why the mechanism exists, what makes it run, what it changes,
how long it remains active, and how its work is undone.

### Bad example, effect

```tsx
function FullFile(props: { card: Accessor<HTMLElement> }): JSX.Element {
  const [bayModes, setBayModes] = createStore<
    Record<string, BayRenderMode | undefined>
  >({});

  // ... mounted bay renderers update `bayModes` through `setBayModes` ...

  const fileRenderMode = createMemo((): BayRenderMode | null => {
    const modes = Object.values(bayModes).filter(
      (mode): mode is BayRenderMode => mode !== undefined,
    );
    if (modes.length === 0) {
      return null;
    }
    return modes.every((mode) => mode === "virtual") ? "virtual" : "rich";
  });

  createEffect(() => {
    const mode = fileRenderMode();
    if (mode === null) {
      delete props.card().dataset.fileRender;
    } else {
      props.card().dataset.fileRender = mode;
    }
  });
  onCleanup(() => {
    delete props.card().dataset.fileRender;
  });

  // ... render the complete File ...
}
```

The body shows that the effect copies a value into a DOM attribute. It does not
say why the attribute exists, who reads it, what reactive input reruns the
effect, or why cleanup must delete it.
An effect comment must name its reactive inputs rather than making the reader
infer them from every nested call.
And *most importantly* the docs for effect must state why this must be an
effect. And ideally an inline comment for whether it is possible to avoid in
the first place.

### Good example, effect

```tsx
function FullFile(props: { card: Accessor<HTMLElement> }): JSX.Element {
  const [bayModes, setBayModes] = createStore<
    Record<string, BayRenderMode | undefined>
  >({});

  // ... mounted bay renderers update `bayModes` through `setBayModes` ...

  const fileRenderMode = createMemo((): BayRenderMode | null => {
    const modes = Object.values(bayModes).filter(
      (mode): mode is BayRenderMode => mode !== undefined,
    );
    if (modes.length === 0) {
      return null;
    }
    return modes.every((mode) => mode === "virtual") ? "virtual" : "rich";
  });

  // This must be an effect under the current component boundary. Mounted bays
  // can change `fileRenderMode` after FullFile renders, but FileCard renders the
  // target card, so FullFile has no JSX attribute it can bind to that changing
  // value. The effect performs the required reactive write for FileTree's
  // attribute observer. FullFile removes the attribute when it unmounts so the
  // persistent card cannot retain the departed body's mode.
  //
  // TODO: Move bay render-mode state to FileCard and bind `data-file-render`
  // in its JSX, eliminating this effect and its cleanup.
  createEffect(() => {
    const mode = fileRenderMode();
    if (mode === null) {
      // props.card() returns an HTMLElement, which filetree would observe
      delete props.card().dataset.fileRender;
    } else {
      props.card().dataset.fileRender = mode;
    }
  });
  onCleanup(() => {
    delete props.card().dataset.fileRender;
  });

  // ... render the complete File ...
}
```

Now the comments state both the reason for effect's existance, its reactive
inputs and side-effects.
Additionaly, it has a note for the implementor on how to improve the
architecture and get rid of an effect altogether.

## Links and pdoc
Put Python identifiers in backticks. Within the same module, the local name is
enough. Use the fully qualified name for another module, such as
`dirdiff.formats.Composer`, so pdoc can create the link.

Use relative Markdown links between repository documents. Link descriptive
text, not words such as "here". Run `make pdoc` when changing Python API
documentation and follow the affected links in the rendered site.

## Inline comments

Use comments for facts that belong beside a statement rather than in a caller's
interface. Explain why the operation is necessary, what ordering it preserves,
or how long mutable state, effects, observers, listeners, and cancellation
remain active. State how those resources are disposed.

Do not translate the next line of code into English. Prefer clearer code when a
comment would only explain what the syntax already says.

## Language

Use the established project terms from `AGENTS.md`. In particular, distinguish
folded lines from collapsed files or directories, a Tab from a file-local diff,
and a frame from its bays.

Use active voice, short sentences, and concrete nouns. Preserve exact external
API names. Avoid vague words that conceal a contract, and do not invent a new
term when the project already has one.

## Review documentation changes

Before declaring a documentation change complete:

1. Read the changed text beside the implementation it describes.
2. Check adjacent callers and architecture documents for contradictions.
3. Remove duplicated facts, history, promises about unfinished work, and prose
   that merely restates names.
4. Read the final diff as documentation, not as a formatting exercise.
