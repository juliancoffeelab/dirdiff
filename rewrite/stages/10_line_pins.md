## 10. Line pins

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Line pins are a separate required rewrite system from hunk navigation.

Implement the approved contract in [09_line_pins.md](../spec/09_line_pins.md).

`ChangeSetSnapshot` calls `linePins()` exactly once, parses the initial URL before
starting its existing file lane, and retains the resulting semantic target as
browser-work state. There is no target callback and no URL listener. The
existing lane performs every required file operation inline, preserves manifest
order through the target, admits the target normally, and awaits
`LinePins.restore()` before continuing beyond it.

DiffGrid receives that same LinePins instance. Direct activation calls
`toggleUrlState()` and paints its exact row without ChangeSet, Navigation, or a
second loading path. Every explicit DiffGrid row-producing operation reads
`parseUrl()` and paints the matching row when present.

LinePins invokes Navigation only after the target FullFile is admitted.
Navigation prepares the exact semantic line through the existing FileCard DOM
interface, completes the approved rich-entry geometry pass, stops scroll-follow
immediately before its one final scroll, and never selects a hunk.

## Required verification

Use the running Vite application and complete these browser scenarios against
real rendered output. Existing scroll presets are preferred. Add
numbered `_test_preset_<N>` fixtures under the scroll presets only when no
existing preset can expose a required lifecycle or geometry case. Each fixture
must use its own numbered directory and immutable time snapshots so concurrent
or later tests cannot overwrite another fixture's files.

1. Load an ordinary-text pin from the initial URL in split and inline view.
   Verify its exact side and line, complete-row decoration, one centered final
   scroll, and unchanged hunk selection.
2. Load notebook source pins for at least two distinct `cell_key` regions.
   Verify that the same line number in another region cannot match.
3. Directly pin, replace, and remove rendered ordinary and notebook lines.
   Verify immediate URL and decoration changes with no scroll and no file-lane
   work.
4. Replace or remove a pending pin through another direct activation. Verify
   that the older restoration cannot later paint, Toast, mutate the URL, or
   scroll.
5. Collapse and reopen the pinned file and a containing directory. Fold and
   unfold the pinned line. Switch inline/split view. Verify that none of these
   operations clears the URL and that explicit row creation restores only the
   exact decoration.
6. Let a pinned rich FullFile become virtual, then restore it. Verify that the
   pin never locks virtualization and preparation enriches the target before
   the final scroll.
7. Restore a distant target while sequential loading is active. Verify that
   every automatic file through the target loads in manifest order, newly
   selected LazyFiles wait, the target blocks later work during restoration,
   and loading resumes afterward.
8. Exercise a deferred Lazy target, an ordinary file-fetch failure, and an
   ordinary lazy-info failure. Verify that failure remains orthogonal, later
   lane work continues, the URL remains, Retry is explicit, and successful
   Retry resumes the same current target without a second loading loop.
9. Use the real many-files, mixed-file-sizes, sandwich, and lazy-files scroll
   presets to move layout above and around the destination. Verify the finite
   rich-entry geometry pass, exact target recalculation, and exactly one final
   programmatic scroll.
10. Use a target file absent from the manifest and a line absent from a complete
    current file. Verify one two-second notice and removal of only that exact
    URL target.
11. Use malformed, duplicate, empty, non-positive, and non-canonical URL pin
    fields. Verify one two-second notice, no loading or scrolling, and no repair
    of the malformed hash.
12. Trigger ChangeSet disposal during loading, preparation, and immediately
    before the final scroll. Verify `stopped` behavior and no later URL,
    decoration, Toast, or scrolling side effect.
13. Trigger an unexpected preparation or Navigation contract failure. Verify
    that it damages the `ChangeSetSnapshot` boundary and produces exactly one
    persistent Toast rather than a transient notice, duplicate Toast, swallowed
    error, or unhandled rejection.
14. Confirm that pointer side-selection behavior remains intact after adding
    direct line activation.
15. Confirm statically that line-pin code contains no Solid effect, polling or
    retry timer, MutationObserver, revision watcher, delegated ChangeSet click
    listener, capture-phase click spy, history listener, query observer,
    `fetchQuery`, or call to `selectHunk()`.

`v_old` remains available until final cutover is explicitly authorized.
