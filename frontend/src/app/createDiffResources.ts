import {
  batch,
  createEffect,
  createSignal,
  type Accessor,
  type Setter,
} from "solid-js";
import { createQuery } from "@tanstack/solid-query";
import {
  fetchDiff,
  fetchFileDiff,
  type DiffEngine,
  type DiffRequest,
  type FileEntry,
  type RefChoices,
  type RepoId,
} from "../api";
import type { DiffViewMode } from "../DiffGrid";
import {
  type BranchSource,
  type ControlsState,
  type LoadedDiff,
  type LoadState,
  addHydratedNotebookSummary,
  appQuery,
  branchReviewRef,
  entryDirectoryLabel,
  fileDiffQueryKey,
  fileKey,
  fileMatchesLinePin,
  loadedStatusLabel,
  nextFileExpansion,
  sortFilesByOrder,
  statusLabel,
} from "../model";
import { getLinePinFromHash } from "../linePins";
import { queryClient } from "../queryClient";
import {
  type DiffQueryPayload,
  diffRequestIdentity,
  diffRequestQueryKey,
} from "./diffRequest";

/**
 * Collaborators supplied by App and UI state.
 *
 * Diff resources own the request/query lifecycle, but progressive hydration has
 * to update visible diff state and expansion state as files arrive. Those
 * writes are intentionally passed in here instead of hiding a second copy of UI
 * state inside this primitive.
 */
type DiffResourcesOptions = {
  selectedRepoId: Accessor<RepoId | null>;
  controls: Accessor<ControlsState | null>;
  setControls: Setter<ControlsState | null>;
  diffViewMode: Accessor<DiffViewMode>;
  resetViewState: () => void;
  setLoadedDiff: Setter<LoadedDiff | null>;
  setDirectoryExpansion: Setter<Record<string, boolean>>;
  setFileExpansion: Setter<Record<string, boolean>>;
  refChoices: () => RefChoices;
};

/**
 * Owns committed diff requests and their render-as-you-load lifecycle.
 *
 * The public actions in this primitive are the only supported way to start,
 * reload, cancel, or retarget a diff. They build complete DiffRequest objects,
 * keep the URL in sync with the active request, cancel stale Solid Query work,
 * and hydrate manifest files progressively through the shared queryClient.
 *
 * Draft form controls and repo initialization live outside this primitive. UI
 * expansion and loaded-diff storage are also external, but this primitive writes
 * through the supplied setters while applying query results so that request
 * identity checks remain colocated with the async work.
 */
