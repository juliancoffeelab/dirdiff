# Implementor Snapshot appendix

## Why an implementor reads a Snapshot

Use a captured Snapshot to understand the immutable evidence behind a finding
and to brief a reviewer. Do not treat it as a checkout or patch destination.
Make every code change in the live worktree.

`snapshot_path` returned by `join_review` or `continue_review` has this
layout:

```text
<snapshot_path>/
  <opaque-file-id>/
    left   # present only when the captured File has a left side
    right  # present only when the captured File has a right side
```

The immediate directory name is an opaque File id, not a repository path.
`left` and `right` contain exact captured bytes. Both present means a
before/after pair; only `right` means added or untracked; only `left` means
removed. The tree has no manifest or repository-path mapping.

## Follow a finding to its bytes

An author-inbox Thread supplies its captured absolute `file` path when the
File remains located. Read that exact side and, when present, its sibling side:

```sh
export DD_FILE='absolute-file-path-from-the-author-inbox-thread'

file "$DD_FILE"
sed -n '1,240p' "$DD_FILE"

DD_PAIR_DIR="$(dirname "$DD_FILE")"
find "$DD_PAIR_DIR" -mindepth 1 -maxdepth 1 -type f \
  \( -name left -o -name right \) -print
```

Use content-appropriate tools. Do not force binary, notebook, or other
structured data through a text reader. If a Thread has no current `file`
because its placement is missing, use its original excerpt and location
metadata, then investigate the corresponding live-worktree code.

The captured side proves what the reviewer inspected. Resolve the finding
against the live repository path established by Thread context and repository
inspection; do not infer that path from the opaque File id.

## Recapture after implementation

After changing and verifying the live worktree, call `continue_review`.
Replace `DD_SNAPSHOT_ID`, `DD_SNAPSHOT_PATH`, and the activity boundary with
the returned values before handing work back to the reviewer.

The new Snapshot is the review candidate. Older Snapshots remain immutable
historical evidence. Never edit, rename, delete, annotate, or generate files
inside any Snapshot directory.

## Brief the reviewer

Give the reviewer the new Snapshot id/path and require it to use its own
reviewer Snapshot instructions to enumerate the complete capture. Do not claim
that inspecting only the Files mentioned by existing findings constitutes a
complete independent review.
