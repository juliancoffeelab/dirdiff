## 5. Navigation and completion

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/04_navigation_virtualization.md`, `../spec/06_components_and_modules.md`, and `../spec/07_navigation_and_hotkeys.md`. This chapter defines implementation order; it does not redefine navigation or virtualization behavior.

Every visible navigation, selection, virtualization, HUD and hotkey result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. DOM replacement and virtualization must not introduce visual differences while their corresponding `v_old` content is visible.

Implemented in this order:

1. DOM identity and selection

   Add real and pseudo-hunk targets, FileCard-owned selected-hunk identity, DOM projection, counters, and structural selection repair.

2. Navigation controller

   Implement `navigation.tsx`, `NavigationProvider`, `useNavigation`, `NavigationCommand`, Next, Previous, wrapping, direct-hunk navigation, and Top.

3. File integration

   Connect HuskFile, LazyFile, FullFile, folding, FileTree targets, FileTree highlighting, headers, counters, and `waitToEnrich` to Navigation.

   Once Husk pseudo-hunks, selected-hunk repair, local/global counters, Next/Previous wrapping and Husk-to-Full replacement all work, stop for a loading-presentation review. Evaluate the approved complete FileTree plus immediate Husk-card presentation in those real interactions. The approved design remains in force unless that review explicitly decides to replace it with progressively appearing file presentation; Chapter 4 visuals alone are not sufficient evidence for such a change.

4. Scrolling and line pins

   Implement the scroll-source gate, throttled scroll-follow, navigation scrolling, and independent line-pin restoration.

5. Whole-file virtualization

   Implement row-count cost, rich zones, VirtualFile, geometry preservation, rich/virtual identity preservation, and enrichment before navigation.

6. HUD and hotkeys

   Implement HintHud, DebugHud, HelpModal, their required placement, direct hotkeys, and removal of Show All/Fold All behavior.

7. Remaining DOM behavior

   Preserve browser text-side selection and keep notebook navigation extensible without implementing the post-rewrite region-key TODO.

8. Final integration

   Remove the temporary rich-only limitation from Chapter 4 and connect the complete navigation and virtualization subsystem to the finished application.

At the end of Chapter 5, `v_new` is a complete working frontend with hunk selection, counters, Next/Previous navigation, wrapping, FileTree projection, folded-target exclusion, line pins, whole-file virtualization, HintHud, DebugHud, HelpModal, and direct hotkeys.

`v_old` remains available until final cutover is explicitly authorized.
