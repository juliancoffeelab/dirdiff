## 9. User-scroll following

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

This is a required rewrite chapter, but implementation remains blocked until its design gate is approved. Once approved, implementation must follow the resulting corrected scroll-follow section of `../spec/08_hunk_navigation.md` and extend the existing Chapter 7 Navigation instance; it must not replace it or mount another controller.

> **TODO design gate — this chapter is not authorized for implementation.** Re-investigate stable behavior, browser input classification, throttling, `scrollend`, layout changes, selection timing, and interaction with explicit and FileTree navigation. Present a corrected complete design and obtain explicit user approval before changing code.

The current investigation direction is:

1. Add the imperative `idle | user | navigation` scroll-source state to the existing Navigation controller.
2. Recognize only real user wheel, touch, and native page-scrolling-key input. Input at the corresponding document boundary does not arm user-scroll following.
3. Throttle DOM sampling to at most one animation frame. On `scrollend`, perform the final permitted calculation and return the source to idle.
4. Re-check the stable reading-line behavior: select only visible participating real targets in the FileCard crossing the reading line. User-scroll following never scrolls, enriches, expands, fetches, or selects pseudo-targets.
5. Decide explicitly whether completion of scroll-only FileTree Navigation invokes the same scroll-follow calculation once. Chapter 8 deliberately leaves selection unchanged and does not imply this behavior.
6. Execute the complete wrapped-Previous and backward-scrolling stress scenario required by Section 47 of `../spec/04_navigation_virtualization.md`.

These notes do not authorize implementation. Exact behavior must be corrected and approved first.

Explicitly absent from Chapter 9:

- line pins;
- notebook region-key extensions;
- a second NavigationProvider;
- a second hotkey listener;
- generic scrolling infrastructure outside Navigation;
- selection changes outside explicit hunk navigation and the approved recognized-scroll path.

`v_old` remains available until final cutover is explicitly authorized.
