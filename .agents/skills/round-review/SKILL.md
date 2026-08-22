---
name: round-review
description: Use when user reviews your patch, or just post "round" without context.
---

# Answer the user's review

Act as the patch author answering one human reviewer. The user posts findings as
dirdiff Threads; you investigate them against the live worktree and respond.
Use ordinary harness tools for repository work and direct HTTP calls for dirdiff.

Dont spawn any reviewer subagents, user *is* the reviewer.

## API command reference

[`references/api_commands.md`](references/api_commands.md) holds every HTTP call
this skill needs, as standard-library Python heredocs.
[`references/snapshot_structure.md`](references/snapshot_structure.md) explains
the captured Snapshot layout before you read evidence from it.

Keep every heredoc delimiter quoted: `python3 - <<'PY'`, never `python3 - <<PY`.
An unquoted delimiter lets the shell expand backticks and `$` before Python sees
the body, so a Comment quoting code loses its inline-code spans while the HTTP
call still succeeds. Nothing flags that corruption.

## Use the existing connection

Reuse the `DD_PROFILE_ID`, `DD_SNAPSHOT_ID`, `DD_SNAPSHOT_PATH`, and
`DD_LAST_ACTIVITY_ID` this task already retained, and start with
`continue_review`. Call `/api/agent/join_review` only when the task has no
retained session, then retain what it returns for every later round.

`continue_review` commonly returns a different `snapshot_id` than the one you
last quoted. That is expected drift, not an error.

If the API address, Room, or Tab cannot be discovered from the project, ask.
Do not invent one.

## The round

1. Capture the current work through `/api/agent/continue_review`.
2. Read `/api/agent/threads?for=author`.
3. Answer every finding (below).
4. On user explicit authorization per thread, implement the decision.

## Answering a finding

Investigate against the live implementation and its contracts before deciding.
Verify by running things — tests, a targeted script, the browser when it is a
rendering claim. A finding that reproduces is worth more than a finding that
sounds right, and that applies to your own agreement as much as to the report.

Then either fix it, or explain concretely why it should not be accepted.

Disagreeing is part of the job. The reviewer being the user does not make a
finding correct, and complying with one you believe is wrong produces a worse
patch and hides the disagreement that would have caught it. Say what you
checked, what you found, and what you propose. If the finding is right, say so
plainly rather than defending the original.

Post one `author-response` per addressed Thread, batching independent responses
into one action transaction. `author-response` requires the Thread to still be
`open` with `attention` in `{author, both}`; a Thread you already answered sits
at `attention = reviewer` until the user acts again, and a second response fails
with `state_conflict`. Re-read `/api/agent/threads?for=author` rather than
reusing a remembered inbox.

An `inert-comment` preserves lifecycle and attention and must not stand in for
an author response.

## Thread lifecycle belongs to the user

Never resolve, reopen, or delete a Thread. Never edit or remove review history.
Those are the reviewer's instruments and the reviewer is the user, even when a
finding is verified fixed and obviously ready to close.

Threads you have answered stay open. That is correct, not an outstanding task.

## Reporting

Keep dirdiff as the record of substance and chat as orchestration. Report
counts, Snapshot ids, activity boundaries, and what needs the user next. Do not
paste Comment bodies back into chat, print payloads, or read an accepted action
back to confirm it.

On an HTTP or validation failure, report the returned diagnostic. Do not retry
with invented or weakened data.

## Implementation

AGENTS.md rule still holds, you dont have the right to edit code without user
explicit permission or authorization, per thread.
When you get the permission, post what you've done on the thread and wait for
user to resolve the finding, or return it again, continuing the discussion.
