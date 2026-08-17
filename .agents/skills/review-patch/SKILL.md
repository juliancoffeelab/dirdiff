---
name: review-patch
description: Use when asked to review a patch using API.
---

# Review a patch

Act as the independent patch reviewer. Use ordinary harness tools to inspect and
verify the captured change and direct HTTP calls to record review actions in
dirdiff. Put substantive findings and decisions in dirdiff; return compact
orchestration messages to the parent agent.

## API command reference

Prefer the standard-library Python heredocs in
[references/python_commands.md](references/python_commands.md). They preserve
multiline Comment bodies without shell-escaping ambiguity. If Python is not
available, read [references/commands.md](references/commands.md) and use its
`jq` and `curl` commands instead.

## Establish review input

Read `reviewer.md`.
Ensure that author gave you *all* required information, ask if not.

Read [references/snapshot_structure.md](references/snapshot_structure.md)
before inspecting captured Files. Its opaque File-pair layout, read-only rule,
and exact-side-path contract are part of this workflow.

Join through `/api/agent/join_review`. Retain the returned Profile, Snapshot,
Snapshot path, and activity boundary. Treat the Snapshot path as read-only.
Read the unfiltered active Thread context before inspecting code so existing
findings are understood and not duplicated.

## Inspect independently

Enumerate and inspect every captured File pair under `snapshot_path`, then read
enough adjacent live-repository implementation to support every conclusion.
Choose appropriate tools and checks, prefer reading the code and when necessary
interacting with the project. Do not run mechanical checks or tests — this
includes `make tscheck`, `make eslint`, `make mypy`, `make ruff`,
`make pyflake`, and `pytest` — even to double-check a finding "for
completeness". Those are the implementor's responsibility; running them here
duplicates that work without adding review signal. If a mechanical check is
relevant to a finding, name the check in the finding body and let the
implementor run it.

Review for three non-negotiable principles:

1. Simplicity: reject unnecessary state, indirection, duplication, optionality,
   compatibility behavior, or multiple paths for one operation.
2. Performance: reject avoidable repeated work, excess queries, unbounded work,
   blocking interaction paths, or other responsiveness regressions.
3. Correctness: reject contract violations, invalid boundaries, broken error or
   lifecycle behavior, and incorrect user-visible results.

For each principle, search for regressions introduced by the patch. Report only
concrete objections supported by evidence.

## Record findings

Post each objection as `create-finding`.
Batch independent findings in one atomic action transaction.
A finding should state the observable problem, the violated contract or
principle, the likely consequence, and enough location context for the author to
investigate it.

Use `inert-comment` only for information that should preserve current lifecycle
and attention. Do not use it instead of `reviewer-return` or
`reviewer-resolve`.

After posting, return a compact handoff such as:

```text
Posted 3 findings through activity 82. Author attention is required.
```

Do not repeat the finding bodies in the parent response.

## Recheck author responses

When resumed, continue as the same reviewer:

1. Capture or accept the revised Snapshot through
   `/api/agent/continue_review`. Its `snapshot_id` commonly differs from any
   Snapshot id named in your task brief or an earlier handoff — the branch
   moves between rounds, and this drift is expected, not an error. Always use
   the `snapshot_id`/`snapshot_path` this call just returned.
2. Use the response's `file_delta` (`added`/`changed`/`removed` captured
   paths) to find the File pairs that actually changed instead of
   re-enumerating or content-grepping the whole Snapshot. Use its
   `thread_delta` to see which Threads got new activity since your last
   `last_activity_id` instead of re-diffing a full Thread read; if
   `has_more_thread_changes` is true, call `continue_review` again with the
   latest `last_activity_id` before assuming you have seen everything.
3. Read `/api/agent/threads?for=reviewer` at one inclusive
   `through_activity_id`; reuse the boundary for every page in that read.
4. Read each author response and verify the actual revised Snapshot. The prose
   response alone is not evidence that the finding is fixed.
5. Recheck simplicity, performance, correctness, and plausible regressions.
6. Use `reviewer-return` with a concrete message if any material objection
   remains.
7. Use `reviewer-resolve` only when the human authorized resolution and the
   finding is genuinely addressed. Its message is required and should concisely
   record the verification supporting resolution.

An open Thread with `attention_after = both` appears in the reviewer inbox and
may be returned or resolved when its guards permit: both actions require the
Thread to still be `open` with `attention` in `{reviewer, both}`. Once a
`reviewer-return`/`reviewer-resolve` has already been posted, `attention`
moves to `author` (or the Thread becomes `resolved`); a second
`reviewer-return`/`reviewer-resolve` on the same Thread without an
intervening `author-response` fails with `state_conflict`
("... is not valid for this Thread outcome"). Re-read the reviewer inbox
before acting rather than working from a remembered Thread list.

After acting, return only compact progress, for example:

```text
Returned 1 finding and resolved 2 through activity 96.
```

When no objection remains:

```text
Resolved the remaining finding. Zero objections.
```

## Dangerous actions

`reviewer-resolve` requires human authorization to resolve verified findings.
A review instruction may authorize resolution for the review session; absent
such authorization, report that a finding is ready rather than resolving it.

`reviewer-delete` is exceptional. Use it only when the human explicitly asks to
delete the identified Thread or exact set of Threads. Ordinary review authority
never implies deletion authority. Never use deletion merely to repair wording,
hide a finding, or clean up history.

## Keep transactions compact

- Do not routinely print payloads or read an accepted action back.
- Do not reconstruct status or attention locally.
- Do not duplicate dirdiff Comment bodies in parent handoffs.
- Communicate counts, Snapshot ids, activity boundaries, and the next required
  role.
- Use one atomic action batch for independent actions that are ready together.
- On an HTTP or validation failure, report the exact failure. Do not retry with
  invented or weakened data.

## Send multiline Comments safely

Use the Python reference by default, and always quote the heredoc delimiter:
`python3 - <<'PY'`, never `python3 - <<PY`. An unquoted delimiter makes the
shell expand backticks and `$` inside the heredoc body before Python ever
sees it, so a Comment body that quotes code (`` `stopped` ``) gets silently
executed and stripped — the HTTP call still succeeds with the mangled text,
so nothing flags the corruption. This already happened in a review round:
three findings posted with their inline-code spans dropped, requiring
detection and reposting. Check the quoting before running any `python3`
heredoc you write.

If using the shell fallback, never pass a single-quoted string containing
`\n` to `jq --arg`; it sends literal backslashes and `n` characters. Follow
the quoted-heredoc pattern (`cat <<'BODY'`) in the shell reference.

If malformed content is actually observed, stop compounding it and report the
affected action. Do not add routine read-after-write checks. Use
`reviewer-delete` only under the explicit authorization above, then repost the
correct action if requested.
