## 8. FileTree navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Implement the approved scroll-only FileTree Navigation contract from `../spec/03_file_presentation.md` and `../spec/08_hunk_navigation.md` through the existing ChangeSet Navigation instance. Do not add another Provider, navigation controller, selection path, or main-page scrolling path.

Implement in this order:

1. Add `{ kind: "file"; fileIndex: number }` to `NavigationCommand`.
2. Resolve the indexed stable FileCard and its exact first target from current DOM.
3. For an expanded virtual FullFile, call its existing `waitToEnrich()`, then resolve hunk zero again.
4. Center the final target immediately. Yield exactly one browser frame and correct the scroll at most once if layout replacement moved that same target completely outside the viewport, then flash its stable FileCard. Do not loop, poll through further animation frames, or force neighbouring files rich.
5. Render FileTree file names as selectable buttons that send the file command. Their neighboring squares remain the only file-expansion controls.
6. Render directory names as selectable buttons ending in `/`. Activation finds the first file in manifest order and sends the same file command without changing directory or file expansion. Disable the button while that first file is a Husk.
7. Preserve scroll-only behavior: no file or directory name calls `selectHunk`, changes `HunkDisplay`, updates FileTree highlighting, calculates counters, expands, collapses, or fetches.
8. Keep Lazy loading exclusive to its plank. A Lazy name scrolls to its current visible or skipped target. A Husk name is disabled with a waiting cursor, and a file command that encounters a transient Husk target returns without scrolling or changing strict sequential loading.

The Next/Previous off-screen-selected-target rule does not apply to a direct file command. Selection remains unchanged until the separate scroll-follow design is approved and implemented.

Browser verification covers rich, virtual, collapsed, Lazy, zero, and Husk representations. Use the real `many-files`, `mixed-file-sizes`, `lazy-files`, and `sandwich` scroll presets to expose sequential loading and layout movement. The one-frame correction is the complete bounded response to observed virtual-to-rich movement; any remaining non-Husk failure must be presented before adding retries or polling.

Explicitly absent from Chapter 8:

- scroll-follow;
- line pins;
- file or directory expansion from a name click;
- hunk selection from a FileTree click;
- a second NavigationProvider;
- a second scrolling implementation in FileTree;
- compatibility behavior copied from `v_old`.

`v_old` remains available until final cutover is explicitly authorized.
