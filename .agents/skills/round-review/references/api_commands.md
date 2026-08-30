# Review-round API commands

Standard-library Python heredocs for the dirdiff agent API, holding only what a
round with a human reviewer needs. Values shown as placeholders are replaced
with API results.

Every example opens with `python3 - <<'PY'` — **the delimiter stays quoted**. An
unquoted `<<PY` lets the shell expand backticks and `$` in the body before Python
runs, which silently strips inline-code spans from any Comment that quotes code.
The HTTP call still succeeds, so nothing flags the corruption. This has already
happened once, to three posted findings.

Wrap `urlopen` in `try`/`except urllib.error.HTTPError` and print
`e.code`/`e.read().decode()` when debugging; an unwrapped call raises a
multi-frame traceback that hides the response body.

## Contents

- [Session variables](#session-variables)
- [Resume a retained connection](#resume-a-retained-connection)
- [Join a review](#join-a-review)
- [Capture the current work](#capture-the-current-work)
- [Read the author inbox](#read-the-author-inbox)
- [Inspect Snapshot evidence](#inspect-snapshot-evidence)
- [Post an author response](#post-an-author-response)
- [Mark a Thread without answering it](#mark-a-thread-without-answering-it)

## Session variables

```sh
: "${DD_URL:?Set DD_URL from onboarding or the identified running backend}"
export DD_URL
export DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
export DD_AGENT_NAME="AI author ${DD_AGENT_UUID%${DD_AGENT_UUID#????????}}"
```

With a human-supplied onboarding URL, load it and use its `dirdiff_url` and
complete `tab` unchanged. Without a link, ask what patch or Tab to review and
use the running backend address identified by the project or human. A retained
session keeps its existing values.

## Resume a retained connection

Reuse `DD_PROFILE_ID`, `DD_SNAPSHOT_ID`, `DD_SNAPSHOT_PATH`, and
`DD_LAST_ACTIVITY_ID` from this task's earlier round. Do not join again. Run
[Capture the current work](#capture-the-current-work), replace the retained
Snapshot values with what it returns, then read the author inbox.

Use the join command below only when the task has no retained session.

## Join a review

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
python3 - <<'PY'
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

Export what it returns:

```sh
export DD_PROFILE_ID='profile-id-from-join'
export DD_SNAPSHOT_ID='snapshot-id-from-join'
export DD_LAST_ACTIVITY_ID='last-activity-id-from-join'
export DD_SNAPSHOT_PATH='snapshot-path-from-join'
```

`DD_SNAPSHOT_PATH` is read-only evidence. Edit only the live worktree.

## Capture the current work

```sh
python3 - <<'PY'
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

The returned `snapshot_id` is commonly different from the one last quoted. That
is expected drift, not an error; replace the retained values with it.

`file_delta` lists `added`/`changed`/`removed` captured paths against the
previous Snapshot — use it to say what changed instead of recalling your own
edits. `thread_delta` is a **list**, not a mapping; iterating it as one raises
`AttributeError`.

The user's browser holds its own Snapshot. When their Threads are missing from
the Snapshot you hold, capture again and look at the newer one before concluding
anything is broken.

## Read the author inbox

`for=author` is the inbox of Threads waiting on you. Omit `for` to read every
open Thread, including ones already answered.

```sh
export DD_FOR='author'

python3 - <<'PY'
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

`page` and `limit` are required; omitting them returns HTTP 422. Later pages
require the first page's pivot, or the request fails with
`"Later review pages require the first page activity pivot."`:

```sh
export DD_THROUGH_ACTIVITY_ID='through-activity-id-from-first-page'
export DD_AUTHOR_THREAD_ID='thread-id-from-author-inbox'
```

Read the **whole** Thread, not its first Comment. A Thread whose opening Comment
looks like noise can carry a real finding underneath it — that has already
happened, and one such Thread held the round's worst regression.

## Inspect Snapshot evidence

Read [snapshot_structure.md](snapshot_structure.md) first: Snapshot child names
are opaque File ids, each holding an exact `left` and/or `right` side, and the
directory name is not a repository path.

```sh
python3 - <<'PY'
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

The Snapshot is evidence. Every change goes in the live worktree.

## Post an author response

`author-response` requires the Thread to be `open` with `attention` in
`{author, both}`. A Thread you already answered sits at `attention = reviewer`
until the user acts again, and a second response fails with `state_conflict`
("... is not valid for this Thread outcome"). Re-read the inbox rather than
reusing a remembered one.

```sh
python3 - <<'PY'
import json
import os
import urllib.request

body = """What I checked, what I found, and what I propose.

Multiline bodies are safe here because the heredoc delimiter is quoted."""
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

Batch independent responses by adding action objects to the same `actions`
list; the complete list applies atomically.

## Mark a Thread without answering it

`inert-comment` preserves lifecycle and attention. Use it to annotate a Thread
that is not a finding — a stray artefact, a note about what a Thread actually
contains. It must never stand in for an author response to a real finding.

Same payload as above with `"kind": "inert-comment"`.
