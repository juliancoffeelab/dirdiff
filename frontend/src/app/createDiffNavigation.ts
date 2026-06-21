import {
  batch,
  createSignal,
  onCleanup,
  onMount,
  type Accessor,
  type Setter,
} from "solid-js";
import type { DiffViewMode } from "../DiffGrid";
import type { FileEntry } from "../api";
import {
  createHunkNavigation,
  fileIdForHunkAnchor,
  richPreloadFileIdsForAnchor,
  richPreloadFileIdsForFileId,
  shouldIgnoreGlobalHotkeyEvent,
} from "../hunkNavigation";
import {
  clearLinePinInHash,
  getLinePinFromHash,
  highlightPinnedLine,
  linePinFromElement,
  restorePinnedLine,
  setLinePinInHash,
} from "../linePins";
import {
  type FileGroup,
  type LinePin,
  directoryElementId,
  entryDirectoryLabel,
  fileBodyAnchorElementId,
  fileDisplayName,
  fileElementId,
  fileKey,
  fileMatchesLinePin,
} from "../fileUtils";

type DiffNavigationOptions = {
  appRoot: Accessor<HTMLElement | undefined>;
  appHeader: Accessor<HTMLElement | undefined>;
  displayFiles: Accessor<FileEntry[]>;
  isFileVirtualized: (fileId: string) => boolean;
  layoutRevision: Accessor<number>;
  virtualizationRevision: Accessor<number>;
  loadingRevision: Accessor<number>;
  diffViewMode: Accessor<DiffViewMode>;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  setForcedRichPreloadIds: (ids: string[]) => void;
  forceRichFileId: (fileId: string) => void;
  setActiveHunkFileId: Setter<string | null>;
  reloadDiff: () => void | Promise<void>;
  toggleDiffViewMode: () => void;
  setAllFilesExpanded: (expanded: boolean) => void;
  openFileExpansion: (file: FileEntry) => void;
  openDirectoryExpansion: (group: FileGroup) => void;
};

type FileTreeNavigationOptions = {
  displayFiles: Accessor<FileEntry[]>;
  isFileVirtualized: (fileId: string) => boolean;
  forceRichFileId: (fileId: string) => void;
  openFileExpansion: (file: FileEntry) => void;
  openDirectoryExpansion: (group: FileGroup) => void;
};

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;

const FILE_TREE_SCROLL_MAX_FRAMES = 120;
const FILE_TREE_SCROLL_STABLE_FRAMES = 2;
const FILE_TREE_SCROLL_STABLE_PX = 1;

function stableFrameCountForTarget(
  target: HTMLElement,
  previousTop: number | null,
  stableFrames: number,
) {
  if (previousTop === null) {
    return 0;
  }
  const top = target.getBoundingClientRect().top;
  if (Math.abs(top - previousTop) <= FILE_TREE_SCROLL_STABLE_PX) {
    return stableFrames + 1;
  }
  return 0;
}

