# Review-patch API commands

## Session variables

For a new session with a human-supplied onboarding URL, load it, set `DD_URL`
from `dirdiff_url`, and set `DD_TAB` to its complete `tab` JSON unchanged.
Without a link, ask what patch or Tab to review, set `DD_URL` to the running
backend address identified by the project or human, and build `DD_TAB` from the
confirmed selection below. The UUID and Profile name are disposable.

```sh
: "${DD_URL:?Set DD_URL from onboarding or the identified running backend}"

DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
DD_AGENT_NAME="AI reviewer ${DD_AGENT_UUID:0:8}"
DD_PAGE=1
DD_LIMIT=20
```

`DD_PAGE` starts at the first page. `DD_LIMIT` is the requested full-Thread
page size; `20` is that endpoint's specified maximum, not a server address or
project-specific value. Requesting a larger `limit` returns
`HTTP 422 Unprocessable Content`; do not raise it to cut down round trips.

## Select a Tab without an onboarding link

Set `DD_TAB` to exactly one shape matching the human-confirmed review. Every
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

Inspect `DD_SNAPSHOT_PATH` as read-only evidence.

## Enumerate and inspect the Snapshot

Read [snapshot_structure.md](snapshot_structure.md) before reviewing. It
explains why the complete capture must be inspected, what each opaque pair
directory represents, and how an exact captured side becomes a finding target.

Enumerate every captured side:

```sh
find "$DD_SNAPSHOT_PATH" \
  -mindepth 2 \
  -maxdepth 2 \
  -type f \
  \( -name left -o -name right \) \
  -print
```

For each immediate pair directory, inspect every present side with a
content-appropriate tool. For ordinary text pairs:

```sh
DD_PAIR_DIR="$DD_SNAPSHOT_PATH/paste-opaque-file-id"

find "$DD_PAIR_DIR" -mindepth 1 -maxdepth 1 -type f \
  \( -name left -o -name right \) -print
file "$DD_PAIR_DIR"/left "$DD_PAIR_DIR"/right 2>/dev/null
diff -u "$DD_PAIR_DIR"/left "$DD_PAIR_DIR"/right
```

Use `/dev/null` when one side is absent. Do not force binary, notebook, or
other structured data through text commands. Set `DD_FILE` to the exact
absolute captured side you inspected when creating a finding.

## Read active context

Read all active Threads before reviewing:

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

Reuse the returned boundary on every later page:

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

## Create a finding

Set `DD_FILE` to the exact absolute captured File path under
`DD_SNAPSHOT_PATH`. Set `DD_BAY_KEY` to the bay of that File the finding
addresses, and the line values to the one-based inclusive range *within that
bay*. All three come from inspecting the captured File and are not fixed API
constants. `Bay keys` in `snapshot_structure.md` says how to read a key and
its lines out of the captured bytes; an ordinary text File is `flatfile` with
its own line numbers.

```sh
DD_FILE='paste-the-exact-captured-file-path'
DD_BAY_KEY='paste-the-bay-key-of-that-file'
DD_START_LINE='paste-the-first-selected-line-number'
DD_END_LINE='paste-the-last-selected-line-number'

# Replace this example with the actual finding.
DD_BODY="$(cat <<'BODY'
This repeats the database query once per item, so the patch introduces linear
query growth on the affected path.
BODY
)"

DD_ACTION_RESPONSE="$(
  jq -n \
    --arg snapshot_id "$DD_SNAPSHOT_ID" \
    --argjson profile_id "$DD_PROFILE_ID" \
    --arg file "$DD_FILE" \
    --arg bay_key "$DD_BAY_KEY" \
    --argjson start_line "$DD_START_LINE" \
    --argjson end_line "$DD_END_LINE" \
    --arg body "$DD_BODY" \
    '{
      snapshot_id: $snapshot_id,
      profile_id: $profile_id,
      actions: [{
        kind: "create-finding",
        file: $file,
        bay: {
          bay_key: $bay_key,
          start_line: $start_line,
          end_line: $end_line
        },
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

DD_CREATED_THREAD_ID="$(jq -r '.results[0].thread_id' <<<"$DD_ACTION_RESPONSE")"
```

The action creates an `open` Thread with `author` attention.

## Continue onto the revised Snapshot

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

The returned `snapshot_id` commonly differs from any Snapshot id named in
an earlier handoff message — the branch moves between rounds, and this is
expected drift, not an error.

`DD_CONTINUE_RESPONSE` also carries `file_delta` (`added`/`changed`/`removed`
captured paths that differ from the previous Snapshot) and `thread_delta`
(authored Thread activity since the prior `last_activity_id`, bounded by
`limit` and flagged incomplete by `has_more_thread_changes`). Read them
before re-enumerating the whole Snapshot or re-diffing a full Thread page:

