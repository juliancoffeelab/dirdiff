import { batch, createSignal, type Accessor, type Setter } from "solid-js";
import { createStore, reconcile } from "solid-js/store";
import { isCancelledError } from "@tanstack/query-core";
import {
  fetchLazyInfo,
  fetchManifest,
  fetchFileDiff,
  REQUEST_TIMEOUT_MS,
  diffParamsQueryParams,
  type BranchReviewDiffParams,
  type BranchSelection,
  type DiffEngine,
  type DiffParams,
  type FileEntry,
  type HeadDiffParams,
  type LazyInfoFile,
  type ManifestEntry,
  type PreparedPullRequest,
  type PresetDiffParams,
  type PresetType,
  type RefsDiffParams,
  type ProjectId,
} from "../api";
import type { DiffViewMode } from "../DiffGrid";
import {
  type BranchSelectionDraft,
  type ControlsState,
  type LoadState,
  type RenderedFileEntry,
  fileDiffQueryKey,
  fileKey,
  fileMatchesLinePin,
  manifestFileEntriesFromTree,
} from "../fileUtils";
import { getLinePinFromHash } from "../linePins";
import { queryClient } from "../queryClient";
import {
  type LazyInfoQueryPayload,
  diffParamsIdentity,
  lazyInfoParamsQueryKey,
  manifestParamsQueryKey,
} from "./diffParams";

const SLOW_FILE_DIFF_MS = REQUEST_TIMEOUT_MS;
const MANUAL_FILE_DIFF_TIMEOUT_MS = 60_000;

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
  controls: ControlsState | null,
): URLSearchParams {
  const params = diffParamsQueryParams(diffParams);
  if (
    diffParams.mode === "branch-review" &&
    controls !== null &&
    controls.tab === "pull-request"
  ) {
    params.set("tab", "pull-request");
    params.set("pull_request_url", controls.pullRequestUrl);
  }
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
    return `${branchSelectionLabel(diffParams.review_selection)} vs ${branchSelectionLabel(diffParams.base_selection)}`;
  }
  if (diffParams.mode === "preset") {
    let kind = "Diff";
    if (diffParams.project_id === "fold") {
      kind = "Fold";
    }
    if (diffParams.project_id === "gumtree") {
      kind = "GumTree";
    }
    return `${kind} preset ${diffParams.preset_subset}`;
  }
  return `${nullableStringValue(leftLabel, diffParams.left)} vs ${nullableStringValue(rightLabel, diffParams.right)}`;
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

function lazyOriginalReasonForHydration(
  file: RenderedFileEntry,
): ManifestEntry["lazy"] {
  if (file.originalLazyReason !== null) {
    return file.originalLazyReason;
  }
  const lazy = file.lazy;
  if (lazy === undefined) {
    throw new Error("Hydrated file did not have a lazy reason.");
  }
  if (lazy === null) {
    return null;
  }
  if (typeof lazy === "string") {
    return lazy;
  }
  return lazy.original;
}

function shouldHydrateManifestEntry(
  entry: ManifestEntry,
  hydratedLazyKeys: Set<string>,
): boolean {
  if (entry.lazy === null) {
    return true;
  }
  return hydratedLazyKeys.has(fileKey(entry));
}

