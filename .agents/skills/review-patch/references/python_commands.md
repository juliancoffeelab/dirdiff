# Review-patch Python commands

Prefer these standard-library Python heredocs for dirdiff HTTP calls. They
serialize multiline strings as real JSON newlines without shell escaping.
Commands read explicit `DD_*` environment variables; values shown as
placeholders must be replaced with API results or selected inbox values.

## Contents

- [Session variables](#session-variables)
- [Join the HEAD/worktree review](#join-the-headworktree-review)
- [Enumerate the captured Snapshot](#enumerate-the-captured-snapshot)
- [Read active or reviewer Threads](#read-active-or-reviewer-threads)
- [Create a finding](#create-a-finding)
- [Continue onto the revised Snapshot](#continue-onto-the-revised-snapshot)
- [Return or resolve a finding](#return-or-resolve-a-finding)

## Session variables

```sh
: "${DD_URL:?Set DD_URL to the base URL printed by the running dirdiff backend}"
export DD_URL
export DD_REPO_PATH="$(git rev-parse --show-toplevel)"
export DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
export DD_AGENT_NAME="AI reviewer ${DD_AGENT_UUID%${DD_AGENT_UUID#????????}}"
```

`DD_URL` must be the address printed by the running dirdiff backend. The
repository must already be marked.

## Join the HEAD/worktree review

```sh
python - <<'PY'
import json
import os
import urllib.request

payload = {
    "agent_uuid": os.environ["DD_AGENT_UUID"],
    "name": os.environ["DD_AGENT_NAME"],
    "tab": {"kind": "head", "repo_path": os.environ["DD_REPO_PATH"]},
}
request = urllib.request.Request(
    f'{os.environ["DD_URL"]}/api/agent/join_review',
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

The `tab` object selects the Tab kind. `{"kind": "head", ...}` above compares
`HEAD` with the current worktree. The other kinds accepted by `join_review`:

```python
# Refs: two explicit sides of one marked repository.
{"kind": "refs", "repo_path": REPO_PATH, "left": "main", "right": "HEAD"}

# Branch review: one symbolic base and review branch pair.
# remote is None for a local branch, or the remote name such as "origin".
{
    "kind": "branch-review",
    "repo_path": REPO_PATH,
    "base": {"remote": None, "name": "main"},
    "review": {"remote": None, "name": "feature/topic"},
}

# Pull request: one supported Pull Request URL; takes no repo_path.
{"kind": "pull-request", "url": "https://github.com/owner/repo/pull/123"}
```

Export the returned values before later commands:

```sh
export DD_PROFILE_ID='profile-id-from-join'
export DD_SNAPSHOT_ID='snapshot-id-from-join'
export DD_LAST_ACTIVITY_ID='last-activity-id-from-join'
export DD_SNAPSHOT_PATH='snapshot-path-from-join'
```

Treat `DD_SNAPSHOT_PATH` as read-only evidence.

## Enumerate the captured Snapshot

Read [snapshot_structure.md](snapshot_structure.md) first. It explains why a
reviewer must inspect the complete capture, what opaque File-pair directories
mean, and why findings use exact captured side paths.

Enumerate every pair and present side without inferring repository paths from
opaque directory names:

```sh
python - <<'PY'
import os
from pathlib import Path

root = Path(os.environ["DD_SNAPSHOT_PATH"])
assert root.is_dir(), f"Snapshot directory is missing: {root}"
for directory in sorted(root.iterdir()):
    assert directory.is_dir(), f"Unexpected Snapshot entry: {directory}"
    sides = [directory / name for name in ("left", "right") if (directory / name).is_file()]
    assert sides, f"Captured File has no sides: {directory}"
    print(directory.name)
    for side in sides:
        print(f"  {side}")
PY
```

Inspect every printed side with content-appropriate tools. Both sides form a
captured before/after pair; one side means an addition or removal. Export the
exact inspected path as `DD_FILE` before creating a finding.

## Read active or reviewer Threads

Omit `for` for initial context. Set it to `reviewer` for the reviewer inbox.

```sh
export DD_FOR='' # Replace with reviewer for the reviewer inbox.

python - <<'PY'
import json
import os
import urllib.parse
import urllib.request

parameters = {
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "page": 1,
    "limit": 20,
}
if os.environ.get("DD_FOR"):
    parameters["for"] = os.environ["DD_FOR"]
if os.environ.get("DD_THROUGH_ACTIVITY_ID"):
    parameters["through_activity_id"] = os.environ["DD_THROUGH_ACTIVITY_ID"]
url = f'{os.environ["DD_URL"]}/api/agent/threads?{urllib.parse.urlencode(parameters)}'
with urllib.request.urlopen(url) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

Export the returned boundary and selected reviewer-inbox Thread:

```sh
export DD_THROUGH_ACTIVITY_ID='through-activity-id-from-first-page'
export DD_REVIEW_THREAD_ID='thread-id-from-reviewer-inbox'
```

Repeat the boundary for every later page in the same read.

## Create a finding

Set `DD_FILE` to an exact absolute File under the captured Snapshot path.

```sh
export DD_FILE='exact-captured-file-path'
export DD_START_LINE='first-selected-line'
export DD_END_LINE='last-selected-line'

python - <<'PY'
import json
import os
import urllib.request

body = """This repeats the database query once per item, so the patch introduces
linear query growth on the affected path."""
payload = {
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "profile_id": int(os.environ["DD_PROFILE_ID"]),
    "actions": [{
        "kind": "create-finding",
        "file": os.environ["DD_FILE"],
        "region": {
            "start_line": int(os.environ["DD_START_LINE"]),
            "end_line": int(os.environ["DD_END_LINE"]),
        },
        "body": body,
    }],
}
request = urllib.request.Request(
    f'{os.environ["DD_URL"]}/api/agent/actions',
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

## Continue onto the revised Snapshot

```sh
python - <<'PY'
import json
import os
import urllib.request

payload = {
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "last_activity_id": int(os.environ["DD_LAST_ACTIVITY_ID"]),
}
request = urllib.request.Request(
    f'{os.environ["DD_URL"]}/api/agent/continue_review',
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

Replace the Snapshot path, Snapshot id, and last activity id with the returned
values.

## Return or resolve a finding

Set `kind` to `reviewer-return` when an objection remains. Use
`reviewer-resolve` only with human authorization and concrete verification.

```sh
export DD_REVIEW_ACTION='reviewer-return'

python - <<'PY'
import json
import os
import urllib.request

body = """The original issue is improved, but this path still drops items with
no metadata. That remains a correctness regression."""
payload = {
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "profile_id": int(os.environ["DD_PROFILE_ID"]),
    "actions": [{
        "kind": os.environ["DD_REVIEW_ACTION"],
        "thread_id": os.environ["DD_REVIEW_THREAD_ID"],
        "body": body,
    }],
}
request = urllib.request.Request(
    f'{os.environ["DD_URL"]}/api/agent/actions',
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), indent=2))
PY
```

Use `inert-comment` only to preserve lifecycle and attention. Use
`reviewer-delete` only when the human explicitly authorizes deletion; that
action omits `body`. Batch independent actions in one `actions` list.
