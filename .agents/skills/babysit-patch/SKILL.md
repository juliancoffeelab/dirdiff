---
name: babysit-patch
description: Use when user asks to babysit a patch via API, or just posts "round" with no additional context.
---

# Babysit a patch

Act as the patch author and review coordinator. Use ordinary harness tools for
repository work and direct HTTP calls for dirdiff. Treat dirdiff as the shared
record of review substance; keep parent and subagent messages to orchestration.

## API command reference

Prefer the standard-library Python heredocs in
[references/python_commands.md](references/python_commands.md). They preserve
multiline Comment bodies without shell-escaping ambiguity. If Python is not
available, read [references/commands.md](references/commands.md) and use its
`jq` and `curl` commands instead.

## Begin the work

1. Read the repository instructions, relevant specifications, implementation,
   adjacent callers, and existing tests before editing.
2. Reuse the Profile id, Snapshot id/path, and activity boundary retained from
   the current task's earlier round. Start with `continue_review` to recapture
   that session. Call `/api/agent/join_review` only when no retained session
   exists, then retain its returned values for every later round.
3. Read the unfiltered active Thread context before implementing so existing
   findings are not rediscovered.
4. Work only in the live worktree. Treat every captured Snapshot path as
   read-only evidence.
5. Implement the requested patch and perform verification proportionate to the
   change.
6. Capture the completed candidate through `/api/agent/continue_review`.

If a required API address, Room, or Tab context cannot be discovered from the
current project, ask for that missing input. Do not invent one.

## Captured Snapshot appendix

Read [references/snapshot_structure.md](references/snapshot_structure.md) when
inspecting captured evidence or briefing a reviewer. Snapshot child names are
opaque File ids; each child contains an exact `left` and/or `right` side. Keep
the Snapshot read-only and make every implementation change in the live
worktree.

## Start independent review

After you're confident in your work, spawn a reviewer subagent instructed to
follow the `review-patch` skill. If a reviewer from an earlier round of the
current task already exists, resume that reviewer instead of spawning a new
one.
Give it the exact user outcome and required verbatim quotes, repository
instructions, relevant specifications, and if required, screenshots.
Require it to follow the captured Snapshot structure reference and inspect
every captured File pair.
The reviewer is forbidden from editing the patch.

The reviewer should put findings in dirdiff and return only a compact handoff,
for example:

```text
Posted 3 findings through activity 82. Author attention is required.
```

Do not require the reviewer to duplicate finding bodies in its response.

## Address findings

1. Read `/api/agent/threads?for=author`.
2. Investigate each finding against the live implementation and its contracts.
3. Fix an accepted finding or explain concretely why it should not be accepted.
4. Post one `author-response` for each addressed finding. Batch independent
   responses in one action transaction.
5. Resume the same reviewer. Report only the new Snapshot and compact API
   progress, for example:

```text
Posted 3 author responses in snapshot 19 through activity 91. Recheck them.
```

Continue with the same reviewer until all threads have no objections.
Keep every reviewer subagent alive and resumable until the review round is
fully done for all of them.

An open Thread with `attention_after = both` appears in the author inbox and
may be advanced with `author-response`. An `inert-comment` preserves lifecycle
and attention and must not stand in for an author response.

## Finish

Do not claim completion while actionable author Threads or reviewer objections
remain.
When review is complete, report the patch outcome, checks actually run,
and review disposition without repeating every dirdiff Comment.

## Keep transactions compact

- Do not routinely print payloads or read an accepted action back.
- Do not reconstruct status or attention locally.
- Do not duplicate dirdiff Comment bodies in agent handoffs.
- Communicate counts, Snapshot ids, activity boundaries, and the next required
  role.
- Use one atomic action batch for independent actions that are ready together.
- On an HTTP or validation failure, report its returned diagnostic. Do not
  retry with invented or weakened data.

## Send multiline Comments safely

Use the Python reference by default. If using the shell fallback, never pass a
single-quoted string containing `\n` to `jq --arg`; it sends literal
backslashes and `n` characters. Follow the quoted-heredoc pattern in the shell
reference.

If malformed content is actually observed, stop compounding it and report the
affected action. Do not add routine read-after-write checks. Do not delete or
rewrite review history without explicit human authorization and an available
authorized instrument.
