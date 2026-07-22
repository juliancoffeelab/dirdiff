## 9. User-scroll following

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Implement the approved user-scroll-following contract from `../spec/08_hunk_navigation.md` inside the existing Chapter 7 Navigation instance. Do not replace it or mount another controller.

1. Store ephemeral browser-work state in private `scrollGuard` and `touchController` closures inside the existing Navigation controller. `scrollGuard` stores only an `"idle" | "input" | "document"` state and one pending wheel/touch expiry. `scrollGuard.ok()` is a pure predicate; the guard contains no DOM policy. Do not add Solid state or retain selected hunk identity.
2. Recognize vertical wheel movement, touch movement, and native page-scrolling keys. Input at the corresponding document boundary does not arm following.
3. Wheel and touch input schedule an expiry before the next repaint when no document scroll occurs. Keyboard input does not use this expiry because native keyboard scrolling may begin later. The first allowed document scroll cancels a pending expiry and moves the guard from `"input"` to `"document"`. A captured nested-element scroll moves `"input"` to `"idle"`, but FileTree movement caused by selection while the guard is already `"document"` does not stop following. Each new recognized input returns the guard to `"input"`, so a new input consumed by a nested scroller still stops following.
4. On every allowed document scroll, hit-test the visible file list at the viewport reading line and select only a visible rich participating real target in that FileCard. Never select virtual, pseudo, or skipped targets.
5. On `scrollend`, stop following without repeating the final reading-line calculation already performed by the last `scroll` event. Explicit Navigation stops following before programmatic scrolling.
6. `scrollFollow` is the sole reading-line selection operation. FileTree Navigation never calls it; FileTree only scrolls.
7. Exactly `nextHunk`, `prevHunk`, and `scrollFollow` call `selectHunk` directly. No helper, wrapper, dispatcher, initialization routine, renderer, FileTree operation, or shared calculation may call it.
8. Report unexpected native-listener errors through one ordinary error Toast and stop that following sequence without retrying.
9. Execute the complete wrapped-Previous and backward-scrolling stress scenario required by Section 47 of `../spec/04_navigation_virtualization.md`.

Explicitly absent from Chapter 9:

- line pins;
- notebook region-key extensions;
- a second NavigationProvider;
- a second hotkey listener;
- generic scrolling infrastructure outside Navigation;
- selection changes outside explicit hunk navigation and the approved recognized-scroll path.

`v_old` remains available until final cutover is explicitly authorized.
