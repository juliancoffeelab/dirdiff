## 4. ChangeSets without navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/01_tanstack_query.md`, `../spec/03_file_presentation.md`, `../spec/05_errors_and_toasts.md`, and `../spec/06_components_and_modules.md`. This chapter defines implementation order and the temporary no-navigation boundary; it does not define alternative behavior or architecture.

Every visible component implemented in this chapter must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. New ownership, state and component boundaries must not change layout, dimensions, spacing, typography, colors, borders, shadows, sticky behavior, overflow, responsive behavior or control states.

Implemented in this order:

1. ChangeSet loading

   Manifest query, lazy metadata, strict FileSequence, canonical file queries, progress, cancellation, and reload.

2. File presentation

   FileTree, ChangeSetTitle, HuskFile, FullFile, LazyFile, their separate headers, FileBody, DiffGrid, folds, notebooks, Portals, and localized boundaries.

3. Rich-only rendering

   Every loaded text file remains rich temporarily. There is no temporary virtualization mechanism.

Explicitly absent until Chapter 5:

- `NavigationProvider`;
- selected hunk;
- hunk counters;
- Next/Previous/Top;
- pseudo-hunk navigation behavior;
- FileTree selected-hunk highlighting;
- line-pin restoration;
- scroll-follow;
- navigation hotkeys;
- HintHud;
- DebugHud hunk projection;
- whole-file virtualization.

At the end of Chapter 4, `v_new` can load and display real ChangeSets through every Tab, but it cannot navigate hunks yet.

Chapter 5 can then implement navigation, selection, virtualization, HintHud, DebugHud, HelpModal, and direct hotkeys as one interconnected subsystem.
