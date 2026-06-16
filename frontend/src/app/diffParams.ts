import type { DiffParams, LazyInfoPayload, RepoManifestPayload } from "../api";

/**
 * Canonical identity for a committed diff parameter set.
 *
 * This is the boundary between "draft controls" and an actual load. Only fields
 * that change the backend manifest payload belong here. View-only state, expansion,
 * pins, and loading progress must not participate in this identity.
 */
function diffParamsParts(diffParams: DiffParams) {
  return [
    diffParams.repo_id,
    diffParams.engine,
    diffParams.mode,
    diffParams.left,
    diffParams.right,
    diffParams.base_branch,
    diffParams.review_branch,
    diffParams.show_untracked,
  ] as const;
}

export function diffParamsIdentity(diffParams: DiffParams): string {
  return JSON.stringify(diffParamsParts(diffParams));
}

export function manifestParamsQueryKey(diffParams: DiffParams) {
  return ["manifest", diffParamsIdentity(diffParams)] as const;
}

export function lazyInfoParamsQueryKey(diffParams: DiffParams) {
  return ["lazy-info", diffParamsIdentity(diffParams)] as const;
}

export type ManifestQueryPayload = {
  diffParams: DiffParams;
  paramsIdentity: string;
  payload: RepoManifestPayload;
};

export type LazyInfoQueryPayload = {
  diffParams: DiffParams;
  paramsIdentity: string;
  payload: LazyInfoPayload;
};
