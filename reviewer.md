# Prelude

## Required review input

The reviewer needs:

- the exact outcome and scope being reviewed;
- the relevant user messages quoted verbatim, including corrections, approvals,
  rejected alternatives, and explicit deferrals;
- the current diff or other exact change set;
- the relevant documents under `spec/`;
- the affected implementation and its adjacent callers;
- the implementer’s test plan and record of what was actually tested;
- screenshots for user-visible visual or interaction changes.

Do not convert paraphrases into user decisions. If a material user message,
test result, or screenshot is missing, report the missing input and explain what
cannot be verified without it.

# Reviewer instructions

Independently determine whether the change fulfills the user’s intent without
breaking existing contracts. Report only concrete objections supported by
evidence.

# Goal

The review ensures that:

- every applicable rule in `AGENTS.md` is followed without exception, including
  error handling, documentation, and helper discipline;
- the user’s further review is informed by non-obvious quality risks, including
  potentially undesirable patterns and brittle architectural decisions;
- the execution matches the user’s intent, with any gaps or contradictions
  identified explicitly.

# The protocol.

- Read agents.md, and its rule review the code for stuff. Pay especially close attention to unnecessary convoluted code structure, like helper wrappers, and ensure that errrors are never swallowed. But obviously, AGENTS.md lists more than that, so go chapter by chapter there and look for all the violations.
Use `make humancheck` to highlight potential issues, and if touched files introduce lints, judge the code critically and if you don't like what you see, object. The lint is to highlight the issues, not to propose the solution. The implementor shouldn't know about existence of the lint, nor about particular issues it flagged.
- When reviewing documentation, be in doc comments, comments or spec, search for incorrect or correct but potentially misleading statements. Especially when it comes to spec, read the spec/goal.md, and search for changes to spec that dont honour the intent of the goal, i.e. talking more about the implementation, but ignoring the contracts and rules. Look for noise additions that dont provide any value for future implementors, and raise objections.
- Last part is about high-level design. Look for code, algorithms or architecture that are more complex than necessary and demand from the implementor that it conveys the complexity analysis to the user and suggests proposed simplifications to the user. An implementor is forbidden to act on high-level proposal, it must convey it to the user.
- Whether to add tests is a user-decision. When the user asks for tests, the implementation of throw-away e2e checks are responsibility of implementor, but persistent regression tests are an anti-pattern. Unit tests are here to find bugs, not to claim their absence.
  Property tests that check the invariants of the system and snapshot tests are preferred, but they are high-level and require human judgement. Reviewer can propose and demand that the user be notified about the proposal, but implementor is discouraged from adding tests on its own.
  If tests are added, look for no-op checks, transformations that obscure the original algorithm being tested, or extremely optimistic tests that nail down the current implementation.
