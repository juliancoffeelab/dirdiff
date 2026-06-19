import {
  batch,
  createEffect,
  createSignal,
  type Accessor,
  type Setter,
} from "solid-js";
import { createQuery } from "@tanstack/solid-query";
import { isCancelledError } from "@tanstack/query-core";
import {
  fetchLazyInfo,
  fetchManifest,
  fetchFileDiff,
  REQUEST_TIMEOUT_MS,
  diffParamsQueryParams,
  type BranchReviewDiffParams,
  type DiffEngine,
  type DiffParams,
  type FileEntry,
  type HeadDiffParams,
  type LazyInfoFile,
  type ManifestEntry,
  type PresetDiffParams,
  type PresetType,
  type RefChoices,
  type RefsDiffParams,
  type RepoId,
} from "../api";
import type { DiffViewMode } from "../DiffGrid";
import {
  type BranchSource,
  type ControlsState,
  type LoadedDiff,
  type LoadState,
  addHydratedNotebookSummary,
  entryDirectoryLabel,
  fileDiffQueryKey,
  fileKey,
  fileMatchesLinePin,
  sortFilesByOrder,
} from "../fileUtils";
import { getLinePinFromHash } from "../linePins";
import { queryClient } from "../queryClient";
import {
  type LazyInfoQueryPayload,
  type ManifestQueryPayload,
  diffParamsIdentity,
  lazyInfoParamsQueryKey,
  manifestParamsQueryKey,
} from "./diffParams";

const builtinSides = new Set(["head", "index", "worktree"]);
const SLOW_FILE_DIFF_MS = REQUEST_TIMEOUT_MS;

function nullableStringValue(
  value: string | null | undefined,
  fallback: string,
): string {
  if (value !== null && value !== undefined && value.length > 0) {
    return value;
  }
  return fallback;
}

function appQuery(
  diffParams: DiffParams,
  viewMode: DiffViewMode,
): URLSearchParams {
  const params = diffParamsQueryParams(diffParams);
  params.set("view", viewMode);
  return params;
}

function statusLabel(
  diffParams: DiffParams,
  leftLabel?: string,
  rightLabel?: string,
): string {
  if (diffParams.mode === "head") {
    return "Working tree vs HEAD";
  }
  if (diffParams.mode === "branch-review") {
    return `${diffParams.review_branch} vs ${diffParams.base_branch}`;
  }
  if (diffParams.mode === "preset") {
    const kind = diffParams.preset_type === "fold" ? "Fold" : "Diff";
    return `${kind} preset ${diffParams.preset}`;
  }
  return `${nullableStringValue(leftLabel, diffParams.left)} vs ${nullableStringValue(rightLabel, diffParams.right)}`;
}

function loadedStatusLabel(
  baseStatus: string,
  loadedFiles: number,
  failedDetailFiles: number,
): string {
  const fileWord = loadedFiles === 1 ? "file" : "files";
  const failureText =
    failedDetailFiles > 0 ? `, failed details ${failedDetailFiles}` : "";
  return `${baseStatus} · loaded ${loadedFiles} ${fileWord}${failureText}`;
}

function slowFileDiffDetail(file: ManifestEntry, elapsedMs: number): string {
  const elapsedSeconds = Math.floor(elapsedMs / 1000);
  return `${manifestDisplayName(file)} is slow, waiting for ${elapsedSeconds}s...`;
}

function manifestDisplayName(entry: ManifestEntry): string {
  if (entry.left_path !== null && entry.left_path.length > 0) {
    if (entry.right_path !== null && entry.right_path.length > 0) {
      if (entry.left_path === entry.right_path) {
        return entry.left_path;
      }
      return `${entry.left_path} -> ${entry.right_path}`;
    }
    return entry.left_path;
  }
  if (entry.right_path !== null && entry.right_path.length > 0) {
    return entry.right_path;
  }
  return "(unknown)";
}

function qualifyRemoteRef(
  remote: string,
  ref: string,
  remoteNames: string[],
): string {
  const trimmedRemote = remote.trim();
  const trimmedRef = ref.trim();
  if (trimmedRemote.length === 0 || trimmedRef.length === 0) {
    return trimmedRef;
  }
  if (
    trimmedRef.startsWith("refs/") ||
    builtinSides.has(trimmedRef) ||
    /^[0-9a-f]{7,40}$/i.test(trimmedRef) ||
    trimmedRef.includes(":") ||
    trimmedRef.includes("^") ||
    trimmedRef.includes("~") ||
    remoteNames.some(
      (name) => trimmedRef === name || trimmedRef.startsWith(`${name}/`),
    )
  ) {
    return trimmedRef;
  }
  return `${trimmedRemote}/${trimmedRef}`;
}

