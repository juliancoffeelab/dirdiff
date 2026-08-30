# Babysit-patch API commands

## Session variables

For a new session with a human-supplied onboarding URL, load it, set `DD_URL`
from `dirdiff_url`, and set `DD_TAB` to its complete `tab` JSON unchanged.
Without a link, ask what patch or Tab to work on, set `DD_URL` to the running
backend address identified by the project or human, and build `DD_TAB` from the
confirmed selection below. The UUID and Profile name are disposable.

```sh
: "${DD_URL:?Set DD_URL from onboarding or the identified running backend}"

DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
DD_AGENT_NAME="AI author ${DD_AGENT_UUID:0:8}"
DD_PAGE=1
DD_LIMIT=20
```

`DD_PAGE` starts at the first page. `DD_LIMIT` is the requested full-Thread
page size; `20` is that endpoint's specified maximum, not a server address or
project-specific value. Requesting a larger `limit` returns
`HTTP 422 Unprocessable Content`; do not raise it to cut down round trips.

## Select a Tab without an onboarding link

Set `DD_TAB` to exactly one shape matching the human-confirmed work. Every
repository path is the absolute path of an active dirdiff Mark.

```json
{"kind": "head", "repo_path": "/absolute/path/to/marked/repository"}
{"kind": "refs", "repo_path": "/absolute/path/to/marked/repository", "left": "main", "right": "HEAD"}
{"kind": "branch-review", "repo_path": "/absolute/path/to/marked/repository", "base": {"remote": null, "name": "main"}, "review": {"remote": null, "name": "feature/topic"}}
{"kind": "pull-request", "url": "https://github.com/owner/repository/pull/123"}
```

For Branch Review, `remote` is `null` for a local branch or the exact remote
name for a remote branch. The human's answer selects the shape and values; none
of the examples is a default.

## Join the selected review

```sh
: "${DD_TAB:?Set DD_TAB from onboarding or the confirmed manual selection}"

DD_JOIN_RESPONSE="$(
  jq -n \
    --arg agent_uuid "$DD_AGENT_UUID" \
    --arg name "$DD_AGENT_NAME" \
    --argjson tab "$DD_TAB" \
    '{
      agent_uuid: $agent_uuid,
      name: $name,
      tab: $tab
    }' |
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$DD_URL/api/agent/join_review")"

DD_PROFILE_ID="$(jq -r '.profile_id' <<<"$DD_JOIN_RESPONSE")"
DD_SNAPSHOT_ID="$(jq -r '.snapshot_id' <<<"$DD_JOIN_RESPONSE")"
DD_SNAPSHOT_PATH="$(jq -r '.snapshot_path' <<<"$DD_JOIN_RESPONSE")"
DD_LAST_ACTIVITY_ID="$(jq -r '.last_activity_id' <<<"$DD_JOIN_RESPONSE")"
```

Every variable below this point is taken from that response. Replace
`DD_SNAPSHOT_ID`, `DD_SNAPSHOT_PATH`, and `DD_LAST_ACTIVITY_ID` only with values
returned by later API calls.

`DD_SNAPSHOT_PATH` is read-only evidence. Edit only the live worktree.

## Read all active context

Omit `for` to see every open Thread before implementing:

```sh
DD_THREADS_RESPONSE="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --get \
  --data-urlencode "snapshot_id=$DD_SNAPSHOT_ID" \
  --data-urlencode "page=$DD_PAGE" \
  --data-urlencode "limit=$DD_LIMIT" \
  "$DD_URL/api/agent/threads")"

DD_THROUGH_ACTIVITY_ID="$(jq -r '.through_activity_id' <<<"$DD_THREADS_RESPONSE")"
```

Reuse `DD_THROUGH_ACTIVITY_ID` on every later page from this read:

```sh
DD_PAGE=$((DD_PAGE + 1))

curl \
  --fail-with-body \
  --silent \
  --show-error \
  --get \
  --data-urlencode "snapshot_id=$DD_SNAPSHOT_ID" \
  --data-urlencode "through_activity_id=$DD_THROUGH_ACTIVITY_ID" \
  --data-urlencode "page=$DD_PAGE" \
  --data-urlencode "limit=$DD_LIMIT" \
  "$DD_URL/api/agent/threads"
```

## Capture the revised patch

```sh
DD_CONTINUE_RESPONSE="$(
  jq -n \
    --arg snapshot_id "$DD_SNAPSHOT_ID" \
    --argjson last_activity_id "$DD_LAST_ACTIVITY_ID" \
    '{
      snapshot_id: $snapshot_id,
      last_activity_id: $last_activity_id
    }' |
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$DD_URL/api/agent/continue_review")"

DD_SNAPSHOT_ID="$(jq -r '.snapshot_id' <<<"$DD_CONTINUE_RESPONSE")"
DD_SNAPSHOT_PATH="$(jq -r '.snapshot_path' <<<"$DD_CONTINUE_RESPONSE")"
DD_LAST_ACTIVITY_ID="$(jq -r '.last_activity_id' <<<"$DD_CONTINUE_RESPONSE")"
```

The returned `snapshot_id` commonly differs from any Snapshot id you last
quoted to a reviewer — the branch moves between rounds, and this is expected
drift, not an error; always brief the reviewer with the fresh value.

`DD_CONTINUE_RESPONSE` also carries `file_delta` (`added`/`changed`/`removed`
captured paths versus the previous Snapshot) and `thread_delta` (authored
Thread activity since the prior `last_activity_id`, bounded by `limit` and
flagged incomplete by `has_more_thread_changes`):