function createFileTreeNavigation(options: FileTreeNavigationOptions) {
  const scrollToFile = (file: FileEntry) => {
    const key = fileKey(file);
    const fileId = fileElementId(key);
    const preloadFileIds = richPreloadFileIdsForFileId(
      fileId,
      options.displayFiles(),
    );
    options.openFileExpansion(file);
    for (const preloadFileId of preloadFileIds) {
      options.forceRichFileId(preloadFileId);
    }

    const resolvedTarget = (card: HTMLElement) => {
      const rowTarget = card.querySelector<HTMLElement>(
        ".diff-row.hunk-anchor:not(.virtual-hunk-anchor)",
      );
      if (rowTarget !== null) {
        return rowTarget;
      }
      const bodyTarget = document.getElementById(fileBodyAnchorElementId(key));
      if (bodyTarget !== null) {
        return bodyTarget;
      }
      throw new Error(
        `Could not find file scroll target for ${fileDisplayName(file)}.`,
      );
    };

    const scrollWhenReady = (
      attempt: number,
      stableFrames: number,
      previousTop: number | null,
    ) => {
      requestAnimationFrame(() => {
        const card = document.getElementById(fileId);
        if (card === null || options.isFileVirtualized(fileId)) {
          if (attempt >= FILE_TREE_SCROLL_MAX_FRAMES) {
            throw new Error(
              `File tree jump did not stabilize for ${fileDisplayName(file)}.`,
            );
          }
          scrollWhenReady(attempt + 1, 0, null);
          return;
        }

        requestAnimationFrame(() => {
          if (options.isFileVirtualized(fileId)) {
            if (attempt >= FILE_TREE_SCROLL_MAX_FRAMES) {
              throw new Error(
                `File tree jump did not stabilize for ${fileDisplayName(file)}.`,
              );
            }
            scrollWhenReady(attempt + 1, 0, null);
            return;
          }
          if (card === null) {
            throw new Error(
              `Could not find file card for ${fileDisplayName(file)}.`,
            );
          }

          const target = resolvedTarget(card);
          target.scrollIntoView({ block: "center", behavior: "instant" });
          requestAnimationFrame(() => {
            const nextStableFrames = stableFrameCountForTarget(
              target,
              previousTop,
              stableFrames,
            );
            if (nextStableFrames < FILE_TREE_SCROLL_STABLE_FRAMES) {
              if (attempt >= FILE_TREE_SCROLL_MAX_FRAMES) {
                throw new Error(
                  `File tree jump did not stabilize for ${fileDisplayName(file)}.`,
                );
              }
              scrollWhenReady(
                attempt + 1,
                nextStableFrames,
                target.getBoundingClientRect().top,
              );
              return;
            }

            card.classList.remove("file-card-flash");
            void card.offsetWidth;
            card.classList.add("file-card-flash");
          });
        });
      });
    };

    scrollWhenReady(0, 0, null);
  };

  const scrollToDirectory = (group: FileGroup) => {
    options.openDirectoryExpansion(group);
    const scrollWhenReady = (
      attempt: number,
      stableFrames: number,
      previousTop: number | null,
    ) => {
      requestAnimationFrame(() => {
        const target = document.getElementById(directoryElementId(group.label));
        if (target === null) {
          if (attempt >= FILE_TREE_SCROLL_MAX_FRAMES) {
            throw new Error(
              `Directory tree jump did not stabilize for ${group.label}.`,
            );
          }
          scrollWhenReady(attempt + 1, 0, null);
          return;
        }
        const header = target.querySelector<HTMLElement>(
          ".directory-group-header",
        );
        if (header === null) {
          throw new Error(
            `Could not find directory header for ${group.label}.`,
          );
        }
        header.scrollIntoView({ block: "start", behavior: "instant" });
        requestAnimationFrame(() => {
          const nextStableFrames = stableFrameCountForTarget(
            header,
            previousTop,
            stableFrames,
          );
          if (nextStableFrames < FILE_TREE_SCROLL_STABLE_FRAMES) {
            if (attempt >= FILE_TREE_SCROLL_MAX_FRAMES) {
              throw new Error(
                `Directory tree jump did not stabilize for ${group.label}.`,
              );
            }
            scrollWhenReady(
              attempt + 1,
              nextStableFrames,
              header.getBoundingClientRect().top,
            );
          }
        });
      });
    };

    scrollWhenReady(0, 0, null);
  };

  return {
    scrollToFile,
    scrollToDirectory,
  };
}

/**
 * Owns browser/DOM-facing diff navigation behavior.
 *
 * This is the intentional home for effects that subscribe to global browser
 * state: keyboard shortcuts, pointer selection, hash-based line pins, sticky
 * header measurement, scroll following, and file-tree scroll targets.
 *
 * It does not own diff data or params state. Instead it receives accessors and
 * explicit actions from UI/diff primitives, then returns the navigation state
 * and commands needed by App and HUD components.
 */
