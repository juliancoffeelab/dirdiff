## 8. User-scroll following and FileTree navigation

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

This is a required rewrite chapter, but implementation remains blocked until its design gate is approved. Once approved, implementation must follow the resulting corrected sections of `../spec/08_hunk_navigation.md` and extend the Chapter 7 Navigation instance; it must not replace it or mount another controller.

> **TODO design gate — this chapter is not authorized for implementation.** The scroll-follow and FileTree-navigation material currently recorded in the specification is unreliable and must be treated only as investigation notes. Re-investigate both behaviors against the stable frontend, browser behavior, DOM and layout failure cases; present corrected complete designs; and obtain explicit user approval before changing code.

The numbered outline below is provisional and does not authorize code. Its behavior must be corrected and approved during the required design work before this chapter can be implemented.

Implement in this order:

1. Add the imperative `idle | user | navigation` scroll-source gate to the existing Navigation controller.

2. Recognize only real user wheel, touch, and native page-scrolling-key input. Input at the corresponding document boundary does not arm user-scroll following.

3. Throttle DOM sampling to at most one animation frame. On `scrollend`, perform the final permitted sample and return the source to idle.

4. Port the stable reading-line behavior: select only visible participating real targets in the FileCard crossing the reading line. User-scroll following never scrolls, enriches, expands, fetches, or selects pseudo-targets.

5. Add FileTree label navigation through the existing Navigation instance only after its remaining target-resolution, enrichment, layout-stabilization, main-page scrolling, and selection design is approved. File and directory names are the navigation surfaces; their neighboring squares remain expansion-only controls and never call Navigation. An expanded file is never collapsed by navigation; a collapsed non-Lazy FullFile may be expanded before navigation.

6. FileTree navigation to a LazyFile neither expands nor fetches it. A directory-name activation may eventually navigate to that directory's first hunk, but it never changes expansion. Directory squares bulk-toggle descendant files, FullFile squares toggle only their file, and no label toggles collapse state. Exact directory/file target resolution, Lazy pseudo-target selection, and scrolling remain part of this chapter's unresolved design gate.

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
