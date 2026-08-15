# Captured Snapshot filesystem

## Contract

`snapshot_path` returned by `join_review` or `continue_review` is the root
of one immutable captured Snapshot. Treat the complete tree as read-only
evidence.

Its current layout is:

```text
<snapshot_path>/
  <opaque-file-id>/
    left   # present only when the captured File has a left side
    right  # present only when the captured File has a right side
  <opaque-file-id>/
    ...
```

Each immediate child directory represents one changed File pair. Its name is
an opaque backend-generated File id, not a repository path. `left` and
`right` contain the exact captured bytes used by dirdiff and the browser.

- Both files present: inspect them as one modified or renamed/copied pair.
- Only `right` present: inspect it as an added or untracked File.
- Only `left` present: inspect it as a removed File.
- Never assume text, UTF-8, a particular extension, or a repository filename
  from the opaque directory name.

The filesystem tree intentionally contains no manifest, ordering, display
name, or repository-path mapping. Use API Thread fields when an existing
Thread supplies file/location context. When creating a finding, pass the exact
absolute `left` or `right` path that was inspected; the backend maps that
captured side to its persisted File identity.

## Enumerate the complete capture

List every captured side without following unrelated paths:

```sh
find "$DD_SNAPSHOT_PATH" \
  -mindepth 2 \
  -maxdepth 2 \
  -type f \
  \( -name left -o -name right \) \
  -print
```

Or enumerate pairs with Python:

```sh
python - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["DD_SNAPSHOT_PATH"])
assert root.is_dir(), f"Snapshot directory is missing: {root}"
for directory in sorted(root.iterdir()):
    assert directory.is_dir(), f"Unexpected Snapshot entry: {directory}"
    sides = [side for side in ("left", "right") if (directory / side).is_file()]
    assert sides, f"Captured File has no sides: {directory}"
    print(directory.name, *sides)
PY
```

Inspect every immediate File pair before concluding that the complete patch was
reviewed. Use ordinary tools appropriate to the contents:

```sh
file "$DD_SNAPSHOT_PATH/<opaque-file-id>/left"
sed -n '1,240p' "$DD_SNAPSHOT_PATH/<opaque-file-id>/left"
diff -u \
  "$DD_SNAPSHOT_PATH/<opaque-file-id>/left" \
  "$DD_SNAPSHOT_PATH/<opaque-file-id>/right"
```

Those examples apply only when the named sides exist and the content is
suitable for the selected tool. For a missing side, compare the present side
with `/dev/null`. For binary or structured formats, use an appropriate
inspector rather than forcing a text read.

## Reviewer obligations

Read all captured File pairs and enough adjacent live-repository implementation
to judge the patch. The Snapshot proves captured before/after bytes; it does not
replace repository instructions, architecture documents, callers, or runtime
inspection.

Record a new finding against the exact inspected side path, normally the
`right` side for code present after the patch:

```sh
export DD_FILE="$DD_SNAPSHOT_PATH/<opaque-file-id>/right"
```

Do not edit, rename, delete, annotate, or generate files inside
`DD_SNAPSHOT_PATH`.