export function createDiffNavigation(options: DiffNavigationOptions) {
  const [linePin, setLinePin] = createSignal<LinePin | null>(
    getLinePinFromHash(),
  );
  const [debugMenuOpen, setDebugMenuOpen] = createSignal(false);
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [fileTreeOpen, setFileTreeOpen] = createSignal(false);
  const fileTreeNavigation = createFileTreeNavigation({
    displayFiles: options.displayFiles,
    isFileVirtualized: options.isFileVirtualized,
    forceRichFileId: options.forceRichFileId,
    openFileExpansion: options.openFileExpansion,
    openDirectoryExpansion: options.openDirectoryExpansion,
  });
  let restoredLinePinKey = "";
  const hunkNav = createHunkNavigation(options.appRoot, {
    afterReconcile: () => {
      const root = options.appRoot();
      if (root === undefined) {
        return;
      }
      restorePinnedLine(root, restoredLinePinKey, (pinKey) => {
        restoredLinePinKey = pinKey;
      });
    },
    onSelectionChange: ({ selected }) => {
      if (selected === null) {
        options.setActiveHunkFileId(null);
        return;
      }
      options.setActiveHunkFileId(fileIdForHunkAnchor(selected));
      options.setForcedRichPreloadIds(
        richPreloadFileIdsForAnchor(selected, options.displayFiles()),
      );
    },
  });

  hunkNav.reconcileWhen([
    options.displayFiles,
    options.layoutRevision,
    options.virtualizationRevision,
    options.loadingRevision,
    options.diffViewMode,
  ]);
  hunkNav.followScroll();

  onMount(() => {
    const root = options.appRoot();
    const header = options.appHeader();
    if (root === undefined || header === undefined) {
      return;
    }

    const updateStickyHeaderOffset = () => {
      root.style.setProperty(
        "--app-header-sticky-offset",
        `${header.offsetHeight}px`,
      );
    };

    updateStickyHeaderOffset();
    const observer = new ResizeObserver(updateStickyHeaderOffset);
    observer.observe(header);
    window.addEventListener("resize", updateStickyHeaderOffset);
    onCleanup(() => {
      observer.disconnect();
      window.removeEventListener("resize", updateStickyHeaderOffset);
    });
  });

  const scrollTop = () => {
    window.scrollTo({ top: 0, behavior: "instant" });
  };

  const toggleFileTreeOpen = () => {
    setFileTreeOpen((open) => !open);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (shouldIgnoreGlobalHotkeyEvent(event)) {
      return;
    }
    if (event.code === "KeyN" && !event.shiftKey) {
      event.preventDefault();
      hunkNav.scrollNext();
      return;
    }
    if (event.code === "KeyN" && event.shiftKey) {
      event.preventDefault();
      hunkNav.scrollPrev();
      return;
    }
    if (event.code === "KeyP") {
      event.preventDefault();
      scrollTop();
      return;
    }
    if (event.code === "KeyT") {
      event.preventDefault();
      toggleFileTreeOpen();
      return;
    }
    if (event.code === "KeyI") {
      event.preventDefault();
      options.toggleDiffViewMode();
      return;
    }
    if (event.code === "KeyS") {
      event.preventDefault();
      options.setAllFilesExpanded(true);
      return;
    }
    if (event.code === "KeyF") {
      event.preventDefault();
      options.setAllFilesExpanded(false);
      return;
    }
    if (event.code === "KeyR") {
      event.preventDefault();
      void options.reloadDiff();
      return;
    }
    if (event.code === "KeyD") {
      event.preventDefault();
      setDebugMenuOpen((open) => !open);
      return;
    }
    if (event.code === "KeyH") {
      event.preventDefault();
      setHelpOpen((open) => !open);
      return;
    }
  };

  onMount(() => {
    document.addEventListener("keydown", onKeyDown);
    onCleanup(() => document.removeEventListener("keydown", onKeyDown));
  });

  const setDiffSelectionSide = (
    grid: HTMLElement | null,
    side: "left" | "right" | null,
  ) => {
    options
      .appRoot()
      ?.querySelector<HTMLElement>(".diff-grid[data-diff-selection-side]")
      ?.removeAttribute("data-diff-selection-side");
    if (grid === null || side === null) {
      return;
    }
    grid.dataset.diffSelectionSide = side;
  };

  const onPointerDown = (event: PointerEvent) => {
    const target = event.target;
    const root = options.appRoot();
    if (
      !(target instanceof Element) ||
      root === undefined ||
      !root.contains(target)
    ) {
      setDiffSelectionSide(null, null);
      return;
    }
    const side = target.closest(".diff-side.side-left, .diff-side.side-right");
    if (side === null || !root.contains(side)) {
      setDiffSelectionSide(null, null);
      return;
    }
    const grid = side.closest<HTMLElement>(".diff-grid");
    if (grid === null || !root.contains(grid)) {
      setDiffSelectionSide(null, null);
      return;
    }
    setDiffSelectionSide(
      grid,
      side.classList.contains("side-left") ? "left" : "right",
    );
  };

  const onLinePinClick = (event: MouseEvent) => {
    const target = event.target;
    const root = options.appRoot();
    if (!(target instanceof Element) || target.closest("button") !== null) {
      return;
    }
    const lineNo = target.closest<HTMLElement>(".line-no[data-line-pin-line]");
    if (lineNo === null || root === undefined || !root.contains(lineNo)) {
      return;
    }
    const pin = linePinFromElement(lineNo);
    if (pin === null) {
      return;
    }
    const pinKey = JSON.stringify(pin);
    const row = lineNo.closest<HTMLElement>(".diff-row");
    if (
      restoredLinePinKey === pinKey &&
      row?.classList.contains("pinned-line") === true
    ) {
      restoredLinePinKey = "";
      clearLinePinInHash();
      setLinePin(null);
      highlightPinnedLine(root, null);
      return;
    }
    restoredLinePinKey = pinKey;
    setLinePinInHash(pin);
    setLinePin(pin);
    highlightPinnedLine(root, row);
  };

  const openPinnedFile = (pin: LinePin) => {
    const file = options
      .displayFiles()
      .find((entry) => fileMatchesLinePin(entry, pin));
    if (file === undefined) {
      return;
    }
    const directory = entryDirectoryLabel(file);
    const key = fileKey(file);
    batch(() => {
      options.setDirectoryExpansion((current) => ({
        ...current,
        [directory]: true,
      }));
      options.setFileExpansion((current) => ({
        ...current,
        [key]: true,
      }));
    });
  };

  const onHashChange = () => {
    const root = options.appRoot();
    if (root === undefined) {
      return;
    }
    const pin = getLinePinFromHash();
    setLinePin(pin);
    if (pin !== null) {
      openPinnedFile(pin);
    }
    restorePinnedLine(root, restoredLinePinKey, (pinKey) => {
      restoredLinePinKey = pinKey;
    });
  };

  document.addEventListener("pointerdown", onPointerDown);
  document.addEventListener("click", onLinePinClick);
  window.addEventListener("hashchange", onHashChange);
  onCleanup(() => {
    document.removeEventListener("pointerdown", onPointerDown);
    document.removeEventListener("click", onLinePinClick);
    window.removeEventListener("hashchange", onHashChange);
    setDiffSelectionSide(null, null);
  });

  return {
    linePin,
    hunkPosition: hunkNav.position,
    debugMenuOpen,
    helpOpen,
    setHelpOpen,
    fileTreeOpen,
    setFileTreeOpen,
    scrollNext: hunkNav.scrollNext,
    scrollPrev: hunkNav.scrollPrev,
    scrollToFile: fileTreeNavigation.scrollToFile,
    scrollToDirectory: fileTreeNavigation.scrollToDirectory,
  };
}
