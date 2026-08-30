# Babysit-patch Python commands

Prefer these standard-library Python heredocs for dirdiff HTTP calls. They
serialize multiline strings as real JSON newlines without shell escaping.
Commands read explicit `DD_*` environment variables; values shown as
placeholders must be replaced with API results or selected inbox values.

Every example below opens with `python3 - <<'PY'` — the delimiter is quoted.
Keep it quoted. An unquoted `<<PY` lets the shell expand backticks and `$` in
the heredoc body before Python runs, which silently corrupts any Comment
text that quotes code (a real incident dropped inline-code spans from three
posted findings, with no HTTP error to signal it).

Wrap `urlopen` in `try`/`except urllib.error.HTTPError` and print
`e.code`/`e.read().decode()` when debugging a failure; an unwrapped call
raises a multi-frame traceback that hides the response body.

## Contents

- [Session variables](#session-variables)
- [Resume a retained review](#resume-a-retained-review)
- [Join the selected review](#join-the-selected-review)
- [Read active Threads](#read-active-threads)
- [Capture the revised patch](#capture-the-revised-patch)
- [Inspect Snapshot evidence](#inspect-snapshot-evidence)
- [Post an author response](#post-an-author-response)

## Session variables

```sh
: "${DD_URL:?Set DD_URL from onboarding or the identified running backend}"
export DD_URL
export DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
export DD_AGENT_NAME="AI author ${DD_AGENT_UUID%${DD_AGENT_UUID#????????}}"
```

With a human-supplied onboarding URL, load it and use its `dirdiff_url` and
complete `tab` unchanged. Without a link, ask what patch or Tab to work on and
use the running backend address identified by the project or human.

## Resume a retained review

Reuse `DD_PROFILE_ID`, `DD_SNAPSHOT_ID`, `DD_SNAPSHOT_PATH`, and
`DD_LAST_ACTIVITY_ID` from the current task's preceding round. Do not join
again. Run [Capture the revised patch](#capture-the-revised-patch), replace the
retained Snapshot values with its response, and then read active Threads.

Use the join command below only when the current task has no retained API
session.

## Join the selected review

Set `DD_TAB` from the onboarding response or to one object matching the human's
confirmed selection:

```json
{"kind": "head", "repo_path": "/absolute/path/to/marked/repository"}
{"kind": "refs", "repo_path": "/absolute/path/to/marked/repository", "left": "main", "right": "HEAD"}
{"kind": "branch-review", "repo_path": "/absolute/path/to/marked/repository", "base": {"remote": null, "name": "main"}, "review": {"remote": null, "name": "feature/topic"}}
{"kind": "pull-request", "url": "https://github.com/owner/repository/pull/123"}
```

Repository paths identify active Marks. Branch Review uses `null` for a local
branch remote or its exact remote name. None of these values is a default.

```sh
export DD_TAB='compact-tab-json-from-onboarding-or-confirmed-selection'
```

```sh
python - <<'PY'
import json
import os
import urllib.request

payload = {
    "agent_uuid": os.environ["DD_AGENT_UUID"],
    "name": os.environ["DD_AGENT_NAME"],
    "tab": json.loads(os.environ["DD_TAB"]),
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

Export the returned values before later commands:

```sh
export DD_PROFILE_ID='profile-id-from-join'
export DD_SNAPSHOT_ID='snapshot-id-from-join'
export DD_LAST_ACTIVITY_ID='last-activity-id-from-join'
export DD_SNAPSHOT_PATH='snapshot-path-from-join'
```

Treat `DD_SNAPSHOT_PATH` as read-only evidence. Edit only the live worktree.

## Read active Threads

Omit `for` to read all open Threads. Set it to `author` for the author inbox.

```sh
export DD_FOR='' # Replace with author for the author inbox.

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

Export the returned `through_activity_id` and repeat it for every later page in
the same read. Select an exact author-inbox Thread before responding:

```sh
export DD_THROUGH_ACTIVITY_ID='through-activity-id-from-first-page'
export DD_AUTHOR_THREAD_ID='thread-id-from-author-inbox'
```

## Capture the revised patch

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
values. The returned `snapshot_id` commonly differs from any Snapshot id you
last quoted to a reviewer — the branch moves between rounds, and this is
expected drift, not an error; always brief the reviewer with the fresh value.

The response also carries `file_delta` (`added`/`changed`/`removed` captured
paths versus the previous Snapshot) and `thread_delta` (authored Thread
activity since `last_activity_id`, bounded by `limit` and flagged incomplete
by `has_more_thread_changes`). Use `file_delta` to point a resumed reviewer
at exactly what changed instead of leaving it to re-enumerate the whole
Snapshot.

## Inspect Snapshot evidence

Read [snapshot_structure.md](snapshot_structure.md) first. It explains why the
implementor reads captured evidence, how opaque File-pair directories are
structured, and why the live worktree is the only edit destination.

After selecting `DD_AUTHOR_THREAD_ID` from the author inbox, fetch that exact
Thread and print its captured side and available pair:

```sh
python - <<'PY'
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path

thread_id = os.environ["DD_AUTHOR_THREAD_ID"]
parameters = urllib.parse.urlencode({
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "page": 1,
    "limit": 1,
})
url = f'{os.environ["DD_URL"]}/api/agent/thread/{thread_id}?{parameters}'
with urllib.request.urlopen(url) as response:
    thread = json.load(response)
assert thread["file"] is not None, "Selected Thread has no current captured File path"

side = Path(thread["file"])
assert side.is_file(), f"Captured side is missing: {side}"
print(side)
for candidate in (side.parent / "left", side.parent / "right"):
    if candidate.is_file():
        print(candidate)
PY
```

Read the printed files with content-appropriate tools. `left` and `right` are
captured before/after bytes; the parent directory name is not a repository
path.

## Post an author response

`author-response` requires the target Thread to be `open` with `attention`
in `{author, both}`. A Thread you already answered has `attention =
reviewer` until the reviewer acts again; posting a second `author-response`
to it fails with `state_conflict` ("... is not valid for this Thread
outcome"). Select `DD_AUTHOR_THREAD_ID` from a fresh
`/api/agent/threads?for=author` read, not a remembered inbox, if there is any
chance the Thread already moved.

The triple-quoted Python string contains real newlines:

```sh
python - <<'PY'
import json
import os
import urllib.request

body = """Implemented the requested correction.

The revised patch preserves the affected contract."""
payload = {
    "snapshot_id": os.environ["DD_SNAPSHOT_ID"],
    "profile_id": int(os.environ["DD_PROFILE_ID"]),
    "actions": [{
        "kind": "author-response",
        "thread_id": os.environ["DD_AUTHOR_THREAD_ID"],
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

Use `inert-comment` instead only when the Comment must preserve attention.
Batch independent responses by adding action objects to the same `actions`
list; the complete list is applied atomically.