function branchReviewRef(
  source: BranchSource,
  remote: string,
  branch: string,
  remoteNames: string[],
): string {
  if (source === "local") {
    return branch.trim();
  }
  return qualifyRemoteRef(remote, branch, remoteNames);
}

function nextFileExpansion(
  current: Record<string, boolean>,
  newFile: FileEntry,
  newFileKey: string,
): Record<string, boolean> {
  if (Object.hasOwn(current, newFileKey)) {
    return current;
  }
  return {
    ...current,
    [newFileKey]: newFile.default_expanded === true,
  };
}

/**
 * Collaborators supplied by App and UI state.
 *
 * Diff resources own the params/query lifecycle, but progressive hydration has
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
  addErrorToast: (title: string, error: unknown) => void;
};

/**
 * Owns committed diff params and their render-as-you-load lifecycle.
 *
 * The public actions in this primitive are the only supported way to start,
 * reload, cancel, or retarget a diff. They build complete DiffParams objects,
 * keep the URL in sync with the current params, cancel stale Solid Query work,
 * and hydrate manifest files progressively through the shared queryClient.
 *
 * Draft form controls and repo initialization live outside this primitive. UI
 * expansion and loaded-diff storage are also external, but this primitive writes
 * through the supplied setters while applying query results so that params
 * identity checks remain colocated with the async work.
 */
