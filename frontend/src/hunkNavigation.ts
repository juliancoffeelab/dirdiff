import { createEffect, createSignal, onCleanup, type Accessor } from "solid-js";
import type { DiffRow, FileEntry } from "./api";
import { fileElementId, fileEntryIsHydrated, fileKey, fileRows } from "./model";

type HunkNavigationOptions = {
  afterReconcile?: () => void;
  onSelectionChange?: (selection: {
    anchors: HunkAnchor[];
    index: number;
    selected: HTMLElement | null;
  }) => void;
};

const SCROLL_FOLLOW_INTERVAL_MS = 100;
const PROGRAMMATIC_SCROLL_IGNORE_MS = 150;
const READING_LINE_RATIO = 0.5;
const RICH_PRELOAD_FILE_RADIUS = 2;

type HunkAnchor = HTMLElement | null;

export function fileIdForHunkAnchor(anchor: HTMLElement): string | null {
  const fileCard = anchor.closest<HTMLElement>(".file-card");
  if (fileCard === null) {
    return null;
  }
  return fileCard.id;
}

export function richPreloadFileIdsForAnchor(
  anchor: HTMLElement,
  files: FileEntry[],
): string[] {
  return richPreloadFileIdsForFileId(fileIdForHunkAnchor(anchor), files);
}

export function richPreloadFileIdsForFileId(
  activeFileId: string | null,
  files: FileEntry[],
): string[] {
  const changedFileIds = files
    .filter(fileCanHaveDomHunks)
    .map((file) => fileElementId(fileKey(file)));
  if (!changedFileIds.length) {
    return [];
  }

  const forced = new Set<string>();
  forced.add(changedFileIds[0]);
  forced.add(changedFileIds[changedFileIds.length - 1]);

  const activeIndex =
    activeFileId === null ? -1 : changedFileIds.indexOf(activeFileId);
  if (activeIndex !== -1) {
    const start = Math.max(0, activeIndex - RICH_PRELOAD_FILE_RADIUS);
    const end = Math.min(
      changedFileIds.length - 1,
      activeIndex + RICH_PRELOAD_FILE_RADIUS,
    );
    for (let index = start; index <= end; index += 1) {
      forced.add(changedFileIds[index]);
    }
  }

  return [...forced];
}

function fileCanHaveDomHunks(file: FileEntry): boolean {
  return (
    fileEntryIsHydrated(file) &&
    file.render_kind !== "notebook" &&
    fileRows(file).length > 0 &&
    fileHasChangedRows(file)
  );
}

function fileHasChangedRows(file: FileEntry): boolean {
  return fileRows(file).some((row) => isChangedDiffRowStatus(row.status));
}

function isChangedDiffRowStatus(status: DiffRow["status"]): boolean {
  return status === "replace" || status === "insert" || status === "delete";
}

export function shouldIgnoreGlobalHotkeyEvent(event: KeyboardEvent): boolean {
  if (
    event.defaultPrevented ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  ) {
    return true;
  }
  const target = event.target;
  if (!(target instanceof HTMLElement)) {
    return false;
  }
  return (
    target.isContentEditable ||
    Boolean(target.closest("input, textarea, select, [contenteditable='true']"))
  );
}

function hunkAnchorElements(root: ParentNode | undefined): HTMLElement[] {
  return [
    ...hunkAnchorRoot(root).querySelectorAll<HTMLElement>(".hunk-anchor"),
  ];
}

function hunkAnchorRoot(root: ParentNode | undefined): ParentNode {
  if (root === undefined) {
    return document;
  }
  return root;
}

function hunkAnchors(root: ParentNode | undefined): HunkAnchor[] {
  return hunkAnchorElements(root).map((anchor) =>
    anchor.classList.contains("hunk-skip") ? null : anchor,
  );
}

function selectCurrentHunk(options: {
  index: number;
  scroll: boolean;
  root: ParentNode | undefined;
}): HTMLElement | null {
  const anchors = hunkAnchors(options.root);
  const anchorElements = hunkAnchorElements(options.root);
  if (anchors.length === 0) {
    return null;
  }
  const selected = anchors[clamp(options.index, 0, anchors.length - 1)];
  for (const anchor of anchorElements) {
    anchor.classList.remove("active-hunk");
    anchor.removeAttribute("aria-current");
  }
  if (selected === null) {
    return null;
  }
  selected.classList.add("active-hunk");
  selected.setAttribute("aria-current", "true");
  if (options.scroll) {
    selected.scrollIntoView({
      block: "center",
      behavior: "instant",
    });
  }
  return selected;
}

