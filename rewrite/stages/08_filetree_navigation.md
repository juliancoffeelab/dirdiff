## 8. FileTree navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Implement the approved scroll-only FileTree Navigation contract from `../spec/03_file_presentation.md` and `../spec/08_hunk_navigation.md` through the existing ChangeSet Navigation instance. Do not add another Provider, navigation controller, selection path, or main-page scrolling path.

Implement in this order:

1. Add `{ kind: "file"; fileIndex: number }` to `NavigationCommand`.
2. Resolve the indexed stable FileCard and its exact first target from current DOM.
3. For an expanded virtual FullFile, call its existing `waitToEnrich()`, then resolve hunk zero again.
4. Calculate the document viewport that a centered destination scroll would produce without moving the page. Ask each expanded virtual FullFile with a `.virtual-file-body` whether its exact row-cost rich-entry zone intersects that hypothetical viewport, enrich one matching FileCard at a time, and recalculate destination geometry after every replacement. Bound the action with a local per-invocation set, finish when no unprocessed eligible FileCard matches, perform exactly one centered scroll, and flash the stable FileCard. Beyond the destination's direct enrichment in the preceding step, do not perform preliminary scrolling, predict rich heights, poll through timers or animation frames, or enrich non-intersecting FileCards.
5. Render FileTree file names as selectable buttons that send the file command. Their neighboring squares remain the only file-expansion controls.
6. Render directory names as selectable buttons ending in `/`. Activation finds the first file in manifest order and sends the same file command without changing directory or file expansion. Disable the button while that first file is a Husk.
7. Preserve scroll-only behavior: no file or directory name calls `selectHunk`, changes `HunkDisplay`, updates FileTree highlighting, calculates counters, expands, collapses, or fetches.
8. Keep Lazy loading exclusive to its plank. A Lazy name scrolls to its current visible or skipped target. A Husk name is disabled with a waiting cursor, and a file command that encounters a transient Husk target returns without scrolling or changing strict sequential loading.

The Next/Previous off-screen-selected-target rule does not apply to a direct file command. Selection remains unchanged until the separate scroll-follow design is approved and implemented.

Browser verification covers rich, virtual, collapsed, Lazy, zero, and Husk representations. Use the real `many-files`, `mixed-file-sizes`, `lazy-files`, and `sandwich` scroll presets to expose sequential loading and layout movement. In particular, verify a Lazy destination immediately following a large loaded virtual FullFile and a tall FullFile destination preceded by another large virtual FullFile. The first FileTree click must enrich every eligible expanded virtual FullFile with `.virtual-file-body` whose FileCard rich-entry zone intersects the hypothetical destination viewport, leave the page stationary until geometry settles, and then land correctly with one final scroll. The finite intersecting-file pass is the complete bounded response to observed virtual-to-rich movement; any remaining non-Husk failure must be presented before adding timer or animation-frame retries.

Explicitly absent from Chapter 8:

- scroll-follow;
- line pins;
- file or directory expansion from a name click;
- hunk selection from a FileTree click;
- a second NavigationProvider;
- a second scrolling implementation in FileTree;
- compatibility behavior copied from `v_old`.

`v_old` remains available until final cutover is explicitly authorized.
