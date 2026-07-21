# API error contract TODOs

This document records deferred backend-and-frontend API error contract work
identified by the fallback audit. It preserves the required cross-layer scope so
a later change can replace tolerant frontend parsing without leaving a
compatibility shim. It does not authorize implementation, change current error
presentation, or change the specified repository-cache-expiration lifecycle.

The governing rewrite guidance requires the application to be conservative in
what it accepts and to assert unexpectedly bad data at its boundary. These TODOs
cannot be completed only in the frontend because the current backend response
contract supplies the ambiguity they describe.

## F3 — Define one error response envelope

`frontend/src/new/api/api.ts` currently accepts both `{ "error": string }` and
FastAPI's `{ "detail": string }`, then accepts any remaining response body as a
plain-text error message. This parsing chain allows endpoints and their declared
response models to disagree without failing at the API boundary.

A later backend contract change should choose one strict error envelope and use
it for every endpoint. The same change must update backend response models,
exception handling, integration tests, and the frontend schema together. Once
all producers emit the canonical shape, `throwResponseError` should parse only
that shape and reject malformed JSON or alternate envelopes instead of retaining
compatibility parsing.

## F4 — Give repository cache expiration a structured code

`frontend/src/new/api/api.ts` currently classifies an expired repository cache by
testing whether human-readable `detail` starts with `"Unknown cache id: "`.
Display copy therefore controls whether the UI reports an ordinary error or
silently replaces the complete ChangeSet snapshot.

The canonical error envelope from F3 should include a stable machine-readable
code for repository-cache expiration. The backend file and lazy-info endpoints
must emit that code, integration tests must assert it, and the frontend must
classify only that code. The existing complete-snapshot suppression and restart
remain unchanged: this TODO replaces the discriminator, not the lifecycle.

This work overlaps the structured repository cache expiration item in
`rewrite/stages/followups.md`; completing either item should remove or update the
other so the repository has one authoritative outstanding task.

## F24 — Return repository-default heuristic failure as a structured HTTP error

`/api/repo-defaults` currently returns an HTTP-success response when its
repository-default heuristic fails. The ordinary response body embeds
`heuristic_fail` where a successful base selection would otherwise appear. That
is not a successful defaults request: the endpoint did not produce the requested
repository defaults.

The endpoint must return a non-OK HTTP status and a structured JSON error payload.
That payload must retain the machine-readable error kind `heuristic_fail`; a
human-readable message may accompany it but must not replace the kind. The
frontend must classify this failure from the non-OK response and its explicit
error kind, then report the Toast title `Heuristic for repository defaults
failed` through the ordinary failed-query path.

The backend response model, endpoint, integration tests, frontend error schema,
and defaults query must change together. After that contract exists, the frontend
must stop accepting `heuristic_fail` inside an HTTP-success defaults payload; it
must not preserve both representations as a compatibility shim.
