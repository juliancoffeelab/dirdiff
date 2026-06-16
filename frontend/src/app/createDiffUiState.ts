import {
  batch,
  createEffect,
  createMemo,
  createSignal,
  type Setter,
} from "solid-js";
import type { FileEntry } from "../api";
import {
  type FileGroup,
  type LoadedDiff,
  entryDirectoryLabel,
  fileKey,
  fileOrderIndex,
  groupFilesByLabel,
} from "../model";
import { richPreloadFileIdsForFileId } from "../hunkNavigation";

function stringArraysEqual(left: string[], right: string[]): boolean {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((value, index) => value === right[index]);
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
  const [loadedDiff, setLoadedDiff] = createSignal<LoadedDiff | null>(null);
  const [directoryExpansion, setDirectoryExpansion] = createSignal<
    Record<string, boolean>
  >({});
  const [fileExpansion, setFileExpansion] = createSignal<
    Record<string, boolean>
  >({});
  const [forcedRichFileIds, setForcedRichFileIds] = createSignal<string[]>([]);
  const [activeHunkFileId, setActiveHunkFileId] = createSignal<string | null>(
    null,
  );
  const [virtualizedFileIds, setVirtualizedFileIds] = createSignal<string[]>(
    [],
  );

  const setForcedRichPreloadIds = (nextIds: string[]) => {
    setForcedRichFileIds((currentIds) =>
      stringArraysEqual(currentIds, nextIds) ? currentIds : nextIds,
    );
  };

  const forceRichFileId = (fileId: string) => {
    setForcedRichFileIds((currentIds) =>
      currentIds.includes(fileId) ? currentIds : [...currentIds, fileId],
    );
  };

  const setFileVirtualized = (fileId: string, virtualized: boolean) => {
    setVirtualizedFileIds((currentIds) => {
      if (virtualized) {
        return currentIds.includes(fileId)
          ? currentIds
          : [...currentIds, fileId];
      }
      return currentIds.filter((currentId) => currentId !== fileId);
    });
  };

  const displayFiles = createMemo(() => {
    const diff = loadedDiff();
    if (diff === null) {
      return [];
    }
    return [...diff.files, ...diff.lazyFiles].sort(
      (leftFile, rightFile) =>
        fileOrderIndex(diff.fileOrder, leftFile) -
        fileOrderIndex(diff.fileOrder, rightFile),
    );
  });

  /**
   * Seed the rich-render preload window until navigation chooses a concrete
   * hunk/file target. Once a user/navigation action forces rich files, that
   * explicit choice wins until the view state is reset.
   */
  createEffect(() => {
    if (forcedRichFileIds().length > 0) {
      return;
    }
    setForcedRichPreloadIds(richPreloadFileIdsForFileId(null, displayFiles()));
  });

  const resetViewState = () => {
    batch(() => {
      setDirectoryExpansion({});
      setFileExpansion({});
      setForcedRichFileIds([]);
      setActiveHunkFileId(null);
      setVirtualizedFileIds([]);
    });
  };

  const updateLoadedDiff = (updater: (current: LoadedDiff) => LoadedDiff) => {
    setLoadedDiff((current) => (current === null ? current : updater(current)));
  };

  const setAllFilesExpanded = (expanded: boolean) => {
    const currentFiles = displayFiles();
    const groups = [...groupFilesByLabel(currentFiles).keys()];
    batch(() => {
      setDirectoryExpansion(() =>
        Object.fromEntries(groups.map((label) => [label, expanded])),
      );
      setFileExpansion(() =>
        Object.fromEntries(
          currentFiles.map((file) => [fileKey(file), expanded]),
        ),
      );
    });
  };

  const openFileExpansion = (file: FileEntry) => {
    const directory = entryDirectoryLabel(file);
    const key = fileKey(file);
    batch(() => {
      setDirectoryExpansion((current) => ({
        ...current,
        [directory]: true,
      }));
      setFileExpansion((current) => ({
        ...current,
        [key]: true,
      }));
    });
  };

  const openDirectoryExpansion = (group: FileGroup) => {
    batch(() => {
      setDirectoryExpansion((current) => ({
        ...current,
        [group.label]: true,
      }));
      setFileExpansion((current) => ({
        ...current,
        ...Object.fromEntries(group.files.map((file) => [fileKey(file), true])),
      }));
    });
  };

  return {
    loadedDiff,
    setLoadedDiff,
    directoryExpansion,
    setDirectoryExpansion,
    fileExpansion,
    setFileExpansion,
    forcedRichFileIds,
    setForcedRichPreloadIds,
    forceRichFileId,
    activeHunkFileId,
    setActiveHunkFileId,
    virtualizedFileIds,
    setFileVirtualized,
    displayFiles,
    resetViewState,
    updateLoadedDiff,
    setAllFilesExpanded,
    openFileExpansion,
    openDirectoryExpansion,
  };
}

export type DiffUiState = ReturnType<typeof createDiffUiState>;
export type ExpansionSetter = Setter<Record<string, boolean>>;