function branchSelectionLabel(selection: BranchSelection): string {
  if (selection.source === "local") {
    return selection.branch;
  }
  return `${selection.branch} @ ${selection.remote}`;
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

function booleanMapSnapshot(map: BooleanMap): Record<string, boolean> {
  const snapshot: Record<string, boolean> = {};
  for (const [key, value] of Object.entries(map)) {
    if (value !== undefined) {
      snapshot[key] = value;
    }
  }
  return snapshot;
}

function stringMapSnapshot(map: StringMap): Record<string, string> {
  const snapshot: Record<string, string> = {};
  for (const [key, value] of Object.entries(map)) {
    if (value !== undefined) {
      snapshot[key] = value;
    }
  }
  return snapshot;
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
  selectedProjectId: Accessor<ProjectId | null>;
  controls: Accessor<ControlsState | null>;
  setControls: Setter<ControlsState | null>;
  diffViewMode: Accessor<DiffViewMode>;
  resetViewState: () => void;
  clearLoadedDiff: () => void;
  applyManifest: (
    diffParams: DiffParams,
    loadId: number,
    payload: Awaited<ReturnType<typeof fetchManifest>>,
    mode: "replace" | "reconcile",
  ) => void;
  upsertFile: (
    entry: FileEntry,
    sourceParams: DiffParams,
    sourceLoadId: number,
    originalLazyReason: ManifestEntry["lazy"],
  ) => void;
  upsertFiles: (
    entries: FileEntry[],
    sourceParams: DiffParams,
    sourceLoadId: number,
    originalLazyReasonByKey: Record<string, ManifestEntry["lazy"]>,
  ) => void;
  currentHydratedLazyKeys: () => string[];
  directoryLabelForFileKey: (key: string) => string;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  addErrorToast: (title: string, error: unknown) => void;
};

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type BooleanMap = Record<string, boolean | undefined>;
type StringMap = Record<string, string | undefined>;
export type DiffStatusPlacement = "inline" | "top";
export type LoadedFilesStatus = {
  failed: number;
  loaded: number;
  total: number;
};
export type DiffStatus = {
  loadedFiles: LoadedFilesStatus | null;
  placement: DiffStatusPlacement;
  state: LoadState;
  text: string;
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
  const [fileState, setFileState] = createStore<{
    loadingFiles: BooleanMap;
    fileErrors: StringMap;
  }>({
    loadingFiles: {},
    fileErrors: {},
  });
  const [loadingRevision, setLoadingRevision] = createSignal(0);
  const [cacheId, setCacheId] = createSignal<string | null>(null);
  const [status, setStatus] = createSignal<DiffStatus>({
    loadedFiles: null,
    placement: "top",
    state: "idle",
    text: "Preparing diff...",
  });
  let toastedManifestErrorIdentity = "";
  let activeLoadId = 0;

  const nextLoadId = (): number => {
    activeLoadId += 1;
    return activeLoadId;
  };

  const loadIsCurrent = (loadId: number): boolean => activeLoadId === loadId;

  const bumpLoadingRevision = () => {
    setLoadingRevision((current) => current + 1);
  };

  const setLoadingFiles: ExpansionSetter = (updater) => {
    const next = updater(booleanMapSnapshot(fileState.loadingFiles));
    setFileState("loadingFiles", reconcile(next));
    bumpLoadingRevision();
  };

  const setFileErrors = (
    updater: (current: Record<string, string>) => Record<string, string>,
  ) => {
    const next = updater(stringMapSnapshot(fileState.fileErrors));
    setFileState("fileErrors", reconcile(next));
    bumpLoadingRevision();
  };

  const resetFileState = () => {
    setFileState("loadingFiles", reconcile({}));
    setFileState("fileErrors", reconcile({}));
    bumpLoadingRevision();
  };

  const currentParamsIdentity = (): string | null => {
    const diffParams = currentParams();
    if (diffParams === null) {
      return null;
    }
    return diffParamsIdentity(diffParams);
  };

  const replaceUrlForParams = (
    diffParams: DiffParams,
    viewMode = options.diffViewMode(),
  ) => {
    history.replaceState(
      {},
      "",
      `/?${appQuery(diffParams, viewMode, options.controls()).toString()}${window.location.hash}`,
    );
  };

  const replaceUrlForCurrentParams = (viewMode: DiffViewMode) => {
    const diffParams = currentParams();
    if (diffParams !== null) {
      replaceUrlForParams(diffParams, viewMode);
    }
  };

  const resetDiffState = (
    nextStatus: LoadState,
    nextStatusText: string,
    nextStatusPlacement: DiffStatusPlacement,
  ) => {
    batch(() => {
      options.clearLoadedDiff();
      options.resetViewState();
      resetFileState();
      setCacheId(null);
      setStatus({
        loadedFiles: null,
        placement: nextStatusPlacement,
        state: nextStatus,
        text: nextStatusText,
      });
    });
  };

  const clearCurrentParams = () => {
    nextLoadId();
    void queryClient.cancelQueries({ queryKey: ["manifest"] });
    void queryClient.cancelQueries({ queryKey: ["lazy-info"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
    setCurrentParams(null);
  };

  const failLoad = (message: string) => {
    clearCurrentParams();
    resetDiffState("error", message, "inline");
  };

  const cancelActiveDiffQueries = () => {
    void queryClient.cancelQueries({ queryKey: ["manifest"] });
    void queryClient.cancelQueries({ queryKey: ["lazy-info"] });
    void queryClient.cancelQueries({ queryKey: ["file-diff"] });
    void queryClient.cancelQueries({ queryKey: ["notebook-section"] });
  };

  const startDiff = (diffParams: DiffParams) => {
    const loadId = nextLoadId();
    setCurrentParams(null);
    cancelActiveDiffQueries();
    queryClient.removeQueries({ queryKey: manifestParamsQueryKey(diffParams) });
    queryClient.removeQueries({ queryKey: ["lazy-info"] });
    replaceUrlForParams(diffParams);
    toastedManifestErrorIdentity = "";
    resetDiffState("loading", "Loading diff...", "top");
    setCurrentParams(diffParams);
    void loadDiff(diffParams, loadId, "replace", []);
  };

  const refreshDiff = (diffParams: DiffParams, nextStatusText: string) => {
    const loadId = nextLoadId();
    const hydratedLazyKeys = options.currentHydratedLazyKeys();
    cancelActiveDiffQueries();
    queryClient.removeQueries({ queryKey: manifestParamsQueryKey(diffParams) });
    queryClient.removeQueries({ queryKey: ["lazy-info"] });
    replaceUrlForParams(diffParams);
    toastedManifestErrorIdentity = "";
    batch(() => {
      setCurrentParams(diffParams);
      resetFileState();
      setCacheId(null);
      setStatus({
        loadedFiles: null,
        placement: "top",
        state: "loading",
        text: nextStatusText,
      });
    });
    void loadDiff(diffParams, loadId, "reconcile", hydratedLazyKeys);
  };

  const selectedProjectIdOrIdle = (): ProjectId | null => {
    const projectId = options.selectedProjectId();
    if (projectId === null) {
      clearCurrentParams();
      resetDiffState("idle", "Choose a repo to load a diff.", "inline");
      return null;
    }
    return projectId;
  };

  async function loadDiff(
    diffParams: DiffParams,
    loadId: number,
    mode: "replace" | "reconcile",
    hydratedLazyKeys: string[],
  ) {
    const paramsIdentity = diffParamsIdentity(diffParams);
    try {
      const payload = await queryClient.fetchQuery({
        queryKey: manifestParamsQueryKey(diffParams),
        queryFn: async ({ signal }) => fetchManifest(diffParams, signal),
        retry: false,
        staleTime: 0,
      });
      if (!loadIsCurrent(loadId)) {
        return;
      }
      applyManifestPayload(
        diffParams,
        paramsIdentity,
        loadId,
        payload,
        mode,
        hydratedLazyKeys,
      );
    } catch (error) {
      if (!loadIsCurrent(loadId)) {
        return;
      }
      if (isCancelledError(error)) {
        return;
      }
      batch(() => {
        setStatus({
          loadedFiles: null,
          placement: "inline",
          state: "error",
          text: error instanceof Error ? error.message : "Failed to load diff.",
        });
      });
      const errorMessage =
        error instanceof Error ? error.message : "Failed to load diff.";
      console.error(`Failed to load diff: ${errorMessage}`);
      const toastIdentity = `${paramsIdentity}:${loadId}`;
      if (toastedManifestErrorIdentity !== toastIdentity) {
        toastedManifestErrorIdentity = toastIdentity;
        options.addErrorToast("Failed to load diff", error);
      }
    }
  }

  function applyManifestPayload(
    diffParams: DiffParams,
    paramsIdentity: string,
    loadId: number,
    payload: Awaited<ReturnType<typeof fetchManifest>>,
    mode: "replace" | "reconcile",
    hydratedLazyKeys: string[],
  ) {
    // This depth-first list is the load order for eager file diffs and lazy
    // placeholders. The UI later walks the stored manifest tree the same way.
    const manifestFiles = manifestFileEntriesFromTree(payload.tree);
    const lazyManifestFiles = manifestFiles.filter(
      (entry) => entry.lazy !== null,
    );
    const hydratedLazyKeySet = new Set(hydratedLazyKeys);
    const baseStatus = statusLabel(
      diffParams,
      payload.left_label,
      payload.right_label,
    );
    batch(() => {
      options.applyManifest(diffParams, loadId, payload, mode);
      resetFileState();
      setCacheId(payload.cache_id);
      setStatus({
        loadedFiles: {
          failed: 0,
          loaded: 0,
          total: manifestFiles.length,
        },
        placement: "inline",
        state: "loading",
        text: baseStatus,
      });
    });
    void hydrateManifestFiles(
      diffParams,
      loadId,
      manifestFiles,
      baseStatus,
      0,
      hydratedLazyKeySet,
      payload.cache_id,
    );
    void hydrateLazyInfo(
      diffParams,
      paramsIdentity,
      loadId,
      lazyManifestFiles,
      hydratedLazyKeySet,
      payload.cache_id,
    );
  }

  function hydrateLazyFiles(
    manifestFiles: ManifestEntry[],
    lazyInfoFiles: LazyInfoFile[],
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
        changed_lines: lazyInfoEntry.changed_lines,
        added_lines: lazyInfoEntry.added_lines,
        removed_lines: lazyInfoEntry.removed_lines,
        lazy: lazyInfoEntry.lazy,
      };
    });
  }

  async function hydrateLazyInfo(
    diffParams: DiffParams,
    paramsIdentity: string,
    loadId: number,
    manifestFiles: ManifestEntry[],
    hydratedLazyKeys: Set<string>,
    cacheId: string,
  ) {
    if (manifestFiles.length === 0) {
      return;
    }
    try {
      const result = await queryClient.fetchQuery({
        queryKey: lazyInfoParamsQueryKey(diffParams, cacheId),
        queryFn: async ({ signal }): Promise<LazyInfoQueryPayload> => ({
          diffParams,
          paramsIdentity,
          payload: await fetchLazyInfo(diffParams, cacheId, signal),
        }),
        staleTime: 0,
      });
      if (!loadIsCurrent(loadId)) {
        return;
      }
      const activeHydratedLazyKeys = new Set(hydratedLazyKeys);
      for (const key of options.currentHydratedLazyKeys()) {
        activeHydratedLazyKeys.add(key);
      }
      const mergedFiles = hydrateLazyFiles(
        manifestFiles,
        result.payload.files,
      ).filter((entry) => !activeHydratedLazyKeys.has(fileKey(entry)));
      const originalLazyReasonByKey: Record<string, ManifestEntry["lazy"]> = {};
      for (const file of mergedFiles) {
        const key = fileKey(file);
        const lazy = file.lazy;
        if (lazy === null) {
          throw new Error(`Lazy placeholder is missing lazy reason: ${key}.`);
        }
        if (lazy === undefined) {
          throw new Error(`Lazy placeholder is missing lazy reason: ${key}.`);
        }
        if (typeof lazy !== "string") {
          throw new Error(`Lazy placeholder is missing lazy reason: ${key}.`);
        }
        originalLazyReasonByKey[key] = lazy;
      }
      options.upsertFiles(
        mergedFiles,
        diffParams,
        loadId,
        originalLazyReasonByKey,
      );
    } catch (error) {
      if (!loadIsCurrent(loadId)) {
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
    loadId: number,
    manifestFiles: ManifestEntry[],
    baseStatus: string,
    initialLoadedFiles: number,
    hydratedLazyKeys: Set<string>,
    cacheId: string,
  ) {
    const pendingFiles = manifestFiles.filter((entry) =>
      shouldHydrateManifestEntry(entry, hydratedLazyKeys),
    );
    if (pendingFiles.length === 0) {
      if (loadIsCurrent(loadId)) {
        setStatus({
          loadedFiles: null,
          placement: "inline",
          state: "done",
          text: baseStatus,
        });
      }
      return;
    }
    const pin = getLinePinFromHash();
    let hasFailure = false;
    let toastedHydrationFailure = false;
    let loadedFiles = initialLoadedFiles;
    let failedDetailFiles = 0;

    for (const entry of pendingFiles) {
      if (!loadIsCurrent(loadId)) {
        return;
      }
      const key = fileKey(entry);
      const originalLazyReason = entry.lazy;
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
        if (tracksSlowFileDiff(diffParams.engine)) {
          const startedAt = Date.now();
          const updateSlowStatus = () => {
            if (!loadIsCurrent(loadId)) {
              clearSlowStatus();
              return;
            }
            setStatus({
              loadedFiles: {
                failed: failedDetailFiles,
                loaded: loadedFiles,
                total: manifestFiles.length,
              },
              placement: "top",
              state: "loading",
              text: `${baseStatus} · ${slowFileDiffDetail(entry, Date.now() - startedAt)}`,
            });
          };
          slowTimeout = window.setTimeout(() => {
            updateSlowStatus();
            slowInterval = window.setInterval(updateSlowStatus, 1000);
          }, SLOW_FILE_DIFF_MS);
        }
        const queryKey = fileDiffQueryKey(diffParams, entry, cacheId);
        queryClient.removeQueries({ queryKey });
        const hydrated = await queryClient.fetchQuery({
          queryKey,
          queryFn: ({ signal }) =>
            fetchFileDiff(diffParams, entry, cacheId, signal),
          retry: false,
          staleTime: 0,
        });
        clearSlowStatus();
        if (!loadIsCurrent(loadId)) {
          return;
        }
        // Hydrated FileEntry construction is allowed only from
        // /api/file-diff. Do not merge in the /api/manifest entry.
        const nextEntry =
          originalLazyReason === null
            ? hydrated
            : {
                ...hydrated,
                lazy_reason: originalLazyReason,
              };
        const nextKey = fileKey(nextEntry);
        const shouldOpenPinnedFile =
          pin !== null && fileMatchesLinePin(nextEntry, pin);
        loadedFiles += 1;
        batch(() => {
          options.upsertFile(nextEntry, diffParams, loadId, originalLazyReason);
          options.setDirectoryExpansion((current) => {
            const directory = options.directoryLabelForFileKey(nextKey);
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
              shouldOpenPinnedFile ? { ...current, [nextKey]: true } : current,
              nextEntry,
              nextKey,
            ),
          );
          setStatus({
            loadedFiles: {
              failed: failedDetailFiles,
              loaded: loadedFiles,
              total: manifestFiles.length,
            },
            placement: "inline",
            state: "loading",
            text: baseStatus,
          });
        });
      } catch (error) {
        clearSlowStatus();
        if (!loadIsCurrent(loadId)) {
          return;
        }
        if (isCancelledError(error)) {
          continue;
        }
        hasFailure = true;
        loadedFiles += 1;
        failedDetailFiles += 1;
        batch(() => {
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
          options.upsertFile(
            failedEntry,
            diffParams,
            loadId,
            originalLazyReason,
          );
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
          setStatus({
            loadedFiles: {
              failed: failedDetailFiles,
              loaded: loadedFiles,
              total: manifestFiles.length,
            },
            placement: "inline",
            state: "error",
            text: baseStatus,
          });
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
    if (loadIsCurrent(loadId)) {
      setStatus({
        loadedFiles: hasFailure
          ? {
              failed: failedDetailFiles,
              loaded: loadedFiles,
              total: manifestFiles.length,
            }
          : null,
        placement: "inline",
        state: hasFailure ? "error" : "done",
        text: baseStatus,
      });
    }
  }

  const hydrateFile = (file: RenderedFileEntry) => {
    const diffParams = currentParams();
    if (diffParams === null) {
      return;
    }
    if (file.sourceLoadId !== activeLoadId) {
      return;
    }
    const loadId = activeLoadId;
    const activeCacheId = cacheId();
    if (activeCacheId === null) {
      throw new Error("Cannot hydrate file without a cache id.");
    }
    const key = fileKey(file);
    const originalLazyReason = lazyOriginalReasonForHydration(file);
    const lazyFetchEntry: ManifestEntry = {
      file_kind: file.file_kind,
      left_path: file.left_path,
      right_path: file.right_path,
      lazy: originalLazyReason,
    };
    batch(() => {
      setLoadingFiles((current) => ({
        ...current,
        [key]: true,
      }));
      setFileErrors((current) => ({
        ...current,
        [key]: "",
      }));
    });
    void (async () => {
      try {
        const queryKey = fileDiffQueryKey(
          diffParams,
          lazyFetchEntry,
          activeCacheId,
        );
        queryClient.removeQueries({ queryKey });
        const hydrated = await queryClient.fetchQuery({
          queryKey,
          queryFn: ({ signal }) =>
            fetchFileDiff(
              diffParams,
              lazyFetchEntry,
              activeCacheId,
              signal,
              MANUAL_FILE_DIFF_TIMEOUT_MS,
            ),
          retry: false,
          staleTime: 0,
        });
        if (!loadIsCurrent(loadId)) {
          return;
        }
        const nextEntry =
          originalLazyReason === null
            ? hydrated
            : {
                ...hydrated,
                lazy_reason: originalLazyReason,
              };
        options.upsertFile(nextEntry, diffParams, loadId, originalLazyReason);
      } catch (error) {
        if (!loadIsCurrent(loadId)) {
          return;
        }
        if (isCancelledError(error)) {
          return;
        }
        setFileErrors((current) => ({
          ...current,
          [key]:
            error instanceof Error
              ? error.message
              : "Failed to load file diff.",
        }));
      } finally {
        if (!loadIsCurrent(loadId)) {
          return;
        }
        setLoadingFiles((current) => ({
          ...current,
          [key]: false,
        }));
      }
    })();
  };

  const loadAgainstHead = (selectedEngine: DiffEngine = engine()) => {
    const projectId = selectedProjectIdOrIdle();
    if (projectId === null) {
      return;
    }
    // Keep controls reflecting the attempted load even when validation below
    // fails. That lets the user fix the visible draft instead of losing edits.
    options.setControls((current) =>
      current === null ? current : { ...current, tab: "head", mode: "head" },
    );
    const diffParams: HeadDiffParams = {
      project_id: projectId,
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
    options.setControls((current) =>
      current === null
        ? current
        : {
            ...current,
            tab: "preset",
            mode: "preset",
            presetType,
            preset,
          },
    );
    const diffParams: PresetDiffParams = {
      project_id: presetType,
      engine: selectedEngine,
      mode: "preset",
      preset_subset: preset,
    };
    startDiff(diffParams);
  };

  const loadRefs = (
    left: string,
    right: string,
    selectedEngine: DiffEngine = engine(),
  ) => {
    const projectId = selectedProjectIdOrIdle();
    if (projectId === null) {
      return;
    }
    const trimmedLeft = left.trim();
    const trimmedRight = right.trim();
    options.setControls((current) =>
      current === null
        ? current
        : { ...current, tab: "refs", mode: "refs", left, right },
    );
    if (trimmedLeft.length === 0 || trimmedRight.length === 0) {
      failLoad("Enter both refs to compare them.");
      return;
    }
    const diffParams: RefsDiffParams = {
      project_id: projectId,
      engine: selectedEngine,
      mode: "refs",
      left: trimmedLeft,
      right: trimmedRight,
    };
    startDiff(diffParams);
  };

  // Branch-review loading validates tagged control state and sends that
  // structure through the API. Remote and branch are not joined in frontend.
  const loadBranchReview = (
    baseSelection: BranchSelectionDraft,
    reviewSelection: BranchSelectionDraft,
    selectedEngine: DiffEngine = engine(),
    controlsPatch: Partial<ControlsState> = {},
  ) => {
    const projectId = selectedProjectIdOrIdle();
    if (projectId === null) {
      return;
    }
    options.setControls((current) =>
      current === null
        ? current
        : {
            ...current,
            tab: "branch-review",
            mode: "branch-review",
            baseSelection,
            reviewSelection,
            ...controlsPatch,
          },
    );
    if (
      baseSelection.state === "missing" ||
      reviewSelection.state === "missing"
    ) {
      failLoad("Pick branches to compare.");
      return;
    }
    const selectedBase = baseSelection.value;
    const selectedReview = reviewSelection.value;
    if (selectedBase.source === "remote" && !selectedBase.remote.trim()) {
      failLoad("Pick a base remote.");
      return;
    }
    if (!selectedBase.branch.trim()) {
      failLoad("Pick a base branch.");
      return;
    }
    if (selectedReview.source === "remote" && !selectedReview.remote.trim()) {
      failLoad("Pick a branch remote.");
      return;
    }
    if (!selectedReview.branch.trim()) {
      failLoad("Pick a branch to compare against the base branch.");
      return;
    }
    const diffParams: BranchReviewDiffParams = {
      project_id: projectId,
      engine: selectedEngine,
      mode: "branch-review",
      base_selection: {
        ...selectedBase,
        branch: selectedBase.branch.trim(),
      },
      review_selection: {
        ...selectedReview,
        branch: selectedReview.branch.trim(),
      },
    };
    startDiff(diffParams);
  };

  const loadPullRequest = (
    prepared: PreparedPullRequest,
    selectedEngine: DiffEngine = engine(),
  ) => {
    setEngine(selectedEngine);
    const baseSelection: BranchSelectionDraft = {
      state: "selected",
      value: {
        source: "remote",
        remote: prepared.base_branch.remote,
        branch: prepared.base_branch.branch,
      },
    };
    const reviewSelection: BranchSelectionDraft = {
      state: "selected",
      value: {
        source: "remote",
        remote: prepared.review_branch.remote,
        branch: prepared.review_branch.branch,
      },
    };
    loadBranchReview(baseSelection, reviewSelection, selectedEngine, {
      tab: "pull-request",
      pullRequestUrl: prepared.pull_request_url,
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
      if (
        nextControls.baseSelection.state === "missing" ||
        nextControls.reviewSelection.state === "missing"
      ) {
        failLoad("Pick branches to compare.");
        return;
      }
      loadBranchReview(
        nextControls.baseSelection,
        nextControls.reviewSelection,
        nextEngine,
        nextControls.tab === "pull-request"
          ? {
              tab: "pull-request",
              pullRequestUrl: nextControls.pullRequestUrl,
            }
          : {},
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
    refreshDiff(diffParams, "Refreshing diff...");
  };

  const loadEngine = (nextEngine: DiffEngine) => {
    setEngine(nextEngine);
    const diffParams = currentParams();
    if (diffParams !== null) {
      const nextDiffParams: DiffParams = {
        ...diffParams,
        engine: nextEngine,
      };
      refreshDiff(nextDiffParams, "Switching diff engine...");
    }
  };

  return {
    engine,
    currentParams,
    currentParamsIdentity,
    loadingFiles: fileState.loadingFiles,
    fileErrors: fileState.fileErrors,
    loadingRevision,
    status,
    cacheId,
    resetDiffState,
    clearCurrentParams,
    loadAgainstHead,
    loadPreset,
    loadRefs,
    loadBranchReview,
    loadPullRequest,
    loadInitialControls,
    hydrateFile,
    reloadDiff,
    loadEngine,
    replaceUrlForCurrentParams,
  };
}

export type DiffResources = ReturnType<typeof createDiffResources>;

function tracksSlowFileDiff(engine: DiffEngine): boolean {
  switch (engine) {
    case "difftastic":
    case "gumtree":
      return true;
    case "dirdiff":
    case "git":
      return false;
    default:
      return engine satisfies never;
  }
}