export function createDiffResources(options: DiffResourcesOptions) {
  const [engine, setEngine] = createSignal<DiffEngine>("dirdiff");
  const [currentParams, setCurrentParams] = createSignal<DiffParams | null>(
    null,
  );
  const [loadingFiles, setLoadingFiles] = createSignal<Record<string, boolean>>(
    {},
  );
  const [fileErrors, setFileErrors] = createSignal<Record<string, string>>({});
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");
  let appliedManifestIdentity = "";
  let toastedManifestErrorIdentity = "";

  const currentParamsIdentity = (): string | null => {
    const diffParams = currentParams();
    if (diffParams === null) {
      return null;
    }
    return diffParamsIdentity(diffParams);
  };

  const paramsAreCurrent = (paramsIdentity: string): boolean =>
    currentParamsIdentity() === paramsIdentity;

  const replaceUrlForParams = (
    diffParams: DiffParams,
    viewMode = options.diffViewMode(),
  ) => {
    history.replaceState(
      {},
      "",
      `/?${appQuery(diffParams, viewMode).toString()}${window.location.hash}`,
    );
  };

  const replaceUrlForCurrentParams = (viewMode: DiffViewMode) => {
    const diffParams = currentParams();
    if (diffParams !== null) {
      replaceUrlForParams(diffParams, viewMode);
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

  const clearCurrentParams = () => {
    void queryClient.cancelQueries({ queryKey: ["manifest"] });
    void queryClient.cancelQueries({ queryKey: ["lazy-info"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    setCurrentParams(null);
  };

  const failLoad = (message: string) => {
    clearCurrentParams();
    resetDiffState("error", message);
  };

  const startDiff = (diffParams: DiffParams) => {
    setCurrentParams(null);
    void queryClient.cancelQueries({ queryKey: ["manifest"] });
    void queryClient.cancelQueries({ queryKey: ["lazy-info"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    queryClient.removeQueries({ queryKey: manifestParamsQueryKey(diffParams) });
    queryClient.removeQueries({ queryKey: lazyInfoParamsQueryKey(diffParams) });
    replaceUrlForParams(diffParams);
    appliedManifestIdentity = "";
    toastedManifestErrorIdentity = "";
    resetDiffState("loading", "Loading diff...");
    setCurrentParams(diffParams);
  };

  const selectedRepoIdOrIdle = (): RepoId | null => {
    const repoId = options.selectedRepoId();
    if (repoId === null) {
      clearCurrentParams();
      resetDiffState("idle", "Choose a repo to load a diff.");
      return null;
    }
    return repoId;
  };

  const manifestQuery = createQuery(() => {
    const diffParams = currentParams();
    if (diffParams === null) {
      return {
        queryKey: ["manifest", "idle"] as const,
        queryFn: async (): Promise<ManifestQueryPayload> => {
          throw new Error("Manifest params are not active.");
        },
        enabled: false,
      };
    }
    const paramsIdentity = diffParamsIdentity(diffParams);
    return {
      queryKey: manifestParamsQueryKey(diffParams),
      queryFn: async ({ signal }): Promise<ManifestQueryPayload> => {
        const payload = await fetchManifest(diffParams, signal);
        return {
          diffParams,
          paramsIdentity,
          payload,
        };
      },
      retry: false,
      staleTime: 0,
    };
  });

  /**
   * Bridge Solid Query server state into the progressive LoadedDiff model.
   *
   * This is intentionally the narrow effect boundary for successful diff loads:
   * ignore stale query results, apply each committed params set once, then let
   * hydrateManifestFiles stream file details.
   */
  createEffect(() => {
    const result = manifestQuery.data;
    if (result === undefined) {
      return;
    }
    if (!paramsAreCurrent(result.paramsIdentity)) {
      return;
    }
    if (appliedManifestIdentity === result.paramsIdentity) {
      return;
    }
    appliedManifestIdentity = result.paramsIdentity;
    applyManifestPayload(result);
  });

  /**
   * Bridge Solid Query errors into the status surface.
   *
   * Query cancellation and stale params are filtered by currentParamsIdentity;
   * visible errors only belong to the currently active params.
   */
  createEffect(() => {
    const error = manifestQuery.error;
    if (!manifestQuery.isError || error === null) {
      return;
    }
    const paramsIdentity = currentParamsIdentity();
    if (paramsIdentity === null) {
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
    if (toastedManifestErrorIdentity !== paramsIdentity) {
      toastedManifestErrorIdentity = paramsIdentity;
      options.addErrorToast("Failed to load diff", error);
    }
  });

  function applyManifestPayload(result: ManifestQueryPayload) {
    const { diffParams, paramsIdentity, payload } = result;
    const order = Object.fromEntries(
      payload.files.map((entry, index) => [fileKey(entry), index]),
    );
    const lazyManifestFiles = payload.files.filter((entry) => entry.lazy);
    const baseStatus = statusLabel(
      diffParams,
      payload.left_label,
      payload.right_label,
    );
    batch(() => {
      options.setLoadedDiff({
        params: diffParams,
        // Manifest files are not FileEntry values; rendered files are inserted
        // only after /api/file-diff or /api/lazy-info returns enough data.
        files: [],
        lazyFiles: lazyManifestFiles,
        fileOrder: order,
        summary: payload.summary,
      });
      setStatusText(loadedStatusLabel(baseStatus, 0, 0));
    });
    void hydrateManifestFiles(
      diffParams,
      paramsIdentity,
      payload.files,
      baseStatus,
      0,
    );
    void hydrateLazyInfo(diffParams, paramsIdentity, lazyManifestFiles, order);
  }

  function hydrateLazyFiles(
    manifestFiles: ManifestEntry[],
    lazyInfoFiles: LazyInfoFile[],
    order: Record<string, number>,
  ): FileEntry[] {
    const manifestKeys = new Set(manifestFiles.map((entry) => fileKey(entry)));
    const lazyInfoKeys = new Set<string>();
    const lazyInfoEntries = new Map<string, LazyInfoFile>();
    for (const file of lazyInfoFiles) {
      if (file.display_name.length === 0) {
        throw new Error("Lazy info returned an empty display_name.");
      }
      const key = fileKey(file);
      if (!manifestKeys.has(key)) {
        throw new Error(
          `Lazy info returned file missing from manifest: ${key}.`,
        );
      }
      if (order[key] === undefined) {
        throw new Error(`Lazy info returned file missing from order: ${key}.`);
      }
      if (lazyInfoKeys.has(key)) {
        throw new Error(`Lazy info returned duplicate file: ${key}.`);
      }
      lazyInfoKeys.add(key);
      lazyInfoEntries.set(key, file);
    }
    for (const key of manifestKeys) {
      if (!lazyInfoKeys.has(key)) {
        throw new Error(`Lazy info is missing file from manifest: ${key}.`);
      }
    }
    return manifestFiles.map((entry) => {
      const key = fileKey(entry);
      const lazyInfoEntry = lazyInfoEntries.get(key);
      if (lazyInfoEntry === undefined) {
        throw new Error(`Lazy info is missing file from manifest: ${key}.`);
      }
      if (lazyInfoEntry.lazy === null) {
        throw new Error(`Lazy info returned file without lazy reason: ${key}.`);
      }
      // Lazy placeholder FileEntry construction is allowed only from
      // /api/lazy-info, which must contain every field needed by the card/tree.
      return {
        file_kind: lazyInfoEntry.file_kind,
        left_path: lazyInfoEntry.left_path,
        right_path: lazyInfoEntry.right_path,
        display_name: lazyInfoEntry.display_name,
        summary: lazyInfoEntry.summary,
        lazy: lazyInfoEntry.lazy,
      };
    });
  }

  async function hydrateLazyInfo(
    diffParams: DiffParams,
    paramsIdentity: string,
    manifestFiles: ManifestEntry[],
    order: Record<string, number>,
  ) {
    if (manifestFiles.length === 0) {
      return;
    }
    try {
      const result = await queryClient.fetchQuery({
        queryKey: lazyInfoParamsQueryKey(diffParams),
        queryFn: async ({ signal }): Promise<LazyInfoQueryPayload> => ({
          diffParams,
          paramsIdentity,
          payload: await fetchLazyInfo(diffParams, signal),
        }),
        staleTime: 0,
      });
      if (!paramsAreCurrent(result.paramsIdentity)) {
        return;
      }
      const mergedFiles = hydrateLazyFiles(
        manifestFiles,
        result.payload.files,
        order,
      );
      options.setLoadedDiff((current) => {
        if (current === null) {
          return current;
        }
        const lazyKeys = new Set(manifestFiles.map((entry) => fileKey(entry)));
        const existingFiles = current.files.filter(
          (entry) => !lazyKeys.has(fileKey(entry)),
        );
        return {
          ...current,
          // These FileEntry values are constructed from /api/lazy-info, not
          // from /api/manifest.
          files: sortFilesByOrder(
            [...existingFiles, ...mergedFiles],
            current.fileOrder,
          ),
        };
      });
    } catch (error) {
      if (!paramsAreCurrent(paramsIdentity)) {
        return;
      }
      if (isCancelledError(error)) {
        return;
      }
      const errorMessage =
        error instanceof Error
          ? error.message
          : "Failed to load lazy file info.";
      console.error(`Failed to load lazy file info: ${errorMessage}`);
      options.addErrorToast("Failed to load lazy file info", error);
    }
  }

  async function hydrateManifestFiles(
    diffParams: DiffParams,
    paramsIdentity: string,
    manifestFiles: ManifestEntry[],
    baseStatus: string,
    initialLoadedFiles: number,
  ) {
    const pendingFiles = manifestFiles.filter((entry) => !entry.lazy);
    if (pendingFiles.length === 0) {
      if (paramsAreCurrent(paramsIdentity)) {
        setStatus("done");
        setStatusText(baseStatus);
      }
      return;
    }
    const pin = getLinePinFromHash();
    let hasFailure = false;
    let toastedHydrationFailure = false;
    let loadedFiles = initialLoadedFiles;
    let failedDetailFiles = 0;

    const workers = Array.from(
      { length: Math.min(4, pendingFiles.length) },
      async (_, workerIndex) => {
        for (let index = workerIndex; index < pendingFiles.length; index += 4) {
          if (!paramsAreCurrent(paramsIdentity)) {
            return;
          }
          const entry = pendingFiles[index];
          const key = fileKey(entry);
          let slowTimeout: number | null = null;
          let slowInterval: number | null = null;
          const clearSlowStatus = () => {
            if (slowTimeout !== null) {
              window.clearTimeout(slowTimeout);
              slowTimeout = null;
            }
            if (slowInterval !== null) {
              window.clearInterval(slowInterval);
              slowInterval = null;
            }
          };
          try {
            if (diffParams.engine === "difftastic") {
              const startedAt = Date.now();
              const updateSlowStatus = () => {
                if (!paramsAreCurrent(paramsIdentity)) {
                  clearSlowStatus();
                  return;
                }
                setStatusText(
                  `${loadedStatusLabel(
                    baseStatus,
                    loadedFiles,
                    failedDetailFiles,
                  )} · ${slowFileDiffDetail(entry, Date.now() - startedAt)}`,
                );
              };
              slowTimeout = window.setTimeout(() => {
                updateSlowStatus();
                slowInterval = window.setInterval(updateSlowStatus, 1000);
              }, SLOW_FILE_DIFF_MS);
            }
            const hydrated = await queryClient.fetchQuery({
              queryKey: fileDiffQueryKey(diffParams, entry),
              queryFn: ({ signal }) => fetchFileDiff(diffParams, entry, signal),
              retry: false,
              staleTime: 0,
            });
            clearSlowStatus();
            if (!paramsAreCurrent(paramsIdentity)) {
              return;
            }
            // Hydrated FileEntry construction is allowed only from
            // /api/file-diff. Do not merge in the /api/manifest entry.
            const nextEntry = hydrated;
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
                  // Insert only the /api/file-diff FileEntry into the rendered
                  // file list.
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
            clearSlowStatus();
            if (!paramsAreCurrent(paramsIdentity)) {
              return;
            }
            if (isCancelledError(error)) {
              continue;
            }
            hasFailure = true;
            loadedFiles += 1;
            failedDetailFiles += 1;
            batch(() => {
              options.setLoadedDiff((current) => {
                if (current === null) {
                  return current;
                }
                const failedEntry: FileEntry = {
                  file_kind: entry.file_kind,
                  left_path: entry.left_path,
                  right_path: entry.right_path,
                  display_name: manifestDisplayName(entry),
                  lazy: {
                    type: "error",
                    original: entry.lazy,
                  },
                };
                const withoutCurrent = current.files.filter(
                  (file) => fileKey(file) !== key,
                );
                return {
                  ...current,
                  // Error placeholders are the only renderable FileEntry values
                  // constructed from /api/manifest; they reuse this manifest
                  // handle when the user retries /api/file-diff.
                  files: sortFilesByOrder(
                    [...withoutCurrent, failedEntry],
                    current.fileOrder,
                  ),
                };
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
            if (!toastedHydrationFailure) {
              toastedHydrationFailure = true;
              options.addErrorToast("Failed to load file diff", error);
            }
          }
        }
      },
    );
    await Promise.all(workers);
    if (paramsAreCurrent(paramsIdentity)) {
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
    const diffParams: HeadDiffParams = {
      repo_id: repoId,
      engine: selectedEngine,
      mode: "head",
      left: "head",
      right: "worktree",
      show_untracked: true,
    };
    startDiff(diffParams);
  };

  const loadPreset = (
    presetType: PresetType,
    preset: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const repoId = selectedRepoIdOrIdle();
    if (repoId === null) {
      return;
    }
    options.setControls((current) =>
      current === null
        ? current
        : {
            ...current,
            mode: "preset",
            presetType,
            preset,
          },
    );
    const diffParams: PresetDiffParams = {
      repo_id: repoId,
      engine: selectedEngine,
      mode: "preset",
      preset_type: presetType,
      preset,
    };
    startDiff(diffParams);
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
    const diffParams: RefsDiffParams = {
      repo_id: repoId,
      engine: selectedEngine,
      mode: "refs",
      left: trimmedLeft,
      right: trimmedRight,
    };
    startDiff(diffParams);
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
    const diffParams: BranchReviewDiffParams = {
      repo_id: repoId,
      engine: selectedEngine,
      mode: "branch-review",
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
    };
    startDiff(diffParams);
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
      loadPreset(nextControls.presetType, nextControls.preset, nextEngine);
    } else {
      loadAgainstHead(nextEngine);
    }
  };

  const reloadDiff = () => {
    const diffParams = currentParams();
    if (diffParams === null) {
      return;
    }
    startDiff(diffParams);
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const diffParams = currentParams();
    if (diffParams !== null) {
      const nextDiffParams: DiffParams = {
        ...diffParams,
        engine: nextEngine,
      };
      startDiff(nextDiffParams);
    }
  };

  return {
    engine,
    currentParams,
    currentParamsIdentity,
    loadingFiles,
    setLoadingFiles,
    fileErrors,
    setFileErrors,
    status,
    statusText,
    resetDiffState,
    clearCurrentParams,
    loadAgainstHead,
    loadPreset,
    loadRefs,
    loadBranchReview,
    loadInitialControls,
    reloadDiff,
    loadEngine,
    replaceUrlForCurrentParams,
  };
}

export type DiffResources = ReturnType<typeof createDiffResources>;
