import {
  batch,
  createEffect,
  createMemo,
  createSelector,
  createSignal,
  untrack,
} from "solid-js";
import { createStore, reconcile, unwrap } from "solid-js/store";
import type {
  DiffParams,
  FileEntry,
  LazyReason,
  ManifestTreeEntry,
  RepoManifestPayload,
  Summary,
} from "../api";
import {
  type FileTreeDirectoryNode,
  type RenderedFileEntry,
  addHydratedNotebookSummary,
  fileTreeFromManifestTree,
  fileEntryIsHydrated,
  fileKey,
  manifestDirectoryLabelsByFileKey,
  manifestFileEntriesFromTree,
} from "../fileUtils";
import { richPreloadFileIdsForFileId } from "../hunkNavigation";
import { diffParamsIdentity } from "./diffParams";

function stringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
}

function booleanRecordsEqual(
  left: Record<string, boolean>,
  right: Record<string, boolean>,
): boolean {
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }
  for (const key of leftKeys) {
    if (right[key] !== left[key]) {
      return false;
    }
  }
  return true;
}

type BooleanMap = Record<string, boolean | undefined>;
type DiffDataState = {
  // Set when a fetched manifest is applied. File hydration passes the same id
  // into upsertFile(); summary() uses it to ignore hydrated notebook counts
  // from older in-flight loads.
  loadId: number;
  // UI copy of the fetched /api/manifest tree. createDiffResources flattens
  // the same response depth-first before applyManifest() to choose
  // /api/file-diff and /api/lazy-info fetch order; this stored copy is read
  // only for rendering and expansion lookups.
  manifestTree: ManifestTreeEntry[];
  // Results from /api/file-diff and /api/lazy-info, keyed by fileKey().
  // upsertFile()/upsertFiles() write it as fetches finish; displayFiles() and
  // displayFileTree() read it without changing depth-first manifest order.
  filesByKey: Record<string, RenderedFileEntry | undefined>;
  // Summary from the fetched /api/manifest response. summary() starts here and
  // adds client-side counts from hydrated notebook files in filesByKey.
  baseSummary: Summary;
};
export type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;

function emptyDiffDataState(): DiffDataState {
  return {
    loadId: 0,
    manifestTree: [],
    filesByKey: {},
    baseSummary: {
      changed_files: 0,
      added_files: 0,
      removed_files: 0,
      updated_files: 0,
      added_lines: 0,
      removed_lines: 0,
      skipped_files: 0,
    },
  };
}

function booleanMapSnapshot(map: BooleanMap): Record<string, boolean> {
  const values = unwrap(map);
  const snapshot: Record<string, boolean> = {};
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) {
      snapshot[key] = value;
    }
  }
  return snapshot;
}

function stringSetSnapshot(map: BooleanMap): string[] {
  return Object.entries(unwrap(map)).flatMap(([key, value]) =>
    value === true ? [key] : [],
  );
}

function renderedFileEntry(
  entry: FileEntry,
  sourceParams: DiffParams,
  sourceLoadId: number,
  originalLazyReason: LazyReason | null,
): RenderedFileEntry {
  const renderedKey = fileKey(entry);
  return {
    ...entry,
    renderedKey,
    sourceParams,
    sourceParamsIdentity: diffParamsIdentity(sourceParams),
    sourceEngine: sourceParams.engine,
    sourceLoadId,
    originalLazyReason,
  };
}

function renderedFileIsHydratedLazy(file: RenderedFileEntry): boolean {
  if (file.originalLazyReason === null) {
    return false;
  }
  return fileEntryIsHydrated(file);
}

function renderedFileIsPendingLazy(file: RenderedFileEntry): boolean {
  if (file.originalLazyReason === null) {
    return false;
  }
  return !fileEntryIsHydrated(file);
}

/**
 * Owns client-only diff presentation state.
 *
 * This primitive stores client-side diff presentation state: display-file
 * ordering, directory/file expansion, forced rich rendering, active hunk file
 * id, and virtualization bookkeeping.
 *
 * It does not fetch data, build requests, write the URL, or attach DOM
 * listeners. Callers mutate it through named actions so those workflows remain
 * explicit in App, diff resources, or navigation.
 */
