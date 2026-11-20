---
name: request-review
description: Use when you implemented a change to dirdiff and are about to ask for its review.
---

# Handing work to a reviewer

For the implementor. The reviewer's own instructions are in the `review` skill;
do not read them.

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

## Rounds

When the user requires a zero-objection review, the existing reviewer reviews
every accepted fix and continues reviewing until that reviewer explicitly
reports zero remaining objections. Start a fresh independent reviewer only
after the existing reviewer has no objections. Give each fresh reviewer fresh
context and the required input above, then repeat the same sequential process
until the required independent review is complete.
