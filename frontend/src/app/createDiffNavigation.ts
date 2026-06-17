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
} from "../model";

type DiffNavigationOptions = {
  appRoot: Accessor<HTMLElement | undefined>;
  appHeader: Accessor<HTMLElement | undefined>;
  displayFiles: Accessor<FileEntry[]>;
  directoryExpansion: Accessor<Record<string, boolean>>;
  fileExpansion: Accessor<Record<string, boolean>>;
  loadingFiles: Accessor<Record<string, boolean>>;
  forcedRichFileIds: Accessor<string[]>;
  diffViewMode: Accessor<DiffViewMode>;
  setDirectoryExpansion: Setter<Record<string, boolean>>;
  setFileExpansion: Setter<Record<string, boolean>>;
  setForcedRichPreloadIds: (ids: string[]) => void;
  forceRichFileId: (fileId: string) => void;
  setActiveHunkFileId: Setter<string | null>;
  reloadDiff: () => void;
  toggleDiffViewMode: () => void;
  setAllFilesExpanded: (expanded: boolean) => void;
  openFileExpansion: (file: FileEntry) => void;
  openDirectoryExpansion: (group: FileGroup) => void;
};

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
    options.directoryExpansion,
    options.fileExpansion,
    options.loadingFiles,
    options.forcedRichFileIds,
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
      options.reloadDiff();
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

  const scrollToFile = (file: FileEntry) => {
    const key = fileKey(file);
    const fileId = fileElementId(key);
    options.openFileExpansion(file);
    options.forceRichFileId(fileId);
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const card = document.getElementById(fileId);
        if (card === null) {
          throw new Error(
            `Could not find file card for ${fileDisplayName(file)}.`,
          );
        }
        let target = card.querySelector<HTMLElement>(
          ".diff-row.hunk-anchor:not(.virtual-hunk-anchor)",
        );
        if (target === null) {
          target = document.getElementById(fileBodyAnchorElementId(key));
        }
        if (target === null) {
          throw new Error(
            `Could not find file scroll target for ${fileDisplayName(file)}.`,
          );
        }
        target.scrollIntoView({ block: "center", behavior: "instant" });
        card.classList.remove("file-card-flash");
        void card.offsetWidth;
        card.classList.add("file-card-flash");
      });
    });
  };

  const scrollToDirectory = (group: FileGroup) => {
    options.openDirectoryExpansion(group);
    requestAnimationFrame(() => {
      const target = document.getElementById(directoryElementId(group.label));
      if (target === null) {
        throw new Error(`Could not find directory group for ${group.label}.`);
      }
      const header = target.querySelector<HTMLElement>(
        ".directory-group-header",
      );
      if (header === null) {
        throw new Error(`Could not find directory header for ${group.label}.`);
      }
      header.scrollIntoView({ block: "start", behavior: "instant" });
    });
  };

  return {
    linePin,
    debugMenuOpen,
    helpOpen,
    setHelpOpen,
    fileTreeOpen,
    setFileTreeOpen,
    scrollNext: hunkNav.scrollNext,
    scrollPrev: hunkNav.scrollPrev,
    scrollToFile,
    scrollToDirectory,
  };
}
