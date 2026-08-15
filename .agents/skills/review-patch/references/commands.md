# Review-patch API commands

## Session variables

`DD_URL` comes from the running dirdiff backend. Set it before using this sheet;
do not assume a port. The repository path is derived from the current worktree,
which must already be marked in dirdiff. The UUID and Profile name are generated
for this disposable reviewer session.

```sh
: "${DD_URL:?Set DD_URL to the base URL printed by the running dirdiff backend}"

DD_REPO_PATH="$(git rev-parse --show-toplevel)"
DD_AGENT_UUID="$(uuidgen | tr -d '-' | tr '[:upper:]' '[:lower:]')"
DD_AGENT_NAME="Codex reviewer ${DD_AGENT_UUID:0:8}"
DD_PAGE=1
DD_LIMIT=20
```

`DD_PAGE` starts at the first page. `DD_LIMIT` is the requested full-Thread
page size; `20` is that endpoint's specified maximum, not a server address or
project-specific value.

## Join the HEAD/worktree review

This Tab compares `HEAD` with the current worktree, including untracked Files.
Use a different `AgentReviewTab` object when the requested Tab is refs, branch
review, or pull request.

```sh
DD_TAB="$(
  jq -n \
    --arg repo_path "$DD_REPO_PATH" \
    '{kind: "head", repo_path: $repo_path}'
)"

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
`DD_SNAPSHOT_PATH`. Set the line values to the one-based inclusive range the
finding addresses; they come from inspecting that captured File and are not
fixed API constants.

```sh
DD_FILE='paste-the-exact-captured-file-path'
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
    --argjson start_line "$DD_START_LINE" \
    --argjson end_line "$DD_END_LINE" \
    --arg body "$DD_BODY" \
    '{
      snapshot_id: $snapshot_id,
      profile_id: $profile_id,
      actions: [{
        kind: "create-finding",
        file: $file,
        region: {
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
