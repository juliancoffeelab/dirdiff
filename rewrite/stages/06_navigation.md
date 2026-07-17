## 6. Navigation and completion

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/04_navigation_virtualization.md`, `../spec/06_components_and_modules.md`, and `../spec/07_navigation_and_hotkeys.md`. This chapter defines implementation order; it does not redefine navigation behavior.

Before implementation begins, the topic specification must contain the explicitly approved DOM hunk-token, folding, provisional-token transition and line-pin designs. If older repair or reconciliation requirements conflict with that approved direction, stop and present the exact contradiction rather than implementing an inferred compromise.

Every visible navigation, selection, HUD and hotkey result must preserve the pixel-perfect visual parity required by Chapter 1 and Appendix A. The virtualization mechanism completed in Chapter 5 remains FileCard-local and hunk-blind.

Implemented in this order:

1. DOM hunk tokens and selection

   Implement the approved stable DOM token model for real, provisional and skipped positions. Selection, participation, ordering and folding behavior come from those DOM tokens rather than duplicated Solid navigation state or generic structural observation.

2. Navigation controller

   Implement `navigation.tsx`, NavigationProvider, useNavigation, NavigationCommand, Next, Previous, wrapping, direct-hunk navigation and Top against current DOM token order.

3. File integration

   Connect HuskFile, LazyFile, FullFile, FileTree, headers, counters and the Chapter 5 rich/virtual representations to the approved token model. Integrating tokens must not make virtualization depend on selected-hunk state.

4. Scrolling

   Implement the scroll-source gate, throttled scroll-follow, navigation scrolling and enrichment required for exact rich geometry.

5. Line pins

   Implement line pins only from their separately approved design. Do not treat pin restoration as a generic consequence of hunk-token, folding or virtualization actions.

6. HUD and hotkeys

   Implement HintHud, DebugHud, HelpModal, their required placement, direct hotkeys and removal of Show All/Fold All behavior.

7. Remaining DOM behavior

   Preserve browser text-side selection and keep notebook navigation extensible without implementing the post-rewrite region-key TODO.

8. Final integration

   Connect the complete navigation subsystem to the finished application without changing the Chapter 5 virtualization heuristic, ownership or layout contract.

9. Wrapped-Previous virtualization stress test

   After navigation integration, execute every setup step, interaction and observation required by Section 47 of `../spec/04_navigation_virtualization.md`. This includes wrapping Previous from the first available hunk to the final manifest target while files still load, walking backward through HuskFile, LazyFile, VirtualFile and rich FullFile targets, exercising both explicit Previous commands and ordinary backward scrolling, and comparing the intrinsic-size optimization enabled and disabled. Use the required elaborate preset derived from real diffs; do not replace the scenario with a smaller synthetic approximation.

At the end of Chapter 6, `v_new` is a complete working frontend with the approved DOM hunk-token model, hunk selection, counters, Next/Previous navigation, wrapping, FileTree projection, folding participation, line pins, whole-file virtualization, HintHud, DebugHud, HelpModal and direct hotkeys.

`v_old` remains available until final cutover is explicitly authorized.
