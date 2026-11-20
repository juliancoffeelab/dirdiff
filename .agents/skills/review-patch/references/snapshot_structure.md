# Captured Snapshot

`init` and `capture` print a read-only Snapshot directory and a manifest:

```text
Snapshot directory (read-only): <snapshot_path>
Changed files (2):
- modify src/app.py
    left:  <snapshot_path>/<file-id>/left
    right: <snapshot_path>/<file-id>/right
- add notes.txt
    right: <snapshot_path>/<file-id>/right
```

Each entry is a change kind (`modify`, `add`, `delete`, `rename`, `copy`), the
repository path (`old -> new` for rename and copy), and the path of each
captured side. The manifest is the only mapping from repository path to bytes;
`<file-id>` is opaque. Only `right` means added, only `left` means removed.

- Never write into the Snapshot directory. Changes belong in the live worktree.
- A side marked `capture error (bytes are a placeholder)` is not source; report
  the error instead of reviewing it.
- Git LFS sides hold the real payload, not the pointer. Do not assume text or
  UTF-8; use tools suited to the content.
- Files named `*-link.json` or `*-target` beside a side are internal, not
  changed files.

## Addressing a range

`find` takes a repository path, `--side left|right`, a bay key, and an
inclusive one-based `--lines A-B` local to the bay. The server rejects an
unknown bay or a line past its end.

- Ordinary text: one bay, `flatfile`; lines are the file's lines.
- Notebook (`.ipynb`): one bay per cell, keyed by the cell's `id`; lines are
  lines of the cell's joined `source`, not of the JSON. List them:

  ```sh
  python3 - <<'PY'
  import json
  from pathlib import Path

  document = json.loads(Path("RIGHT_SIDE_PATH").read_text(encoding="utf-8"))
  for cell in document["cells"]:
      source = cell["source"]
      text = source if isinstance(source, str) else "".join(source)
      print(f"bay_key={cell['id']}")
      for number, line in enumerate(text.splitlines(), start=1):
          print(f"{number:5d} {line}")
  PY
  ```

  Cell outputs and metadata are rendered text with no line numbers in the
  bytes; use `--review` for those. A notebook that does not parse, or whose
  cells lack distinct ids, is plain text: `flatfile`.
- `image` or `blob` file: one bay of that name; use `--lines 1-1`.