```sh
jq '{file_delta, unresolved_thread_count, has_more_thread_changes}' \
  <<<"$DD_CONTINUE_RESPONSE"
```

If `has_more_thread_changes` is `true`, call `continue_review` again with the
latest `last_activity_id` before assuming you have seen everything.

## Read the reviewer inbox

```sh
DD_PAGE=1

DD_REVIEWER_THREADS="$(curl \
  --fail-with-body \
  --silent \
  --show-error \
  --get \
  --data-urlencode "snapshot_id=$DD_SNAPSHOT_ID" \
  --data-urlencode 'for=reviewer' \
  --data-urlencode "page=$DD_PAGE" \
  --data-urlencode "limit=$DD_LIMIT" \
  "$DD_URL/api/agent/threads")"
```

This returns open Threads whose `attention_after` is `reviewer` or `both`.

## Return a finding

`reviewer-return` and `reviewer-resolve` both require the target Thread to be
`open` with `attention` in `{reviewer, both}`. Posting either one twice in a
row on the same Thread, without an intervening `author-response`, fails with
`state_conflict` ("... is not valid for this Thread outcome") because the
first post already moved `attention` to `author` (or closed the Thread).
Select `DD_REVIEW_THREAD_ID` from a fresh reviewer-inbox read, not a
remembered id.

```sh
# List the returned Thread ids and select the one being returned.
jq -r '.items[].thread_id' <<<"$DD_REVIEWER_THREADS"

# Set DD_REVIEW_THREAD_ID to the exact selected `.items[].thread_id`, then
# continue. Do not reuse DD_CREATED_THREAD_ID: it identifies a finding created
# earlier, not necessarily the Thread currently returned to the reviewer.
: "${DD_REVIEW_THREAD_ID:?Set DD_REVIEW_THREAD_ID from DD_REVIEWER_THREADS before posting}"

# Replace this example with the concrete remaining objection.
DD_BODY="$(cat <<'BODY'
The original issue is fixed, but the revised path drops items with no metadata.
That changes existing behavior and remains a correctness regression.
BODY
)"

jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg thread_id "$DD_REVIEW_THREAD_ID" \
  --arg body "$DD_BODY" \
  '{
    snapshot_id: $snapshot_id,
    profile_id: $profile_id,
    actions: [{
      kind: "reviewer-return",
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

The action preserves `open` and sets attention to `author`.

## Resolve a verified finding

Use only when the human authorized resolution:

```sh
# Keep DD_REVIEW_THREAD_ID set to the exact verified Thread from the reviewer
# inbox.
: "${DD_REVIEW_THREAD_ID:?Set DD_REVIEW_THREAD_ID from DD_REVIEWER_THREADS before resolving}"

# Replace this example with the verification supporting resolution.
DD_BODY="$(cat <<'BODY'
Verified in the revised Snapshot. The fix is simple, removes the repeated work,
preserves the affected behavior, and introduces no related regression.
BODY
)"

jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg thread_id "$DD_REVIEW_THREAD_ID" \
  --arg body "$DD_BODY" \
  '{
    snapshot_id: $snapshot_id,
    profile_id: $profile_id,
    actions: [{
      kind: "reviewer-resolve",
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

The action sets status to `resolved` and attention to `none`.

## Post an inert Comment

```sh
# Keep DD_REVIEW_THREAD_ID set to the exact Thread receiving context only.
: "${DD_REVIEW_THREAD_ID:?Set DD_REVIEW_THREAD_ID from DD_REVIEWER_THREADS before commenting}"

# Replace this example with the actual neutral Comment.
DD_BODY="$(cat <<'BODY'
Additional review context only; no workflow transition intended.
BODY
)"

jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg thread_id "$DD_REVIEW_THREAD_ID" \
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

## Delete an explicitly identified Thread

Use only when the human explicitly requested deletion of this Thread:

```sh
# Set DD_REVIEW_THREAD_ID to the exact Thread named by the human.
: "${DD_REVIEW_THREAD_ID:?Set DD_REVIEW_THREAD_ID to the Thread explicitly named by the human}"

jq -n \
  --arg snapshot_id "$DD_SNAPSHOT_ID" \
  --argjson profile_id "$DD_PROFILE_ID" \
  --arg thread_id "$DD_REVIEW_THREAD_ID" \
  '{
    snapshot_id: $snapshot_id,
    profile_id: $profile_id,
    actions: [{
      kind: "reviewer-delete",
      thread_id: $thread_id
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

## Batch findings or decisions

Put multiple action objects in one `actions` array. The complete array is
validated first and committed atomically. Do not write multiline bodies as
single-quoted strings containing `\n`.
