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
    ...    # private format sidecars may also be present
  <opaque-file-id>/
    ...
```

Each immediate child directory represents one changed File pair. Its name is
an opaque backend-generated File id, not a repository path. `left` and
`right` contain the exact captured side bytes used by dirdiff and the browser.
Private link sidecars may sit beside them; they are composition data, not more
changed Files or valid finding paths. Enumerate and address only exact `left`
and `right` names.

- Both files present: inspect them as one modified or renamed/copied pair.
- Only `right` present: inspect it as an added or untracked File.
- Only `left` present: inspect it as a removed File.
- Never assume text, UTF-8, a particular extension, or a repository filename
  from the opaque directory name.

The public File ids and side names contain no manifest, ordering, display name,
or mapping to the outer repository File. Private link metadata may state its
captured nested paths, but it is not a general manifest and is not a finding
path. Use API Thread fields when an existing Thread supplies file/location
context. When creating a finding, pass the exact
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

## Bay keys

Every finding names a bay of a captured File plus a one-based inclusive line
range **local to that bay**. Both come from the captured bytes; there is no
API call that lists them.

An ordinary text File composes exactly one bay, `flatfile`, spanning the
whole File. Its bay lines are the File's own lines, so the numbers a normal
read gives you are already correct:

```sh
export DD_BAY_KEY='flatfile'
```

A captured `.ipynb` composes one bay per cell instead. The bay key is the
`id` that cell carries in the notebook, and the bay text is that cell's
`source` entries joined together — so line one of the bay is line one of the
cell, not of the JSON file. Print both before choosing a range:

```sh
python - <<'PY'
import json
import os
from pathlib import Path

document = json.loads(Path(os.environ["DD_FILE"]).read_text(encoding="utf-8"))
for cell in document["cells"]:
    source = cell["source"]
    text = source if isinstance(source, str) else "".join(source)
    print(f"bay_key={cell['id']}")
    for number, line in enumerate(text.splitlines(), start=1):
        print(f"{number:5d} {line}")
PY
```

A notebook also composes bays for cell outputs, cell metadata, and the
notebook's own metadata. Their text is rendered rather than stored, so the
captured bytes give you no line numbers for them. Do not address those keys.

A `.ipynb` whose JSON does not load is not a notebook, and neither is one whose
cells do not each carry a distinct `id`. Either reaches the same `flatfile`
terminal as any other text File, so address it by its own lines.

Passing a key the File does not compose is rejected, and so is a line past the
end of the bay. Neither is silently adjusted.

## Reviewer obligations

Read all captured File pairs and enough adjacent live-repository implementation
to judge the patch. The Snapshot proves captured before/after bytes; it does not
replace repository instructions, architecture documents, callers, or runtime
inspection.

Record a new finding against the exact inspected side path, normally the
`right` side for code present after the patch, together with the bay of that
File the finding addresses:

```sh
export DD_FILE="$DD_SNAPSHOT_PATH/<opaque-file-id>/right"
export DD_BAY_KEY='flatfile'
```

Do not edit, rename, delete, annotate, or generate files inside
`DD_SNAPSHOT_PATH`.
