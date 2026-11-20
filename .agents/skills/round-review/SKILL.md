---
name: round-review
description: How to use dirdiff's agent API (the `dirdiff agent` CLI) as the patch author answering a human reviewer's Threads. Use when the user posts "round" or asks to respond to threads. It covers the board mechanics only, not how to review or what to check.
---

# Answer the human's Threads on dirdiff

You are the patch author. The human is the reviewer and posts findings as
Threads on a dirdiff board; you investigate each against the live worktree and
answer on the Thread. Do not spawn a reviewer. This skill covers the board.
What to check, how to verify a claim, whether to run tests, and code style come
from the instructions of the repository you work on (its agent or contributor
files, whatever they are called) and from the human. The skill adds one rule:
record concrete answers supported by evidence on the board.

`dirdiff agent --help` and `dirdiff agent COMMAND --help` list every command
and option.

## Session

Every command takes `--session PATH`: a JSON file holding your server,
identity, role, Tab, latest Snapshot and activity cursor. Use one file per
review, never share it, and pass it on every call; nothing else persists
between your tool calls. If this task already has one, reuse it. Otherwise keep
the new one outside the repository's working tree so it cannot end up in the
patch, and run:

```sh
dirdiff agent init --session S.json --onboard-url URL --role author \
  --name NAME --model MODEL --harness HARNESS
```

Use the onboarding URL the human gives you. Without one, ask which Tab and
which server, then build `SERVER/api/agent/onboard?` plus one of these
(percent-encode values; `project_id` comes from `dirdiff mark --list`):

- `tab=head&project_id=N`
- `tab=refs&project_id=N&left=A&right=B`
- `tab=branch-review&project_id=N&base_source=local&base_branch=X&review_source=local&review_branch=Y`
  (a `remote` source also needs `base_remote` / `review_remote`)
- `tab=pull-request&pull_request_url=URL`

To join a second review as the same agent, replace `--name --model --harness`
with `--identity-from S.json` (the session of a review you already joined).

## The model

- A Thread is a conversation on one code range or on the whole review. Any
  participant may comment on, resolve or reopen any Thread.
- A comment hands the Thread to the other role: after yours it is
  `waiting on reviewer`. A Thread the human made in the browser may say
  `both`, and `waiting on reviewer` after you answer is correct, not a task.
- Resolve a Thread when its finding is addressed and you verified it; reopen it
  with a comment when it turns out not to be.
- Delete a Thread, or edit or remove a Comment that is not yours, only when the
  human explicitly asks.

## A round

1. `capture` the current work. A changed Snapshot id is normal. The Snapshot
   directory is read-only evidence; `init` and `capture` print a manifest
   mapping repository paths to captured `left` and `right` sides. Make all
   changes in the live worktree.
2. `threads`, then `show THREAD_ID` for each Thread `waiting on author` or
   `both`. Check `threads` rather than a remembered list.
3. Investigate each finding against the live code before deciding. A claim you
   reproduced is worth more than one that sounds right, for the human's
   findings and for your own agreement.
4. Answer each on its own Thread:

   ```sh
   dirdiff agent comment --session S.json THREAD_ID <<'EOF'
   What you checked, what you found, what you propose.
   EOF
   ```

   If the finding is right, say so plainly. If you disagree, say so, with what
   you checked and why; the human being the reviewer does not make a finding
   correct, and complying silently with one you believe is wrong hides the
   disagreement. Answer a whole-review Thread on its own Thread; open your own
   with `find --review --body-file F`.
5. Change code only after the human authorises that change on that Thread. Then
   edit the worktree, `capture`, and post what you did on the Thread. The
   human resolves it or replies.

Bodies come from `--body-file` or stdin, never from an argument. Quote the
heredoc delimiter (`<<'EOF'`): an unquoted one lets the shell expand backticks
and `$`, silently stripping code spans from the body. Cite the file and
behaviour in comments, not commit hashes: a hash stops resolving if history is
later rewritten.

## Waiting

To wait for the human, run `dirdiff agent wait --session S.json --timeout 540`
(1-540 seconds) rather than polling `news`. It returns when someone else acts;
your own actions never wake it.

## Rules

- Times are UTC.
- A failing command prints the server's message and exits non-zero. Report it;
  do not retry with altered data.
- Fix a Comment of yours that came out wrong with `edit THREAD_ID COMMENT_ID`
  rather than posting a correction.
- The board is the record. Report to the human with counts, Snapshot ids,
  activity ids and what needs them next, not comment bodies. Do not read
  accepted actions back.
