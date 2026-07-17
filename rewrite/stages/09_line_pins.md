## 9. Line pins

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Line pins are a separate system from hunk navigation.

Implementation must not begin until a separate line-pin specification has been presented to and explicitly approved by the user. That specification must define URL identity, direct DOM ownership, initial and repeated restoration, asynchronous target arrival, layout instability, retry termination, user-scroll interaction, navigation interaction, cleanup, and browser history behavior.

This chapter does not authorize:

- adding line-pin state to NavigationProvider;
- routing pins through hunk selection;
- selecting a hunk as a side effect of pin restoration;
- inventing a shared scrolling abstraction;
- copying the old implementation without the approved design;
- inferring unspecified behavior during implementation.

Once approved, the line-pin specification becomes this chapter's authority. Until then, stop and request the missing design rather than implementing a compromise.

`v_old` remains available until final cutover is explicitly authorized.
