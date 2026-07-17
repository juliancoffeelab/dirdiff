## 4. ChangeSets without navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/01_tanstack_query.md`, `../spec/03_file_presentation.md`, `../spec/05_errors_and_toasts.md`, and `../spec/06_components_and_modules.md`. This chapter defines implementation order and the temporary no-navigation boundary; it does not define alternative behavior or architecture.

Repository cache IDs remain disposable backend handles. The frontend handles their expiration through the snapshot replacement specified in `../spec/01_tanstack_query.md`; longer backend retention remains an explicit follow-up. Missing preset snapshot identity remains the existing separate FIXME in [followups.md](followups.md) and must not cause preset, backend, or integration-test changes during this chapter.

Every visible component implemented in this chapter must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. New ownership, state and component boundaries must not change layout, dimensions, spacing, typography, colors, borders, shadows, sticky behavior, overflow, responsive behavior or control states.

App routes the selected profile identity through Tabs into ChangeSet. ChangeSet observes the same canonical preferences query as Profile and derives `aggressiveFolds` reactively, defaulting to `true` when no profile is selected or preference data is unavailable. It does not copy preferences into App or local signals, and preference changes do not restart manifest or file requests.

Implemented in this order:

1. ChangeSet loading

   Implement the ownership boundary from `../spec/01_tanstack_query.md`: persistent lightweight `ChangeSet`, active `ChangeSetContent` for one immutable complete `DiffParams`, and manifest-keyed `ChangeSetSnapshot` for all manifest-dependent observation, sequencing and rendering. Inactive Tabs retain none of those backend observers or rendered files. Manifest replacement and repository-cache expiration dispose the complete old snapshot before loading restarts.

   Then implement manifest observation, lazy metadata, strict FileSequence, canonical file queries, progress, cancellation, reload, and repository-cache-expiration restart inside those boundaries.

2. File presentation

   FileTree, ChangeSetTitle, HuskFile, FullFile, LazyFile, their separate headers, FileBody, DiffGrid, folds, notebooks, Portals, and localized boundaries. Every manifest entry appears immediately as a FileTree entry and stable FileCard; an ordinary queued or fetching entry uses its HuskFile presentation until its canonical file query succeeds.

   This immediate Husk/FileTree loading presentation is the final Chapter 4 design. It remains authoritative unless a later explicit review decides to replace it. Chapter 7 provides the first useful opportunity for that review because provisional hunk tokens, counters and navigation transitions work together there; Chapter 4 does not attempt to judge interactions that do not exist yet.

3. Rich-only rendering

   Every loaded text file remains rich temporarily. There is no temporary virtualization mechanism.

Explicitly absent until Chapter 7:

- `NavigationProvider`;
- selected hunk;
- hunk counters;
- Next/Previous/Top;
- pseudo-hunk navigation behavior;
- FileTree selected-hunk highlighting;
- line-pin restoration;
- scroll-follow;
- navigation-specific hotkeys;
- HintHud;
- DebugHud hunk projection;

Whole-file virtualization remains absent until Chapter 5.

At the end of Chapter 4, `v_new` can load and display real ChangeSets through every Tab, but it cannot navigate hunks yet.

The current `schedulerYield`, `admittedFiles`, and `admitted` FileCard contract remain unchanged during this lifecycle correction. Their possible removal belongs only to the explicit follow-up in [followups.md](followups.md).

Chapter 5 can implement whole-file virtualization as a separate FileCard-local subsystem. Chapter 6 then implements only the direct non-hunk hotkeys and HelpModal. Chapter 7 implements navigation, selection, HintHud, DebugHud and navigation-specific hotkeys without making virtualization depend on hunk state.
