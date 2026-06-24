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
  ManifestEntry,
  RepoManifestPayload,
} from "../api";
import {
  type FileGroup,
  type LoadedDiff,
  type RenderedFileEntry,
  addHydratedNotebookSummary,
  entryDirectoryLabel,
  fileEntryIsHydrated,
  fileKey,
  groupFilesByLabel,
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
type DiffDocumentState = {
  params: DiffParams | null;
  loadId: number;
  order: string[];
  fileOrder: Record<string, number>;
  filesByKey: Record<string, RenderedFileEntry | undefined>;
  lazyFilesByKey: Record<string, ManifestEntry | undefined>;
  baseSummary: LoadedDiff["summary"];
};
export type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;

function emptyDocumentState(): DiffDocumentState {
  return {
    params: null,
    loadId: 0,
    order: [],
    fileOrder: {},
    filesByKey: {},
    lazyFilesByKey: {},
    baseSummary: {
      changed_files: 0,
      added_files: 0,
      removed_files: 0,
      updated_files: 0,
      changed_lines: 0,
      modified_lines: 0,
      added_lines: 0,
      removed_lines: 0,
      moved_lines: 0,
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

function manifestOrder(payload: RepoManifestPayload): {
  order: string[];
  fileOrder: Record<string, number>;
  lazyFilesByKey: Record<string, ManifestEntry>;
} {
  const order: string[] = [];
  const fileOrder: Record<string, number> = {};
  const lazyFilesByKey: Record<string, ManifestEntry> = {};
  payload.files.forEach((entry, index) => {
    const key = fileKey(entry);
    order.push(key);
    fileOrder[key] = index;
    if (entry.lazy !== null) {
      lazyFilesByKey[key] = entry;
    }
  });
  return { order, fileOrder, lazyFilesByKey };
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
 * This primitive stores the LoadedDiff snapshot and all UI state derived from
 * rendering it: display-file ordering, directory/file expansion, forced rich
 * rendering, active hunk file id, and virtualization bookkeeping.
 *
 * It does not fetch data, build requests, write the URL, or attach DOM
 * listeners. Callers mutate it through named actions so those workflows remain
 * explicit in App, diff resources, or navigation.
 */
export function createDiffUiState() {
  const [document, setDocument] =
    createStore<DiffDocumentState>(emptyDocumentState());
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
    for (const key of document.order) {
      const file = document.filesByKey[key];
      if (file !== undefined) {
        files.push(file);
      }
    }
    return files;
  });

  const lazyFiles = createMemo(() => {
    const files: ManifestEntry[] = [];
    for (const key of document.order) {
      const entry = document.lazyFilesByKey[key];
      if (entry !== undefined) {
        files.push(entry);
      }
    }
    return files;
  });

  const summary = createMemo(() => {
    let nextSummary = document.baseSummary;
    for (const file of displayFiles()) {
      if (file.sourceLoadId === document.loadId) {
        nextSummary = addHydratedNotebookSummary(nextSummary, file);
      }
    }
    return nextSummary;
  });

  const loadedDiff = createMemo(() => {
    const params = document.params;
    if (params === null) {
      return null;
    }
    return {
      params,
      files: displayFiles(),
      lazyFiles: lazyFiles(),
      fileOrder: document.fileOrder,
      summary: summary(),
    };
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
    setDocument(emptyDocumentState());
    bumpLayoutRevision();
  };

  const applyManifest = (
    diffParams: DiffParams,
    loadId: number,
    payload: RepoManifestPayload,
    mode: "replace" | "reconcile",
  ) => {
    const next = manifestOrder(payload);
    const activeKeys = new Set(next.order);
    const lazyKeys = new Set(Object.keys(next.lazyFilesByKey));
    batch(() => {
      setDocument("params", diffParams);
      setDocument("loadId", loadId);
      setDocument("baseSummary", payload.summary);
      setDocument("order", next.order);
      setDocument("fileOrder", reconcile(next.fileOrder));
      setDocument("lazyFilesByKey", reconcile(next.lazyFilesByKey));
      if (mode === "replace") {
        setDocument("filesByKey", reconcile({}));
      } else {
        for (const key of Object.keys(unwrap(document.filesByKey))) {
          const currentFile = document.filesByKey[key];
          if (currentFile === undefined) {
            continue;
          }
          if (!activeKeys.has(key)) {
            setDocument("filesByKey", key, undefined);
            continue;
          }
          if (!lazyKeys.has(key)) {
            if (renderedFileIsPendingLazy(currentFile)) {
              setDocument("filesByKey", key, undefined);
            }
            continue;
          }
          if (renderedFileIsHydratedLazy(currentFile)) {
            continue;
          }
          setDocument("filesByKey", key, undefined);
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
      setDocument("filesByKey", file.renderedKey, file);
      if (originalLazyReason !== null && fileEntryIsHydrated(file)) {
        setDocument("lazyFilesByKey", file.renderedKey, undefined);
      }
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
    const groups = [...groupFilesByLabel(currentFiles).keys()];
    batch(() => {
      setViewState(
        "directoryExpansion",
        reconcile(Object.fromEntries(groups.map((label) => [label, expanded]))),
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
    const directory = entryDirectoryLabel(file);
    const key = fileKey(file);
    batch(() => {
      setViewState("directoryExpansion", directory, true);
      setViewState("fileExpansion", key, true);
      bumpLayoutRevision();
    });
  };

  const openDirectoryExpansion = (group: FileGroup) => {
    batch(() => {
      setViewState("directoryExpansion", group.label, true);
      for (const file of group.files) {
        setViewState("fileExpansion", fileKey(file), true);
      }
      bumpLayoutRevision();
    });
  };

  return {
    loadedDiff,
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
    resetViewState,
    setAllFilesExpanded,
    openFileExpansion,
    openDirectoryExpansion,
  };
}

export type DiffUiState = ReturnType<typeof createDiffUiState>;
