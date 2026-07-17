## 7. Explicit hunk navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/08_hunk_navigation.md`, the whole-file virtualization contract in `../spec/04_navigation_virtualization.md`, the module boundaries in `../spec/06_components_and_modules.md`, and the existing hotkey/HUD composition in `../spec/07_navigation_and_hotkeys.md`.

Every implemented visible result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. Chapter 5 virtualization remains FileCard-local and hunk-blind. Chapter 6's one hotkey listener and HelpModal remain intact.

Implement in this order:

1. Render concrete real, Husk, Lazy, zero, and skip identity objects directly into their specified DOM attributes.

2. Add one coordinate-preserving `skip` pseudo for every real hunk removed by explicit FullFile collapse. Apply `.skip` directly for explicitly collapsed Husk, Lazy, and zero targets. A queued or loading Husk remains a participating pseudo-target.

3. Store selected identity only on FileCard DOM. Initialize every non-empty ChangeSet by selecting its first participating target exactly once.

4. Implement the ChangeSet-scoped NavigationProvider, checked `useNavigation()`, NavigationCommand, and one private target-based `selectHunk()`.

5. Implement Next and Previous with off-screen scroll-back, wrapping, strict DOM participation, and `waitToEnrich()`. Re-resolve the rich target before final selection and scrolling.

6. Enable the existing HintHud Next/Previous buttons and the existing `n`/`N` bindings. Route `p` through Navigation's Top operation without mounting another hotkey listener.

7. Render HintHud and DebugHud exactly as specified and preserve their adjacent source and rendered placement.

8. Implement DOM-derived counters and the narrow ChangeSet MutationObserver. Counts exclude `.skip`; zero is exact; only Husk and Lazy add `+`.

9. Implement read-only FileTree highlighting from selected FileCard DOM. FileTree rows remain non-navigating in this chapter.

Explicitly absent from Chapter 7:

- user-scroll following;
- FileTree file-row navigation;
- line pins;
- notebook region-key extensions;
- selection changes outside initialization and explicit navigation actions;
- any additional selected-hunk state outside FileCard DOM.

At the end of Chapter 7, HintHud, DebugHud, Next, Previous, wrapping, off-screen return, Top, counters, selected FileTree highlighting, collapse participation, and rich materialization work without scroll-follow or FileTree click navigation.

`v_old` remains available until final cutover is explicitly authorized.
