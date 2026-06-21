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
  type LoadState,
  type RenderedFileEntry,
  entryDirectoryLabel,
  fileDiffQueryKey,
  fileKey,
  fileMatchesLinePin,
} from "../fileUtils";
import { getLinePinFromHash } from "../linePins";
import { queryClient } from "../queryClient";
import {
  type LazyInfoQueryPayload,
  diffParamsIdentity,
  lazyInfoParamsQueryKey,
  manifestParamsQueryKey,
} from "./diffParams";

const builtinSides = new Set(["head", "index", "worktree"]);
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
  selectedRepoId: Accessor<RepoId | null>;
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
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  refChoices: () => RefChoices;
  addErrorToast: (title: string, error: unknown) => void;
};

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type BooleanMap = Record<string, boolean | undefined>;
type StringMap = Record<string, string | undefined>;

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
  const [status, setStatus] = createSignal<LoadState>("idle");
  const [statusText, setStatusText] = createSignal("Preparing diff...");
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
      options.clearLoadedDiff();
      options.resetViewState();
      resetFileState();
      setStatus(nextStatus);
      setStatusText(nextStatusText);
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
    resetDiffState("error", message);
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
    queryClient.removeQueries({ queryKey: lazyInfoParamsQueryKey(diffParams) });
    replaceUrlForParams(diffParams);
    toastedManifestErrorIdentity = "";
    resetDiffState("loading", "Loading diff...");
    setCurrentParams(diffParams);
    void loadDiff(diffParams, loadId, "replace", []);
  };

  const refreshDiff = (diffParams: DiffParams, nextStatusText: string) => {
    const loadId = nextLoadId();
    const hydratedLazyKeys = options.currentHydratedLazyKeys();
    cancelActiveDiffQueries();
    queryClient.removeQueries({ queryKey: manifestParamsQueryKey(diffParams) });
    queryClient.removeQueries({ queryKey: lazyInfoParamsQueryKey(diffParams) });
    replaceUrlForParams(diffParams);
    toastedManifestErrorIdentity = "";
    batch(() => {
      setCurrentParams(diffParams);
      resetFileState();
      setStatus("loading");
      setStatusText(nextStatusText);
    });
    void loadDiff(diffParams, loadId, "reconcile", hydratedLazyKeys);
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
        setStatus("error");
        setStatusText(
          error instanceof Error ? error.message : "Failed to load diff.",
        );
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
    const order = Object.fromEntries(
      payload.files.map((entry, index) => [fileKey(entry), index]),
    );
    const lazyManifestFiles = payload.files.filter(
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
      setStatusText(loadedStatusLabel(baseStatus, 0, 0));
    });
    void hydrateManifestFiles(
      diffParams,
      loadId,
      payload.files,
      baseStatus,
      0,
      hydratedLazyKeySet,
    );
    void hydrateLazyInfo(
      diffParams,
      paramsIdentity,
      loadId,
      lazyManifestFiles,
      order,
      hydratedLazyKeySet,
    );
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
    loadId: number,
    manifestFiles: ManifestEntry[],
    order: Record<string, number>,
    hydratedLazyKeys: Set<string>,
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
        order,
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
  ) {
    const pendingFiles = manifestFiles.filter((entry) =>
      shouldHydrateManifestEntry(entry, hydratedLazyKeys),
    );
    if (pendingFiles.length === 0) {
      if (loadIsCurrent(loadId)) {
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
        if (diffParams.engine === "difftastic") {
          const startedAt = Date.now();
          const updateSlowStatus = () => {
            if (!loadIsCurrent(loadId)) {
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
        const queryKey = fileDiffQueryKey(diffParams, entry);
        queryClient.removeQueries({ queryKey });
        const hydrated = await queryClient.fetchQuery({
          queryKey,
          queryFn: ({ signal }) => fetchFileDiff(diffParams, entry, signal),
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
              shouldOpenPinnedFile ? { ...current, [nextKey]: true } : current,
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
    if (loadIsCurrent(loadId)) {
      setStatus(hasFailure ? "error" : "done");
      if (!hasFailure) {
        setStatusText(baseStatus);
      }
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
        const queryKey = fileDiffQueryKey(diffParams, lazyFetchEntry);
        queryClient.removeQueries({ queryKey });
        const hydrated = await queryClient.fetchQuery({
          queryKey,
          queryFn: ({ signal }) =>
            fetchFileDiff(
              diffParams,
              lazyFetchEntry,
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
    statusText,
    resetDiffState,
    clearCurrentParams,
    loadAgainstHead,
    loadPreset,
    loadRefs,
    loadBranchReview,
    loadInitialControls,
    hydrateFile,
    reloadDiff,
    loadEngine,
    replaceUrlForCurrentParams,
  };
}

export type DiffResources = ReturnType<typeof createDiffResources>;
