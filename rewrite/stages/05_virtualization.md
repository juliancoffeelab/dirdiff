## 5. Whole-file virtualization

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to the whole-file virtualization requirements in Sections 34–46 of `../spec/04_navigation_virtualization.md`, the virtualization-only parts of Section 47, and the module boundaries in `../spec/06_components_and_modules.md`. Section 47's explicit wrapped-Previous scenario belongs to Chapter 7; its ordinary backward-scrolling portion belongs to Chapter 8 after user-scroll following is integrated. This chapter defines implementation order; it does not redefine virtualization behavior.

The eventual VirtualFile hunk requirements remain in the specification. They belong to Chapter 7 integration and are not implemented by making the virtualization mechanism observe or own hunk state.

Every visible rich/virtual result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. Virtualization must not introduce an unauthorized visual redesign.

Implemented in this order:

1. FileCard-local render mode

   FullFile owns its local rich/virtual mode. The mode is not workspace, Tab, ChangeSet, Navigation or TanStack Query state.

2. Row-count cost and observer zones

   Implement the specified row-count bands, cost-dependent lead distance, hysteresis and IntersectionObserver lifecycle. The heuristic depends on file cost and viewport distance, not hunk identity or selection.

3. VirtualFile presentation

   Render complete old-side and new-side text in the required split presentation without syntax spans, inline-token spans, decorations or row virtualization. Preserve native browser search across both sides.

4. Geometry preservation

   Measure rich outer height immediately before rich-to-virtual transition. Give only `.virtual-file-body` that exact fixed height and contain internal overflow without displaying its scrollbar. A never-rich file uses its natural virtual height rather than a fabricated rich estimate.

5. Representation lifecycle

   Keep RichFileBody natural document content. Inline/split changes reconstruct only rich presentation; VirtualFile remains split. Collapsing a file unmounts the current body while retaining harmless FileCard-local measurements, and expanding it re-evaluates proximity.

6. Layout-focused verification

   Exercise ordinary forward and reverse scrolling, transitions above and below the viewport, tiny and huge files, native search, view changes while virtual, document-end behavior, and the intrinsic-size optimization comparison required by the specification. The complete scenario finishes in Chapter 8 because both Previous and recognized backward user scrolling must exist.

Explicitly absent until Chapter 7:

- `navigation.tsx`, NavigationProvider and useNavigation;
- hunk tokens or targets in either representation;
- selected-hunk identity or decoration;
- hunk counters and FileTree highlighting;
- Next, Previous, Top and scroll-follow;
- pseudo-hunk behavior and file-collapse participation rules;
- `waitToEnrich` routing from Navigation;
- line-pin restoration changes;
- HintHud, DebugHud hunk display and navigation-specific hotkeys.

At the end of Chapter 5, loaded text files switch between rich and virtual presentation with the specified cost heuristic and geometry behavior. Hunk navigation remains entirely absent.

Chapter 6 adds only direct non-hunk hotkeys and HelpModal. Chapter 7 integrates explicit DOM hunk navigation, and Chapter 8 adds recognized user-scroll following and FileTree navigation. It must not move virtualization policy or render mode into Navigation.
