import type { DiffParams, LazyInfoPayload, RepoManifestPayload } from "../api";

/**
 * Canonical identity for a committed diff parameter set.
 *
 * This is the boundary between "draft controls" and an actual load. Only fields
 * that change the server manifest payload belong here. View-only state, expansion,
 * pins, and loading progress must not participate in this identity.
 */
function diffParamsParts(diffParams: DiffParams) {
  if (diffParams.mode === "preset") {
    return [
      diffParams.engine,
      diffParams.mode,
      diffParams.project_id,
      diffParams.preset_subset,
    ] as const;
  }
  if (diffParams.mode === "branch-review") {
    return [
      diffParams.project_id,
      diffParams.engine,
      diffParams.mode,
      diffParams.base_selection,
      diffParams.review_selection,
    ] as const;
  }
  return [
    diffParams.project_id,
    diffParams.engine,
    diffParams.mode,
    diffParams.left,
    diffParams.right,
    diffParams.mode === "head",
  ] as const;
}

export function diffParamsIdentity(diffParams: DiffParams): string {
  return JSON.stringify(diffParamsParts(diffParams));
}

export function manifestParamsQueryKey(diffParams: DiffParams) {
  return ["manifest", diffParamsIdentity(diffParams)] as const;
}

export function lazyInfoParamsQueryKey(
  diffParams: DiffParams,
  cacheId: string,
) {
  return ["lazy-info", diffParamsIdentity(diffParams), cacheId] as const;
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
