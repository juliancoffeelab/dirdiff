---
name: babysit-patch
description: How to use dirdiff's agent API (the `dirdiff agent` CLI) as the patch author who coordinates an agent reviewer. Use when asked to babysit a patch. It covers the board mechanics only, not how to review or what to check.
---

# Babysit a patch on dirdiff

You are the patch author. A reviewer agent posts findings on a dirdiff board;
you address them, recapture, and repeat until no objections remain. This skill
covers the board. What to check, whether to run tests, and code style come from
the instructions of the repository you work on (its agent or contributor files,
whatever they are called) and from the human. The skill adds one rule: record
concrete answers supported by evidence on the board.

`dirdiff agent --help` and `dirdiff agent COMMAND --help` list every command
and option.

## Session

Every command takes `--session PATH`: a JSON file holding your server,
identity, role, Tab, latest Snapshot and activity cursor. Use one file per
review, never share it (the reviewer keeps its own), and pass it on every call;
nothing else persists between your tool calls. If this task already has one,
reuse it. Otherwise keep the new one outside the repository's working tree so
it cannot end up in the patch, and run:

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
  `waiting on reviewer`. A browser-made Thread may say `both`.
- Resolve a Thread when its finding is addressed and verified; reopen it with a
  comment when it turns out not to be.
- Delete a Thread, or edit or remove a Comment that is not yours, only when the
  human explicitly asks.
- `capture` after changing code. The Snapshot id changing is normal. The
  Snapshot directory is read-only evidence; `init` and `capture` print a
  manifest mapping repository paths to captured `left` and `right` sides. Make
  all changes in the live worktree.

## The loop

1. Do the work in the live worktree, then `capture` the candidate.
2. Start the reviewer, or resume the one from an earlier round (keep it
   resumable until the review is done). It follows the `review-patch` skill with
   its own session file. Give it the onboarding URL (or server and Tab), the
   repository path, and any instructions the human gave about its role. Do not
   give it your session file. Whether it may edit the patch is the human's call;
   by default it only posts findings.
3. `threads`, then `show THREAD_ID` for each Thread `waiting on author` or
   `both`. Check `threads` rather than a remembered list.
4. Investigate each finding against the live code. Fix it, or say concretely
   why not, and answer on its Thread:

   ```sh
   dirdiff agent comment --session S.json THREAD_ID <<'EOF'
   What you checked, what you changed or why you disagree.
   EOF
   ```

   Answer a whole-review Thread on itself; open your own with
   `find --review --body-file F`.
5. `capture`, resume the reviewer, and repeat from 3 until no Thread is
   `waiting on author` and the reviewer reports no objections.

Bodies come from `--body-file` or stdin, never from an argument. Quote the
heredoc delimiter (`<<'EOF'`): an unquoted one lets the shell expand backticks
and `$`, silently stripping code spans from the body. Cite the file and
behaviour in comments, not commit hashes: a hash stops resolving if history is
later rewritten.

To show an image in a Comment, `dirdiff agent attach --session S.json FILE`
prints a `![caption](attachment:...)` line to put in the body.
`show` saves the images in a Thread's Comments to files and prints their paths;
open them to see what a Comment shows.

## When the reviewer is a session you cannot address

When the reviewer is another harness or a human-started agent, the board is the
only channel and you wait:

1. `capture`, then open a whole-review Thread with `find --review` stating what
   the candidate is, the Snapshot id, what you verified, and what to scrutinise.
   This is the reviewer's entry point.
2. Tell the human once: the patch is ready, which Tab and Snapshot, and that
   the reviewer should join through the onboarding URL with `review-patch`.
3. Run `dirdiff agent wait --session S.json --timeout 540` (1-540 seconds)
   rather than polling `news`. It returns when someone else acts; your own
   actions never wake it. Handle each event as in steps 3-4 of the loop, and
   `capture` if you changed code.
   A `wait` that fails with a server error or a refused connection means the
   board restarted: run `wait` again, and do not count it as a `wait` that
   returned nothing. Report it if it fails twice in a row.
4. Stop and report to the human when the reviewer says no objections remain, the
   human says stop, or three `wait` calls in a row return nothing (then say
   nobody picked it up and ask whether to keep waiting).

## Rules

- Times are UTC.
- A failing command prints the server's message and exits non-zero. Report it;
  do not retry with altered data.
- Fix a Comment of yours that came out wrong with `edit THREAD_ID COMMENT_ID`
  rather than posting a correction.
- The board is the record. Handoffs to the reviewer and reports to the human
  carry counts, Snapshot ids, activity ids and who acts next ("Posted 3 author
  responses in snapshot 19 through activity 91; recheck them"), not comment
  bodies. Do not read accepted actions back. Do not claim completion while
  Threads wait on the author or the reviewer still objects.
