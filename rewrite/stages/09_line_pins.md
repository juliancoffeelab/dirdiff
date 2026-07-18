## 9. Line pins

Read [guidance.md](guidance.md) before implementing this chapter; it governs every practical rewrite stage.

Line pins are a separate required rewrite system from hunk navigation.

> **TODO design gate — this chapter is not authorized for implementation.** Existing line-pin notes and implementation behavior are unreliable until URL identity, asynchronous target arrival, repeated restoration, layout instability, retries, cancellation, browser history, and interaction with user or programmatic scrolling have been re-investigated together.

Implementation must not begin until a separate line-pin specification has been presented to and explicitly approved by the user. That specification must define URL identity, direct DOM ownership, initial and repeated restoration, asynchronous target arrival, layout instability, retry termination, user-scroll interaction, navigation interaction, cleanup, and browser history behavior.

This chapter does not authorize:

- adding line-pin state to NavigationProvider;
- routing pins through hunk selection;
- selecting a hunk as a side effect of pin restoration;
- inventing a shared scrolling abstraction;
- copying the old implementation without the approved design;
- inferring unspecified behavior during implementation.

Once approved, the line-pin specification becomes this chapter's authority. Until then, this required chapter remains gated: stop and request the missing design rather than implementing a compromise or treating line pins as an optional follow-up.

`v_old` remains available until final cutover is explicitly authorized.
