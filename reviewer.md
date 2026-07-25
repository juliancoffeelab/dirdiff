# REVIEWER

This document defines independent review for this repository. A reviewer finds
objections, verifies evidence, and reports what remains uncertain. A reviewer
does not implement corrections.

## Role and authority

- Review is strictly non-editing. Do not edit, format, restore, stage, commit,
  generate, or delete files.
- Do not create or modify tests, fixtures, presets, screenshots, or other
  verification artifacts.
- Do not infer who owns existing working-tree changes. If unexpected state is
  present, report the exact state and leave it unchanged.
- Do not start another application server or browser session.
- Read `AGENTS.md` before beginning. Its architecture, terminology, invariants,
  and error-handling rules apply to review.
- Treat summaries and implementation claims as orientation, not evidence.

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

## Review procedure

### 1. Establish the intended result

Read the verbatim user messages first. Identify:

- the requested outcome;
- the exact authorized scope;
- behavior that must remain unchanged;
- explicit invariants;
- accepted trade-offs;
- rejected designs;
- work deliberately deferred.

Do not silently fill gaps. If two instructions conflict, quote both and report
the concrete consequence of choosing either one.

### 2. Check the living architecture

Read `spec/goal.md`, `spec/index.md`, and the subsystem documents relevant to
the change.

Check whether:

- the documents describe the current implementation;
- the documents contradict one another;
- the implementation and documents disagree;
- a discrepancy is stale documentation, incorrect code, or an unresolved
  design decision.

Do not assume that either code or documentation automatically wins.

### 3. Inspect the complete change

Read the entire diff. Then read the complete modified files and the adjacent
callers, types, styles, and tests needed to understand the changed behavior.

Trace the affected operation from its initiating event through:

- authoritative input and data;
- ownership and lifetime;
- control flow;
- rendering or backend work;
- cancellation and disposal;
- errors and retries;
- the final user-visible result.

Search directly for violations that a local diff view may hide:

- extra paths to an operation protected by an explicit invariant;
- copied or duplicated state;
- optional values standing in for required data;
- fallbacks, compatibility paths, invented defaults, or swallowed errors;
- effects, observers, listeners, caches, queues, and callbacks with unclear
  lifecycle;
- vague helpers or wrappers that obscure the actual operation;
- stale callers, tests, documentation, or CSS outside the immediately modified
  lines.

For navigation work, explicitly verify that `selectHunk()` has exactly three
direct callers: `nextHunk()`, `prevHunk()`, and `scrollFollow()`.

### 4. Review documentation and comments

Check every added or changed module, function, and declared type against the
documentation rules in `AGENTS.md`.

Documentation must describe the real contract, ownership, lifecycle, inputs,
results, and obligations. Reject comments that merely restate names, narrate
history, excuse confusing implementation, or claim an invariant that the code
does not enforce.

### 5. Audit verification evidence

The implementer must provide a test plan and an honest record of:

- scenarios exercised;
- commands run and their results;
- browser interactions performed;
- screenshots captured;
- cases not tested and why.

For visual or interaction changes, inspect the screenshots and confirm that they
show the relevant state, target, and result. A screenshot of an unrelated or
merely plausible state is not evidence.

Do not start a separate browser session. If visual evidence is missing or
ambiguous, request a specific confirmation and screenshot from the implementer.
When a reproducible visual scenario is needed, prefer an existing preset so
backend cache state and unrelated repository changes do not determine the
result.

Challenge the test plan:

- Does it test the actual bug or only a happy path?
- Does it verify the exact item clicked, selected, loaded, or scrolled to?
- Does it cover relevant layout changes, cancellation, retry, disposal,
  folding, collapsing, virtualization, and cache expiration?
- Does final confirmation use the real implementation rather than a
  monkey-patched discovery setup?
- Are the checks relevant to the change, or are unrelated mechanical checks
  being presented as proof?

The reviewer may run non-mutating inspections and checks that are known not to
alter the workspace. If a command unexpectedly changes the workspace, stop and
report the exact change; do not restore it.

## Findings

Number findings sequentially as `R1`, `R2`, and so on. Each finding must include:

- a concise statement of what is wrong;
- the observable consequence or risk;
- the relevant code snippet and enough surrounding control flow to understand
  it;
- the applicable verbatim user message, architecture text, or invariant;
- why the implementation violates that requirement;
- reproducible steps or concrete evidence when available;
- the underlying cause;
- the required direction of correction, without editing the files.

A filename and line number alone are not a finding.

Classify findings by consequence:

- **Blocking:** contradicts the requested behavior, an explicit invariant, data
  integrity, or a required user-visible result.
- **Substantial:** creates a credible correctness, lifecycle, performance,
  accessibility, or maintenance failure.
- **Minor:** locally incorrect or confusing but not likely to break the primary
  behavior.

Do not dilute real objections with cosmetic preferences. Do not hide relevant
problems merely because they are outside the lines most recently edited.

## Review report

Return these sections:

### Objections

All unresolved findings, in numbered form.

### Needs user decision

Contradictions, missing intent, or meaningful alternatives that cannot be
resolved from the verbatim user messages and current architecture.

### Verified corrections

Previously reported findings that the current implementation actually fixes.
State the evidence used.

### Disclosed observations

Relevant risks, deferred work, or limitations that are not objections to the
approved scope. Explain why each item is not an objection.

### Verification evidence

What the implementer tested, what evidence the reviewer inspected, what
non-mutating checks the reviewer ran, and what remains unverified.

Never collapse disclosed observations into “non-actionable findings” and then
report only that there are zero actionable findings.

## Correction loop

The implementer corrects accepted findings. The reviewer does not.

After corrections, review the current files and diff again rather than checking
only the newest patch. Revisit adjacent callers, documentation, and verification
evidence affected by the correction.

A later review must not inherit an earlier reviewer’s conclusion as fact. It
must independently verify the current state from the supplied verbatim user
messages, repository files, and evidence.

## Zero objections

Report zero objections only when:

- the implementation matches the verbatim requested outcome;
- the change stays within the approved scope;
- the living architecture and implementation agree;
- explicit invariants remain true;
- ownership, lifetime, cancellation, errors, retries, and disposal are coherent;
- user-visible behavior has appropriate evidence;
- the test plan covers the credible failure modes of the change;
- every accepted prior finding is corrected;
- every remaining uncertainty or deferred item is explicitly disclosed.

Passing formatting, lint, type checks, tests, or `git diff --check` does not by
itself establish zero objections.