function wrapIndex(index: number, length: number): number {
  return ((index % length) + length) % length;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function nextSelectableIndex(
  anchors: HunkAnchor[],
  startIndex: number,
  direction: 1 | -1,
): number | null {
  if (!anchors.some((anchor) => anchor !== null)) {
    return null;
  }
  let index = wrapIndex(startIndex, anchors.length);
  for (let steps = 0; steps < anchors.length; steps += 1) {
    if (anchors[index] !== null) {
      return index;
    }
    index = wrapIndex(index + direction, anchors.length);
  }
  return null;
}

export function createHunkNavigation(
  root: () => ParentNode | undefined,
  options: HunkNavigationOptions = {},
) {
  const [currentIndex, setCurrentIndex] = createSignal(0);
  let reconcileTimer: number | null = null;
  let scrollFollowTimer: number | null = null;
  let ignoreScrollFollowUntil = 0;

  const anchors = () => hunkAnchors(root());

  const select = (selectionOptions: { index: number; scroll: boolean }) => {
    const currentAnchors = anchors();
    if (!currentAnchors.length) {
      return;
    }
    const index = clamp(selectionOptions.index, 0, currentAnchors.length - 1);
    const selected = selectCurrentHunk({
      index,
      scroll: selectionOptions.scroll,
      root: root(),
    });
    options.onSelectionChange?.({
      anchors: currentAnchors,
      index,
      selected,
    });
  };

  const cancelReconcileTimer = () => {
    if (reconcileTimer === null) {
      return;
    }
    clearTimeout(reconcileTimer);
    reconcileTimer = null;
  };

  const cancelScrollFollowTimer = () => {
    if (scrollFollowTimer === null) {
      return;
    }
    clearTimeout(scrollFollowTimer);
    scrollFollowTimer = null;
  };

  const ignoreScrollFollowFor = (durationMs: number) => {
    ignoreScrollFollowUntil = performance.now() + durationMs;
  };

  const selectNearestHunkToViewport = () => {
    const currentAnchors = anchors();
    if (currentAnchors.length === 0) {
      return;
    }

    const readingLineY = window.innerHeight * READING_LINE_RATIO;
    let nextIndex = currentIndex();
    let nextDistance = Number.POSITIVE_INFINITY;

    currentAnchors.forEach((anchor, index) => {
      if (anchor === null) {
        return;
      }
      const distance = Math.abs(
        anchor.getBoundingClientRect().top - readingLineY,
      );
      if (distance < nextDistance) {
        nextIndex = index;
        nextDistance = distance;
      }
    });

    if (nextIndex === currentIndex()) {
      return;
    }

    setCurrentIndex(nextIndex);
    select({ index: nextIndex, scroll: false });
  };

  const reconcile = () => {
    cancelReconcileTimer();
    reconcileTimer = window.setTimeout(() => {
      reconcileTimer = null;
      const currentAnchors = anchors();
      if (currentAnchors.length === 0) {
        setCurrentIndex(0);
        options.afterReconcile?.();
        return;
      }

      const activeIndex = currentAnchors.findIndex(
        (anchor) => anchor?.classList.contains("active-hunk") === true,
      );
      const nextIndex =
        activeIndex === -1
          ? clamp(currentIndex(), 0, currentAnchors.length - 1)
          : activeIndex;
      setCurrentIndex(nextIndex);
      select({ index: nextIndex, scroll: false });
      options.afterReconcile?.();
    }, 120);
  };

  const scroll = (direction: 1 | -1) => {
    cancelScrollFollowTimer();
    const currentAnchors = anchors();
    if (!currentAnchors.length) {
      console.error(
        "[dirdiff] Hunk navigation requested with no mounted hunk anchors.",
      );
      throw new Error(
        "Hunk navigation requested with no mounted hunk anchors.",
      );
    }

    const nextIndex = nextSelectableIndex(
      currentAnchors,
      currentIndex() + direction,
      direction,
    );
    if (nextIndex === null) {
      setCurrentIndex(0);
      select({ index: 0, scroll: false });
      return;
    }
    setCurrentIndex(nextIndex);
    ignoreScrollFollowFor(PROGRAMMATIC_SCROLL_IGNORE_MS);
    select({ index: nextIndex, scroll: true });
  };

  const onScroll = () => {
    if (performance.now() < ignoreScrollFollowUntil) {
      return;
    }
    if (scrollFollowTimer !== null) {
      return;
    }

    scrollFollowTimer = window.setTimeout(() => {
      scrollFollowTimer = null;
      selectNearestHunkToViewport();
    }, SCROLL_FOLLOW_INTERVAL_MS);
  };

  const followScroll = () => {
    window.addEventListener("scroll", onScroll, { passive: true });
    onCleanup(() => {
      window.removeEventListener("scroll", onScroll);
      cancelScrollFollowTimer();
    });
  };

  const reconcileWhen = (dependencies: Accessor<unknown>[]) => {
    createEffect(() => {
      for (const dependency of dependencies) {
        dependency();
      }
      reconcile();
    });
  };

  onCleanup(() => {
    cancelReconcileTimer();
    cancelScrollFollowTimer();
  });

  return {
    followScroll,
    ignoreScrollFollowFor,
    reconcileWhen,
    scrollNext: () => scroll(1),
    scrollPrev: () => scroll(-1),
  };
}
