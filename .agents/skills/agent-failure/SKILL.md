---
name: agent-failure
description: Use when an agent's work went wrong (misread task, broken or wrong output, repeated mistake) and the user asks why, or asks what to change in the code or instructions so that kind of agent failure doesn't recur. Investigates and reports; does not edit.
---

# Garden investigation

This skill is about building a better environment for an agent. The agent is
doing its best, and the human is doing their best. Sometimes that is not enough.

We investigate what went wrong in the process of communication between the human
and the agent, and what needs to be weeded out so that similar problems do not
happen again.

Investigate the failure and report its causes and remedies. Do not edit code,
instructions or artifacts during this workflow.

## 1. Triage the problem

Inspect what happened and whether the problem still exists. Check any repairs
already made and establish what they fixed, how, and what remains.

**Complete when:** the problem is triaged and its current state is established.

## 2. Find the causes

Examine these layers in order:

1. Architecture.
2. Existing code or artifacts.
3. Instructions.

Investigate how each shaped the agent's actions and contributed to the failure.
For non-code failures, examine the workflow that produced the artifacts.

**Complete when:** each layer has an evidence-supported explanation of how it
contributed, or why it did not; unresolved contributions remain explicit.

## 3. Propose remedies

Address the remaining causes in priority order:

1. Architecture.
2. Existing code or artifacts.
3. Instructions.

For every relevant remaining cause, explain what to change and how that change
fixes the problem. Account for repairs already completed. If no further change
is needed, explain why those repairs are sufficient.

**Complete when:** every remaining cause is linked to an identified change and
its expected effect, or completed repairs are explained as sufficient.
