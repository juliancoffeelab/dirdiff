## 11. Review and correction

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Chapter 11 begins only after the required Chapters 8–10 have approved designs where required and completed implementations. At that point, `v_new` remains available for continued review, correction and direct user feedback.

Every visual difference from `v_old` that is not explicitly authorized by Appendix A is a defect. “Similar,” “close,” “equivalent,” or “improved” is not sufficient; review compares the two implementations at matching viewport, URL, backend data and UI state.

Review covers the complete frontend, including:

- application structure;
- provider behavior;
- backend requests and TanStack Query ownership;
- workspace and Tab state;
- metadata freshness;
- controls and input behavior;
- ChangeSet loading;
- strict file ordering;
- FileTree;
- FileCard states;
- rendering;
- errors and Toasts;
- headers and Portals;
- navigation;
- selection;
- counters;
- scrolling;
- line pins;
- virtualization;
- folding;
- notebooks;
- HUD behavior;
- hotkeys;
- styling;
- layout;
- pixel-perfect visual parity;
- performance;
- missing behavior;
- behavior that technically works but remains confusing or unpleasant.

The topic files in `../spec/` remain the authority for intended frontend behavior and architecture.

The practical chapter files in this directory remain the authority for rewrite order and coexistence with `v_old`.

When implementation disagrees with the specification, correct the implementation.

When user feedback changes an agreed requirement, present the proposed topic-file correction and wait for explicit permission before editing the specification.

Corrections remain inside `v_new`. They must not introduce imports from `v_old`, copied `v_old` state, compatibility providers, compatibility API responses or alternate code paths that bypass the new architecture.

Review and correction continue in this order:

1. Present the current `v_new` behavior to the user.
2. Investigate every reported problem against the implementation and both specifications.
3. Explain the cause and proposed correction.
4. Apply only the correction authorized by the user.
5. Recheck the affected behavior in the browser.
6. Continue responding to feedback until the user explicitly accepts the rewritten frontend.

Query-lifecycle review includes:

- Dirdiff → Git → Dirdiff;
- Tab A → Tab B → Tab A;
- manifest refetch replacing the complete snapshot;
- no old lazy/file observer crossing a manifest change;
- repository cache expiration replacing the complete snapshot;
- ordinary file failure remaining localized;
- view change causing no backend work;
- outer layout state surviving only at the agreed boundaries.

`v_old` remains available as the stable alternative throughout this chapter.

This chapter does not authorize:

- deleting `v_old`;
- moving `new/` into the root of `frontend/src/`;
- removing the frontend switch;
- changing the default frontend;
- removing migration storage;
- treating `v_new` as accepted merely because Chapters 1–10 were implemented.

Deletion of `v_old` and final cutover happen only in a separate, explicitly authorized follow-up.