```sh
jq '{file_delta, unresolved_thread_count, has_more_thread_changes}' \
  <<<"$DD_CONTINUE_RESPONSE"
```

Point a resumed reviewer at `file_delta` directly instead of leaving it to
re-enumerate the whole Snapshot.

## Read the author inbox

```sh
DD_PAGE=1

DD_AUTHOR_THREADS="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --get \
  --data-urlencode "snapshot_id=$DD_SNAPSHOT_ID" \
  --data-urlencode 'for=author' \
  --data-urlencode "page=$DD_PAGE" \
  --data-urlencode "limit=$DD_LIMIT" \
  "$DD_URL/api/agent/threads")"
```

This returns open Threads whose `attention_after` is `author` or `both`.

## Inspect Snapshot evidence

Read [snapshot_structure.md](snapshot_structure.md) before using the captured
filesystem. It explains why an implementor uses the Snapshot, what the opaque
pair directories mean, and why changes belong only in the live worktree.

Select a Thread id from the author inbox. Its `file` is the exact captured side
behind the finding:

```sh
jq -r '.items[].thread_id' <<<"$DD_AUTHOR_THREADS"
: "${DD_AUTHOR_THREAD_ID:?Set DD_AUTHOR_THREAD_ID from DD_AUTHOR_THREADS}"

DD_FILE="$(jq -r \
  --arg thread_id "$DD_AUTHOR_THREAD_ID" \
  '.items[] | select(.thread_id == $thread_id) | .file // empty' \
  <<<"$DD_AUTHOR_THREADS")"

: "${DD_FILE:?Selected Thread has no current captured File path}"
file "$DD_FILE"
sed -n '1,240p' "$DD_FILE"

DD_PAIR_DIR="$(dirname "$DD_FILE")"
find "$DD_PAIR_DIR" -mindepth 1 -maxdepth 1 -type f \
  \( -name left -o -name right \) -print
```

Use a content-appropriate reader instead of `sed` for binary or structured
data. The sibling `left` and `right` files are the captured before/after pair;
the opaque directory name is not a repository path.

## Post an author response

`author-response` requires the target Thread to be `open` with `attention`
in `{author, both}`. A Thread you already answered has `attention =
reviewer` until the reviewer acts again; posting a second `author-response`
to it fails with `state_conflict` ("... is not valid for this Thread
outcome"). Select `DD_AUTHOR_THREAD_ID` from a fresh `DD_AUTHOR_THREADS` read,
not a remembered inbox, if there is any chance the Thread already moved.

Use a quoted heredoc for actual multiline text:

```sh
# List the returned Thread ids and select the one being answered.
jq -r '.items[].thread_id' <<<"$DD_AUTHOR_THREADS"

# Set DD_AUTHOR_THREAD_ID to the exact selected `.items[].thread_id`, then
# continue.
: "${DD_AUTHOR_THREAD_ID:?Set DD_AUTHOR_THREAD_ID from DD_AUTHOR_THREADS before posting}"

# Replace this example with the actual response to that Thread.
DD_BODY="$(cat <<'BODY'
Implemented the requested correction.

The revised patch preserves the affected contract.
BODY
)"

DD_ACTION_RESPONSE="$(
  jq -n \
    --arg snapshot_id "$DD_SNAPSHOT_ID" \
    --argjson profile_id "$DD_PROFILE_ID" \
    --arg thread_id "$DD_AUTHOR_THREAD_ID" \
    --arg body "$DD_BODY" \
    '{
      snapshot_id: $snapshot_id,
      profile_id: $profile_id,
      actions: [{
        kind: "author-response",
        thread_id: $thread_id,
        body: $body
      }]
    }' |
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$DD_URL/api/agent/actions")"
```

The action preserves `open` and sets attention to `reviewer`.

## Post an inert Comment

Use this only when the Comment must not transfer attention:

```sh
# Select the exact target from DD_AUTHOR_THREADS if this section is used alone.
: "${DD_AUTHOR_THREAD_ID:?Set DD_AUTHOR_THREAD_ID from DD_AUTHOR_THREADS before posting}"

DD_BODY="$(cat <<'BODY'
Additional context only; no workflow transition intended.
BODY
)"

jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg thread_id "$DD_AUTHOR_THREAD_ID" \
  --arg body "$DD_BODY" \
  '{
    snapshot_id: $snapshot_id,
    profile_id: $profile_id,
    actions: [{
      kind: "inert-comment",
      thread_id: $thread_id,
      body: $body
    }]
  }' |
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$DD_URL/api/agent/actions"
```

## Batch author responses

Put multiple action objects in one `actions` array. The complete array is
validated first and committed atomically. Set each Thread id from the author
inbox and each body from a quoted heredoc before running the command:

```sh
jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg first_thread "$DD_FIRST_THREAD_ID" \
  --arg first_body "$DD_FIRST_BODY" \
  --arg second_thread "$DD_SECOND_THREAD_ID" \
  --arg second_body "$DD_SECOND_BODY" \
  '{
    snapshot_id: $snapshot_id,
    profile_id: $profile_id,
    actions: [
      {
        kind: "author-response",
        thread_id: $first_thread,
        body: $first_body
      },
      {
        kind: "author-response",
        thread_id: $second_thread,
        body: $second_body
      }
    ]
  }' |
curl \
  --fail-with-body \
  --silent \
  --show-error \
  --request POST \
  --header 'Content-Type: application/json' \
  --data-binary @- \
  "$DD_URL/api/agent/actions"
```

Do not write multiline bodies as single-quoted strings containing `\n`.
