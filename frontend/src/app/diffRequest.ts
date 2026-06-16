import type { DiffRequest, RepoDiffPayload } from "../api";

/**
 * Canonical identity for a committed diff request.
 *
 * This is the boundary between "draft controls" and an actual load. Only fields
 * that change the backend diff payload belong here. View-only state, expansion,
 * pins, and loading progress must not participate in this identity.
 */
function diffRequestParts(request: DiffRequest) {
  return [
    request.repo_id,
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
  ] as const;
}

export function diffRequestIdentity(request: DiffRequest): string {
  return JSON.stringify(diffRequestParts(request));
}

export function diffRequestQueryKey(request: DiffRequest) {
  return ["diff", diffRequestIdentity(request)] as const;
}

export type DiffQueryPayload = {
  request: DiffRequest;
  requestIdentity: string;
  payload: RepoDiffPayload;
};