export function createDiffUiState() {
  const [diffData, setDiffData] =
    createStore<DiffDataState>(emptyDiffDataState());
  const [viewState, setViewState] = createStore<{
    directoryExpansion: BooleanMap;
    fileExpansion: BooleanMap;
    forcedRichFileIds: BooleanMap;
    virtualizedFileIds: BooleanMap;
  }>({
    directoryExpansion: {},
    fileExpansion: {},
    forcedRichFileIds: {},
    virtualizedFileIds: {},
  });
  const [activeHunkFileId, setActiveHunkFileId] = createSignal<string | null>(
    null,
  );
  const isActiveHunkFileId = createSelector(activeHunkFileId);
  const [layoutRevision, setLayoutRevision] = createSignal(0);
  const [virtualizationRevision, setVirtualizationRevision] = createSignal(0);

  const bumpLayoutRevision = () => {
    setLayoutRevision((current) => current + 1);
  };

  const bumpVirtualizationRevision = () => {
    setVirtualizationRevision((current) => current + 1);
  };

  const setForcedRichPreloadIds = (nextIds: string[]) => {
    const currentIds = stringSetSnapshot(viewState.forcedRichFileIds);
    if (stringArraysEqual(currentIds, nextIds)) {
      return;
    }
    const nextMap: Record<string, boolean> = {};
    for (const id of nextIds) {
      nextMap[id] = true;
    }
    setViewState("forcedRichFileIds", reconcile(nextMap));
    bumpLayoutRevision();
  };

  const forceRichFileId = (fileId: string) => {
    const currentlyForced = untrack(
      () => viewState.forcedRichFileIds[fileId] === true,
    );
    if (currentlyForced) {
      return;
    }
    setViewState("forcedRichFileIds", fileId, true);
    bumpLayoutRevision();
  };

  const setFileVirtualized = (fileId: string, virtualized: boolean) => {
    const currentVirtualized = untrack(
      () => viewState.virtualizedFileIds[fileId] === true,
    );
    if (currentVirtualized === virtualized) {
      return;
    }
    if (virtualized) {
      setViewState("virtualizedFileIds", fileId, true);
      bumpVirtualizationRevision();
      return;
    }
    setViewState("virtualizedFileIds", fileId, undefined);
    bumpVirtualizationRevision();
  };

  const displayFiles = createMemo(() => {
    const files: RenderedFileEntry[] = [];
    // Main diff cards follow the same depth-first manifest walk used for
    // fetching, then skip entries whose file payload has not arrived yet.
    for (const entry of manifestFileEntriesFromTree(diffData.manifestTree)) {
      const key = fileKey(entry);
      const file = diffData.filesByKey[key];
      if (file !== undefined) {
        files.push(file);
      }
    }
    return files;
  });

  const displayFileTree = createMemo(() =>
    fileTreeFromManifestTree(diffData.manifestTree, diffData.filesByKey),
  );

  const directoryLabelByFileKey = createMemo(() =>
    manifestDirectoryLabelsByFileKey(diffData.manifestTree),
  );

  const summary = createMemo(() => {
    let nextSummary = diffData.baseSummary;
    for (const file of displayFiles()) {
      if (file.sourceLoadId === diffData.loadId) {
        nextSummary = addHydratedNotebookSummary(nextSummary, file);
      }
    }
    return nextSummary;
  });

  /**
   * Seed the rich-render preload window until navigation chooses a concrete
   * hunk/file target. Once a user/navigation action forces rich files, that
   * explicit choice wins until the view state is reset.
   */
  createEffect(() => {
    if (stringSetSnapshot(viewState.forcedRichFileIds).length > 0) {
      return;
    }
    setForcedRichPreloadIds(richPreloadFileIdsForFileId(null, displayFiles()));
  });

  const clearLoadedDiff = () => {
    setDiffData(emptyDiffDataState());
    bumpLayoutRevision();
  };

  const applyManifest = (
    diffParams: DiffParams,
    loadId: number,
    payload: RepoManifestPayload,
    mode: "replace" | "reconcile",
  ) => {
    const manifestFiles = manifestFileEntriesFromTree(payload.tree);
    const activeKeys = new Set(manifestFiles.map((entry) => fileKey(entry)));
    const lazyKeys = new Set(
      manifestFiles.flatMap((entry) =>
        entry.lazy === null ? [] : [fileKey(entry)],
      ),
    );
    batch(() => {
      setDiffData("loadId", loadId);
      setDiffData("baseSummary", payload.summary);
      setDiffData("manifestTree", reconcile(payload.tree));
      if (mode === "replace") {
        setDiffData("filesByKey", reconcile({}));
      } else {
        for (const key of Object.keys(unwrap(diffData.filesByKey))) {
          const currentFile = diffData.filesByKey[key];
          if (currentFile === undefined) {
            continue;
          }
          if (!activeKeys.has(key)) {
            setDiffData("filesByKey", key, undefined);
            continue;
          }
          if (!lazyKeys.has(key)) {
            if (renderedFileIsPendingLazy(currentFile)) {
              setDiffData("filesByKey", key, undefined);
            }
            continue;
          }
          if (renderedFileIsHydratedLazy(currentFile)) {
            continue;
          }
          setDiffData("filesByKey", key, undefined);
        }
      }
      bumpLayoutRevision();
    });
  };

  const upsertFile = (
    entry: FileEntry,
    sourceParams: DiffParams,
    sourceLoadId: number,
    originalLazyReason: LazyReason | null,
  ) => {
    const file = renderedFileEntry(
      entry,
      sourceParams,
      sourceLoadId,
      originalLazyReason,
    );
    batch(() => {
      setDiffData("filesByKey", file.renderedKey, file);
      bumpLayoutRevision();
    });
  };

  const upsertFiles = (
    entries: FileEntry[],
    sourceParams: DiffParams,
    sourceLoadId: number,
    originalLazyReasonByKey: Record<string, LazyReason | null>,
  ) => {
    batch(() => {
      for (const entry of entries) {
        const key = fileKey(entry);
        const originalLazyReason = originalLazyReasonByKey[key];
        if (originalLazyReason === undefined) {
          throw new Error(`Missing original lazy reason for ${key}.`);
        }
        upsertFile(entry, sourceParams, sourceLoadId, originalLazyReason);
      }
    });
  };

  const currentHydratedLazyKeys = (): string[] =>
    displayFiles().flatMap((file) =>
      renderedFileIsHydratedLazy(file) ? [file.renderedKey] : [],
    );

  const setDirectoryExpansion: ExpansionSetter = (updater) => {
    const current = booleanMapSnapshot(viewState.directoryExpansion);
    const next = updater(current);
    if (booleanRecordsEqual(current, next)) {
      return;
    }
    setViewState("directoryExpansion", reconcile(next));
    bumpLayoutRevision();
  };

  const setFileExpansion: ExpansionSetter = (updater) => {
    const current = booleanMapSnapshot(viewState.fileExpansion);
    const next = updater(current);
    if (booleanRecordsEqual(current, next)) {
      return;
    }
    setViewState("fileExpansion", reconcile(next));
    bumpLayoutRevision();
  };

  const resetViewState = () => {
    batch(() => {
      setViewState("directoryExpansion", reconcile({}));
      setViewState("fileExpansion", reconcile({}));
      setViewState("forcedRichFileIds", reconcile({}));
      setViewState("virtualizedFileIds", reconcile({}));
      setActiveHunkFileId(null);
      bumpLayoutRevision();
    });
  };

  const setAllFilesExpanded = (expanded: boolean) => {
    const currentFiles = displayFiles();
    const collectDirectoryLabels = (
      entries: ReturnType<typeof displayFileTree>,
    ): string[] =>
      entries.flatMap((entry) => {
        if (entry.type === "file") {
          return [];
        }
        return [entry.label, ...collectDirectoryLabels(entry.entries)];
      });
    const directories = collectDirectoryLabels(displayFileTree());
    batch(() => {
      setViewState(
        "directoryExpansion",
        reconcile(
          Object.fromEntries(directories.map((label) => [label, expanded])),
        ),
      );
      setViewState(
        "fileExpansion",
        reconcile(
          Object.fromEntries(
            currentFiles.map((file) => [fileKey(file), expanded]),
          ),
        ),
      );
      bumpLayoutRevision();
    });
  };

  const openFileExpansion = (file: FileEntry) => {
    const key = fileKey(file);
    const directory = directoryLabelByFileKey()[key];
    if (directory === undefined) {
      throw new Error(`Missing directory label for ${key}.`);
    }
    batch(() => {
      setViewState("directoryExpansion", directory, true);
      setViewState("fileExpansion", key, true);
      bumpLayoutRevision();
    });
  };

  const directoryLabelForFileKey = (key: string): string => {
    const directory = directoryLabelByFileKey()[key];
    if (directory === undefined) {
      throw new Error(`Missing directory label for ${key}.`);
    }
    return directory;
  };

  const openTreeDirectoryExpansion = (directory: FileTreeDirectoryNode) => {
    batch(() => {
      setViewState("directoryExpansion", directory.label, true);
      for (const file of directory.files) {
        setViewState("fileExpansion", fileKey(file), true);
      }
      bumpLayoutRevision();
    });
  };

  return {
    summary,
    clearLoadedDiff,
    applyManifest,
    upsertFile,
    upsertFiles,
    currentHydratedLazyKeys,
    directoryExpansion: viewState.directoryExpansion,
    setDirectoryExpansion,
    fileExpansion: viewState.fileExpansion,
    setFileExpansion,
    isForcedRichFileId: (fileId: string) =>
      viewState.forcedRichFileIds[fileId] === true,
    setForcedRichPreloadIds,
    forceRichFileId,
    activeHunkFileId,
    isActiveHunkFileId,
    setActiveHunkFileId,
    isFileVirtualized: (fileId: string) =>
      viewState.virtualizedFileIds[fileId] === true,
    setFileVirtualized,
    layoutRevision,
    virtualizationRevision,
    displayFiles,
    displayFileTree,
    directoryLabelForFileKey,
    resetViewState,
    setAllFilesExpanded,
    openFileExpansion,
    openTreeDirectoryExpansion,
  };
}

export type DiffUiState = ReturnType<typeof createDiffUiState>;
