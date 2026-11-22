---
name: review-patch
description: How to use dirdiff's agent API (the `dirdiff agent` CLI) as the reviewer of a patch. Use when asked to review a patch on dirdiff. It covers the board mechanics only, not what to check.
---

# Review a patch on dirdiff

You are the reviewer. The patch is shown on a dirdiff board; you read it, post
findings there as Threads, and recheck the author's answers. This skill covers
the board. What to check, how strictly, whether to run tests or other checks,
and whether you may edit code come from the instructions of the repository
under review (its agent or contributor files, whatever they are called) and
from the human. The skill adds one rule: record concrete findings supported by
evidence on the board.

`dirdiff agent --help` and `dirdiff agent COMMAND --help` list every command
and option.

## Session

Every command takes `--session PATH`: a JSON file holding your server,
identity, role, Tab, latest Snapshot and activity cursor. Use one file per
review, never share it with another agent, and pass it on every call; nothing
else persists between your tool calls. Keep it outside the reviewed
repository's working tree so it cannot end up in the patch.

## Start

```sh
dirdiff agent init --session S.json --onboard-url URL --role reviewer \
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
with `--identity-from S.json` (the session of a review you already joined); a
second `init` with the same name is rejected.

`init` prints the Snapshot directory, the changed files and the open Threads.
Read the Threads first (`threads`, `show THREAD_ID`) so you do not repeat a
finding. Read [references/snapshot_structure.md](references/snapshot_structure.md)
before reading captured files or choosing `--bay` and `--lines`.

## The model

- A Thread is a conversation on one code range or on the whole review. Any
  participant may comment on, resolve or reopen any Thread.
- A comment hands the Thread to the other role: `waiting on author` or
  `waiting on reviewer`. A Thread made in the browser may say `both`.
- Resolve a Thread when its finding is addressed and you verified it. Reopen it
  with a comment when it turns out not to be.
- Delete a Thread, or a Comment that is not yours, only when the human
  explicitly asks.

## Post findings

```sh
dirdiff agent find --session S.json --path src/app.py --side right \
  --lines 10-12 --bay flatfile <<'EOF'
Body in Markdown.
EOF
dirdiff agent find --session S.json --review --body-file finding.md
```

Use `--path --side --lines --bay` for a finding about one range, normally on
`right`. Use `--review` for anything not tied to one range (a missing test, a
contract broken across files). State the observable problem, why it matters,
and where to look. Cite the file and behaviour, not a commit hash: a hash stops
resolving if history is later rewritten.

Bodies come from `--body-file` or stdin, never from an argument. In a heredoc,
quote the delimiter (`<<'EOF'`): an unquoted one lets the shell expand
backticks and `$`, silently stripping code spans from the body.

## Later rounds

1. `news`, then `capture`. A changed Snapshot id is normal; the author moved on.
2. `threads`, then `show` each Thread `waiting on reviewer`. Check the new
   Snapshot and the live code; the author's prose is not evidence.
3. `comment THREAD_ID` if an objection remains, or `resolve THREAD_ID
   --body-file F` recording what you verified.
4. Wait for the author with `wait --session S.json --timeout 540` (1-540
   seconds), not by polling `news`. It returns when someone else acts; your
   own actions never wake it. When it returns with activity, repeat from 1.
5. When it times out, run `reviews`. A `head` review is tied to the HEAD
   commit, so an author who committed continues in a new review of the same
   Tab: it shows Threads waiting `on reviewer`. Join it with a new session
   file (`init` with the same onboarding URL and `--identity-from S.json`) and
   repeat from 1 there.

The review is over when the human says so, or when no Thread is open in your
review and three `wait` calls in a row time out with `reviews` showing nothing
waiting on the reviewer. Until then your turn does not end: answering every
Thread is one round, not the end of the review.

## Rules

- Times are UTC.
- A failing command prints the server's message and exits non-zero. Report it;
  do not retry with altered data.
- Fix a Comment of yours that came out wrong with `edit THREAD_ID COMMENT_ID`
  rather than posting a correction.
- The board is the record. Report to whoever asked you with counts, Snapshot
  ids, activity ids and who acts next ("Posted 3 findings through activity 82;
  author acts next"), not comment bodies. Do not read accepted actions back.
