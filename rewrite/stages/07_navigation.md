## 7. Explicit hunk navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/08_hunk_navigation.md`, the whole-file virtualization contract in `../spec/04_navigation_virtualization.md`, the module boundaries in `../spec/06_components_and_modules.md`, and the existing hotkey/HUD composition in `../spec/07_navigation_and_hotkeys.md`.

Every implemented visible result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. Chapter 5 virtualization remains FileCard-local and hunk-blind. Chapter 6's one hotkey listener and HelpModal remain intact.

Implement in this order:

1. Render concrete real, Husk, Lazy, zero, and skip identity objects directly into their specified DOM attributes.

2. Add one coordinate-preserving `skip` pseudo for every real hunk removed by explicit FullFile collapse. Apply `.skip` directly for explicitly collapsed Husk, Lazy, and zero targets. A queued or loading Husk remains a participating pseudo-target.

3. Store selected identity only on FileCard DOM. Initialize every non-empty ChangeSet without `data-file-render-error` by writing the first FileCard's first selected hunk attributes directly while mounting, including when that target carries `.skip`. Initialization must not call `selectHunk`. A terminal renderer marker stops initialization without selection or repair.

4. Implement the ChangeSet-scoped NavigationProvider, checked `useNavigation()`, NavigationCommand, and one private target-based `selectHunk()`. Exactly `nextHunk`, `prevHunk`, and `scrollFollow` call it directly; no other selection path is permitted.

5. Implement Next and Previous with off-screen scroll-back, wrapping, strict DOM participation, and `waitToEnrich()`. Re-resolve the rich target before final selection and scrolling.

6. Enable the existing HintHud Next/Previous buttons and the existing `n`/`N` bindings. Route `p` through Navigation's Top operation without mounting another hotkey listener. Handle every rejected navigation Promise with one persistent “Navigation failed” Toast.

7. Render HintHud and DebugHud exactly as specified and preserve their adjacent source and rendered placement. Enable the existing `d` Debug binding, keep the Help row enabled, and expose Help state through HintHud's `aria-expanded`.

8. Implement the exact `HunkDisplay` signal stored by the mounted ChangeSet shell and the attribute-filtered ChangeSet MutationObserver. `HunkDisplay` mirrors DOM navigation information but is never read by Navigation or selection logic. Skipped and replaced selected targets retain calculable positions; skipped targets do not increase totals. `globalSelectedHunk.hasMore` covers loading Husk targets, explicitly loadable Lazy targets, and collapsed files; zero alone is exact. Calculation validates only the semantic attributes it needs. A calculation failure produces one direct persistent Toast and no error signal.

Unexpected FullFile renderer exceptions retain the stable FileCard article where possible and replace the failed renderer with a critical unrecoverable strip. They mark terminal DOM with `data-file-render-error` and produce one persistent Toast, no RetryButton, no hunk target, and no selection repair or automatic recovery. Navigation initialization and HunkDisplay observation stop at that marker without another Toast or escalation.

9. Render file counters, DebugHud's Hunk value, and read-only FileTree highlighting declaratively from `HunkDisplay`. HintHud remains the existing three-button visual component and reads Navigation only. FileTree rows remain non-navigating in this chapter.

Explicitly absent from Chapter 7:

- user-scroll following;
- FileTree file-row navigation;
- line pins;
- notebook region-key extensions;
- selection changes outside initialization and explicit navigation actions;
- any additional selected-hunk state outside FileCard DOM.

At the end of Chapter 7, HintHud, DebugHud, Next, Previous, wrapping, off-screen return, Top, counters, selected FileTree highlighting, collapse participation, and rich materialization work without scroll-follow or FileTree click navigation.

`v_old` remains available until final cutover is explicitly authorized.
