## 8. User-scroll following and FileTree navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Everything in this chapter must be implemented according to `../spec/08_hunk_navigation.md`. It extends the Chapter 7 Navigation instance; it does not replace it or mount another controller.

Implement in this order:

1. Add the imperative `idle | user | navigation` scroll-source gate to the existing Navigation controller.

2. Recognize only real user wheel, touch, and native page-scrolling-key input. Input at the corresponding document boundary does not arm user-scroll following.

3. Throttle DOM sampling to at most one animation frame. On `scrollend`, perform the final permitted sample and return the source to idle.

4. Port the stable reading-line behavior: select only visible participating real targets in the FileCard crossing the reading line. User-scroll following never scrolls, enriches, expands, fetches, or selects pseudo-targets.

5. Add FileTree file-row navigation through the existing Navigation instance. Expand the required file, resolve its first participating real, Husk, Lazy, or zero target, then perform ordinary target navigation.

6. FileTree navigation to a LazyFile may select and scroll to its plank but never activates or fetches it. Directory rows continue to control expansion only.

7. Execute the complete wrapped-Previous and backward-scrolling stress scenario required by Section 47 of `../spec/04_navigation_virtualization.md`, including rich, virtual, Husk, Lazy, zero, and collapsed skip representations.

Explicitly absent from Chapter 8:

- line pins;
- notebook region-key extensions;
- a second NavigationProvider;
- a second hotkey listener;
- generic scroll coordination outside Navigation;
- selection changes outside explicit navigation and recognized user-scroll actions.

At the end of Chapter 8, explicit navigation, recognized user-scroll selection, and FileTree file-row navigation share one ChangeSet-scoped controller and the same DOM truth.

`v_old` remains available until final cutover is explicitly authorized.
