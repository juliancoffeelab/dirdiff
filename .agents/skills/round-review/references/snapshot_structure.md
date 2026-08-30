# Snapshot appendix

## Why you read a Snapshot

A captured Snapshot is the immutable evidence behind a finding — the exact bytes
the user was looking at when they wrote it. It is not a checkout and never an
edit destination. Every change goes in the live worktree.

`snapshot_path`, returned by `join_review` or `continue_review`, has this
layout:

```text
<snapshot_path>/
  <opaque-file-id>/
    left   # present only when the captured File has a left side
    right  # present only when the captured File has a right side
    ...    # private format sidecars may also be present
```

The immediate directory name is an opaque File id, not a repository path.
`left` and `right` hold exact captured side bytes. Both present is a before/after
pair; only `right` means added or untracked; only `left` means removed. Private
link sidecars may sit beside them; do not treat those as changed Files or pass
their paths to review instruments. Public File ids and side names carry no
manifest or outer-File repository mapping. Private link metadata may state its
captured nested paths, but it is not a general manifest.

## Follow a finding to its bytes

An author-inbox Thread supplies its captured absolute `file` path while the File
remains located. Read that exact side and, when present, its sibling:

```sh
export DD_FILE='absolute-file-path-from-the-author-inbox-thread'

file "$DD_FILE"
sed -n '1,240p' "$DD_FILE"

DD_PAIR_DIR="$(dirname "$DD_FILE")"
find "$DD_PAIR_DIR" -mindepth 1 -maxdepth 1 -type f \
  \( -name left -o -name right \) -print
```

Use content-appropriate tools; do not force binary, notebook, or other
structured data through a text reader.

A Thread whose placement is missing has no current `file`. Use its
`original_excerpt` and location metadata instead, then investigate the
corresponding live-worktree code.

The captured side proves what the user inspected. Resolve the finding against
the live repository path established by Thread context and repository
inspection — never infer that path from the opaque File id.

## The Snapshot is not the argument

The user is reading their own view of the same change, and their Snapshot may be
newer than yours. A Thread you cannot find is a reason to capture again, not
evidence that the review is broken.

Never edit, rename, delete, annotate, or generate files inside any Snapshot
directory. Older Snapshots stay immutable historical evidence.
