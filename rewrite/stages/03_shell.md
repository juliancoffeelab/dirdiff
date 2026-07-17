## 3. Application shell

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/01_tanstack_query.md`, `../spec/02_client_state.md`, `../spec/05_errors_and_toasts.md`, and `../spec/06_components_and_modules.md`. This chapter defines implementation order and the temporary empty-ChangeSet boundary; it does not define alternative behavior or architecture.

Every visible component implemented in this chapter must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. New ownership, state and component boundaries must not change layout, dimensions, spacing, typography, colors, borders, shadows, sticky behavior, overflow, responsive behavior or control states.

Implemented in this order:

1. Complete `api/api.ts`

   Schemas, backend types, HTTP handlers, query/mutation definitions, and the `api` facade.

2. Domain-independent components

   `Select`, `AutocompleteInput`, and their local interaction state.

3. Workspace shell

   `hud/App`, `AppHeader`, global repo/engine/view state, TabStrip, eternal Tabs, and reset-from-URL behavior.

4. Metadata workflows

   Repositories, refs, defaults, presets, profiles, preferences, warmups, explicit refetches, and stale-time policies.

5. Tab workflows

   Head, Refs, Branch Review, Pull Request, and Preset controls. Each produces complete `DiffParams`.

6. Empty ChangeSet boundary

   Each Tab mounts its final ChangeSet owner boundary when it has selected `DiffParams`. During this intermediate chapter, that boundary is intentionally empty: it starts no ChangeSet requests and renders no ChangeSet content. Chapter 4 fills the same boundary rather than replacing it. AppHeader ChangeSet status outlets exist but have nothing to display.

This chapter does not introduce a fake ChangeSet, fake backend data, temporary file representation, or alternate loading path.

Explicitly absent until Chapter 4:

- manifest loading;
- lazy metadata;
- FileSequence and file queries;
- ChangeSet progress, cancellation and reload;
- FileTree and ChangeSetTitle;
- HuskFile, FullFile and LazyFile;
- FileBody, DiffGrid, folds and notebooks;
- ChangeSet Portals and localized file boundaries.

Virtualization remains absent until Chapter 5. Navigation and hunk selection remain absent until Chapter 6.

At the end of Chapter 3, `v_new` has its complete application shell, metadata behavior, Tabs and controls. Every Tab owns an empty ChangeSet boundary, but no ChangeSet data is loaded or displayed.

Chapter 4 can then implement ChangeSets and rendered files as a separately scoped and reviewable milestone.