export function createDiffResources(options: DiffResourcesOptions) {
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
  const [activeRequest, setActiveRequest] = createSignal<DiffRequest | null>(
    null,
  );
  const [loadingFiles, setLoadingFiles] = createSignal<Record<string, boolean>>(
    {},
  );
  const [fileErrors, setFileErrors] = createSignal<Record<string, string>>({});
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");
  let appliedDiffIdentity = "";

  const activeRequestIdentity = (): string | null => {
    const request = activeRequest();
    if (request === null) {
      return null;
    }
    return diffRequestIdentity(request);
  };

  const requestIsActive = (requestIdentity: string): boolean =>
    activeRequestIdentity() === requestIdentity;

  const replaceUrlForRequest = (
    request: DiffRequest,
    viewMode = options.diffViewMode(),
  ) => {
    history.replaceState(
      {},
      "",
      `/?${appQuery(request, viewMode).toString()}${window.location.hash}`,
    );
  };

  const replaceUrlForActiveRequest = (viewMode: DiffViewMode) => {
    const request = activeRequest();
    if (request !== null) {
      replaceUrlForRequest(request, viewMode);
    }
  };

  const resetDiffState = (nextStatus: LoadState, nextStatusText: string) => {
    batch(() => {
      options.setLoadedDiff(null);
      options.resetViewState();
      setLoadingFiles({});
      setFileErrors({});
      setStatus(nextStatus);
      setStatusText(nextStatusText);
    });
  };

  const clearActiveRequest = () => {
    void queryClient.cancelQueries({ queryKey: ["diff"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    setActiveRequest(null);
  };

  const failLoad = (message: string) => {
    clearActiveRequest();
    resetDiffState("error", message);
  };

  const startDiff = (request: DiffRequest) => {
    setActiveRequest(null);
    void queryClient.cancelQueries({ queryKey: ["diff"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    queryClient.removeQueries({ queryKey: diffRequestQueryKey(request) });
    replaceUrlForRequest(request);
    appliedDiffIdentity = "";
    resetDiffState("loading", "Loading diff...");
    setActiveRequest(request);
  };

  const selectedRepoIdOrIdle = (): RepoId | null => {
    const repoId = options.selectedRepoId();
    if (repoId === null) {
      clearActiveRequest();
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    return repoId;
  };

  const diffQuery = createQuery(() => {
    const request = activeRequest();
    if (request === null) {
      return {
        queryKey: ["diff", "idle"] as const,
        queryFn: async (): Promise<DiffQueryPayload> => {
          throw new Error("Diff request is not active.");
        },
        enabled: false,
      };
    }
    const requestIdentity = diffRequestIdentity(request);
    return {
      queryKey: diffRequestQueryKey(request),
      queryFn: async ({ signal }): Promise<DiffQueryPayload> => ({
        request,
        requestIdentity,
        payload: await fetchDiff(request, signal),
      }),
      staleTime: 0,
    };
  });

  /**
   * Bridge Solid Query server state into the progressive LoadedDiff model.
   *
   * This is intentionally the narrow effect boundary for successful diff loads:
   * ignore stale query results, apply each committed request once, then let
   * hydrateManifestFiles stream file details.
   */
  createEffect(() => {
    const result = diffQuery.data;
    if (result === undefined) {
      return;
    }
    if (!requestIsActive(result.requestIdentity)) {
      return;
    }
    if (appliedDiffIdentity === result.requestIdentity) {
      return;
    }
    appliedDiffIdentity = result.requestIdentity;
    applyDiffPayload(result);
  });

  /**
   * Bridge Solid Query errors into the status surface.
   *
   * Query cancellation and stale requests are filtered by activeRequestIdentity;
   * visible errors only belong to the currently active request.
   */
  createEffect(() => {
    const error = diffQuery.error;
    if (!diffQuery.isError || error === null) {
      return;
    }
    const requestIdentity = activeRequestIdentity();
    if (requestIdentity === null) {
      return;
    }
    batch(() => {
      setStatus("error");
      setStatusText(
        error instanceof Error ? error.message : "Failed to load diff.",
      );
    });
    const errorMessage =
      error instanceof Error ? error.message : "Failed to load diff.";
    console.error(`Failed to load diff: ${errorMessage}`);
  });

  function applyDiffPayload(result: DiffQueryPayload) {
    const { request, requestIdentity, payload } = result;
    const order = Object.fromEntries(
      payload.files.map((entry, index) => [fileKey(entry), index]),
    );
    const lazyManifestFiles = payload.files.filter((entry) => entry.lazy);
    const baseStatus = statusLabel(
      request,
      payload.left_label,
      payload.right_label,
    );
    batch(() => {
      options.setLoadedDiff({
        request,
        files: [],
        lazyFiles: lazyManifestFiles,
        fileOrder: order,
        summary: payload.summary,
      });
      setStatusText(loadedStatusLabel(baseStatus, lazyManifestFiles.length, 0));
    });
    void hydrateManifestFiles(
      request,
      requestIdentity,
      payload.files,
      baseStatus,
      lazyManifestFiles.length,
    );
  }

  async function hydrateManifestFiles(
    request: DiffRequest,
    requestIdentity: string,
    manifestFiles: FileEntry[],
    baseStatus: string,
    initialLoadedFiles: number,
  ) {
    const pendingFiles = manifestFiles.filter((entry) => !entry.lazy);
    if (pendingFiles.length === 0) {
      if (requestIsActive(requestIdentity)) {
        setStatus("done");
        setStatusText(baseStatus);
      }
      return;
    }
    const pin = getLinePinFromHash();
    let hasFailure = false;
    let loadedFiles = initialLoadedFiles;
    let failedDetailFiles = 0;

    const workers = Array.from(
      { length: Math.min(4, pendingFiles.length) },
      async (_, workerIndex) => {
        for (let index = workerIndex; index < pendingFiles.length; index += 4) {
          if (!requestIsActive(requestIdentity)) {
            return;
          }
          const entry = pendingFiles[index];
          const key = fileKey(entry);
          try {
            const hydrated = await queryClient.fetchQuery({
              queryKey: fileDiffQueryKey(request, entry),
              queryFn: ({ signal }) => fetchFileDiff(request, entry, signal),
              staleTime: 0,
            });
            if (!requestIsActive(requestIdentity)) {
              return;
            }
            const nextEntry = {
              ...entry,
              ...hydrated,
              lazy: null,
              default_expanded: hydrated.default_expanded,
            };
            const nextKey = fileKey(nextEntry);
            const shouldOpenPinnedFile =
              pin !== null && fileMatchesLinePin(nextEntry, pin);
            loadedFiles += 1;
            batch(() => {
              options.setLoadedDiff((current) => {
                if (current === null) {
                  return current;
                }
                const withoutCurrent = current.files.filter(
                  (file) => fileKey(file) !== nextKey,
                );
                return {
                  ...current,
                  files: sortFilesByOrder(
                    [...withoutCurrent, nextEntry],
                    current.fileOrder,
                  ),
                  summary: addHydratedNotebookSummary(
                    current.summary,
                    nextEntry,
                  ),
                };
              });
              options.setDirectoryExpansion((current) => {
                const directory = entryDirectoryLabel(nextEntry);
                if (shouldOpenPinnedFile) {
                  return { ...current, [directory]: true };
                }
                if (Object.hasOwn(current, directory)) {
                  return current;
                }
                return { ...current, [directory]: true };
              });
              options.setFileExpansion((current) =>
                nextFileExpansion(
                  shouldOpenPinnedFile
                    ? { ...current, [nextKey]: true }
                    : current,
                  nextEntry,
                  nextKey,
                ),
              );
              setStatusText(
                loadedStatusLabel(baseStatus, loadedFiles, failedDetailFiles),
              );
            });
          } catch (error) {
            if (!requestIsActive(requestIdentity)) {
              return;
            }
            hasFailure = true;
            loadedFiles += 1;
            failedDetailFiles += 1;
            batch(() => {
              options.setLoadedDiff((current) => {
                if (current === null) {
                  return current;
                }
                const withoutCurrent = current.files.filter(
                  (file) => fileKey(file) !== key,
                );
                return {
                  ...current,
                  files: sortFilesByOrder(
                    [...withoutCurrent, entry],
                    current.fileOrder,
                  ),
                };
              });
              options.setDirectoryExpansion((current) => {
                const directory = entryDirectoryLabel(entry);
                if (Object.hasOwn(current, directory)) {
                  return current;
                }
                return { ...current, [directory]: true };
              });
              options.setFileExpansion((current) => ({
                ...current,
                [key]: true,
              }));
              setFileErrors((current) => ({
                ...current,
                [key]:
                  error instanceof Error
                    ? error.message
                    : "Failed to load file diff.",
              }));
              setStatus("error");
              setStatusText(
                loadedStatusLabel(baseStatus, loadedFiles, failedDetailFiles),
              );
            });
            const errorMessage =
              error instanceof Error ? error.message : String(error);
            console.error(
              `Failed to hydrate file diff for ${fileKey(entry)}: ${errorMessage}`,
            );
          }
        }
      },
    );
    await Promise.all(workers);
    if (requestIsActive(requestIdentity)) {
      setStatus(hasFailure ? "error" : "done");
      if (!hasFailure) {
        setStatusText(baseStatus);
      }
    }
  }

  const loadAgainstHead = (selectedEngine: DiffEngine = engine()) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    options.setControls((current) =>
      current === null ? current : { ...current, mode: "head" },
    );
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "head",
      left: "head",
      right: "worktree",
      base_branch: null,
      review_branch: null,
      show_untracked: true,
    });
  };

  const loadPreset = (selectedEngine: DiffEngine = engine()) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    options.setControls((current) =>
      current === null ? current : { ...current, mode: "preset" },
    );
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "preset",
      left: "presets",
      right: "new",
      base_branch: null,
      review_branch: null,
      show_untracked: false,
    });
  };

  const loadRefs = (
    left: string,
    right: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    const trimmedLeft = left.trim();
    const trimmedRight = right.trim();
    options.setControls((current) =>
      current === null ? current : { ...current, mode: "refs", left, right },
    );
    if (trimmedLeft.length === 0 || trimmedRight.length === 0) {
      failLoad("Enter both refs to compare them.");
      return;
    }
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "refs",
      left: trimmedLeft,
      right: trimmedRight,
      base_branch: null,
      review_branch: null,
      show_untracked: false,
    });
  };

  const loadBranchReview = (
    baseSource: BranchSource,
    baseRemote: string,
    baseBranch: string,
    branchSource: BranchSource,
    branchRemote: string,
    reviewBranch: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    const choices = options.refChoices();
    options.setControls((current) =>
      current === null
        ? current
        : {
            ...current,
            mode: "branch-review",
            baseSource,
            baseRemote,
            baseBranch,
            branchSource,
            branchRemote,
            reviewBranch,
          },
    );
    if (baseSource === "remote" && !baseRemote.trim()) {
      failLoad("Pick a base remote.");
      return;
    }
    if (!baseBranch.trim()) {
      failLoad("Pick a base branch.");
      return;
    }
    if (branchSource === "remote" && !branchRemote.trim()) {
      failLoad("Pick a branch remote.");
      return;
    }
    if (!reviewBranch.trim()) {
      failLoad("Pick a branch to compare against the base branch.");
      return;
    }
    startDiff({
      repo_id: repoId,
      engine: selectedEngine,
      mode: "branch-review",
      left: "",
      right: "",
      base_branch: branchReviewRef(
        baseSource,
        baseRemote,
        baseBranch,
        choices.remote_names,
      ),
      review_branch: branchReviewRef(
        branchSource,
        branchRemote,
        reviewBranch,
        choices.remote_names,
      ),
      show_untracked: false,
    });
  };

  const loadInitialControls = (
    nextControls: ControlsState,
    nextEngine: DiffEngine,
  ) => {
    setEngine(nextEngine);
    if (nextControls.mode === "refs") {
      loadRefs(nextControls.left, nextControls.right, nextEngine);
    } else if (nextControls.mode === "branch-review") {
      loadBranchReview(
        nextControls.baseSource,
        nextControls.baseRemote,
        nextControls.baseBranch,
        nextControls.branchSource,
        nextControls.branchRemote,
        nextControls.reviewBranch,
        nextEngine,
      );
    } else if (nextControls.mode === "preset") {
      loadPreset(nextEngine);
    } else {
      loadAgainstHead(nextEngine);
    }
  };

  const reloadDiff = () => {
    const request = activeRequest();
    if (request === null) {
      return;
    }
    startDiff(request);
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const request = activeRequest();
    if (request !== null) {
      startDiff({ ...request, engine: nextEngine });
    }
  };

  return {
    engine,
    activeRequest,
    activeRequestIdentity,
    loadingFiles,
    setLoadingFiles,
    fileErrors,
    setFileErrors,
    status,
    statusText,
    resetDiffState,
    clearActiveRequest,
    loadAgainstHead,
    loadPreset,
    loadRefs,
    loadBranchReview,
    loadInitialControls,
    reloadDiff,
    loadEngine,
    replaceUrlForActiveRequest,
  };
}

export type DiffResources = ReturnType<typeof createDiffResources>;
