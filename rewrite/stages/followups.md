# Explicit follow-ups after the frontend rewrite

This file records work deliberately kept outside practical rewrite Chapters 1–7. These items are not hidden implementation tasks and must not be pulled into a frontend stage merely because that stage exposes the existing backend behavior. Each follow-up requires a separate user decision before implementation, especially where the current integration tests assert the existing contract.

## TODO — repository manifest cache lifetime

This is a backend lifetime limitation, not a frontend-rewrite bug.

The process-local repository cache currently retains only the newest manifest `cache_id` for each marked repository. Requesting another manifest for the same repository deletes the previous cache entry. An eternal inactive ChangeSet may therefore retain a TanStack-cached manifest whose unloaded, lazy, or retried file requests later fail with `Unknown cache id` after another Tab loads a newer manifest for that repository.

The frontend rewrite must preserve the existing backend contract and expose any resulting file failure through the specified localized error and Toast behavior. It must not add automatic manifest recovery, fabricated client generations, compatibility handling, or backend cache changes as part of Chapters 1–7.

A separate follow-up may reconsider process-lifetime retention, bounded retention, request-identity retention, or an explicit lease/release contract. The current integration test requires older repository cache IDs to fail, so changing the policy also requires explicit approval to change that tested behavior.

## FIXME — preset manifests have no snapshot identity

This is a backend snapshot-identity bug deferred until after the frontend rewrite.

Preset manifests currently return `cache_id: ""`, and preset lazy-info/file-diff requests reconstruct current fixture state. Reloading a changed preset can therefore produce a new manifest while canonical TanStack file keys still reuse results from the previous fixture contents.

The frontend rewrite must continue using the stable backend response and must not invent a frontend generation counter or routinely remove file queries to disguise the missing identity.

A separate follow-up must give preset manifests meaningful snapshot identity. Candidate backend designs include a content-derived ID with follow-up validation or a real cached preset snapshot. Existing integration tests assert the empty ID, so the chosen backend correction requires explicit approval to update that tested contract.
