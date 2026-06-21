import { createEffect, createSignal, onCleanup, type Accessor } from "solid-js";
import type { FileEntry } from "./api";
import { fileElementId, fileKey } from "./fileUtils";

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
type HunkPosition = {
  current: number;
  total: number;
};

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
  const preloadFileIds = files
    .filter((file) => file.lazy === null)
    .map((file) => fileElementId(fileKey(file)));
  if (!preloadFileIds.length) {
    return [];
  }

  const forced = new Set<string>();
  forced.add(preloadFileIds[0]);
  forced.add(preloadFileIds[preloadFileIds.length - 1]);

  const activeIndex =
    activeFileId === null ? -1 : preloadFileIds.indexOf(activeFileId);
  if (activeIndex === -1) {
    return [...forced];
  }

  for (
    let offset = -RICH_PRELOAD_FILE_RADIUS;
    offset <= RICH_PRELOAD_FILE_RADIUS;
    offset += 1
  ) {
    const index = wrapIndex(activeIndex + offset, preloadFileIds.length);
    forced.add(preloadFileIds[index]);
  }

  return [...forced];
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

function anchorDistanceToReadingLine(
  anchor: HTMLElement,
  readingLineY: number,
) {
  return Math.abs(anchor.getBoundingClientRect().top - readingLineY);
}

function anchorIsVisible(anchor: HTMLElement): boolean {
  const rect = anchor.getBoundingClientRect();
  return rect.bottom > 0 && rect.top < window.innerHeight;
}

function currentSelectableAnchor(
  anchors: HunkAnchor[],
  index: number,
): HTMLElement | null {
  const anchor = anchors[clamp(index, 0, anchors.length - 1)];
  if (anchor === null || anchor === undefined) {
    return null;
  }
  return anchor;
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
  const [position, setPosition] = createSignal<HunkPosition>({
    current: 0,
    total: 0,
  });
  let reconcileTimer: number | null = null;
  let scrollFollowTimer: number | null = null;
  let ignoreScrollFollowUntil = 0;

  const anchors = () => hunkAnchors(root());

  const syncPosition = (
    currentAnchors: HunkAnchor[],
    selectedIndex: number,
  ) => {
    const selectableIndices = currentAnchors.flatMap((anchor, index) =>
      anchor === null ? [] : [index],
    );
    if (selectableIndices.length === 0) {
      setPosition({ current: 0, total: 0 });
      return;
    }
    const ordinal = selectableIndices.indexOf(selectedIndex);
    setPosition({
      current: ordinal === -1 ? 0 : ordinal + 1,
      total: selectableIndices.length,
    });
  };

  const select = (selectionOptions: { index: number; scroll: boolean }) => {
    const currentAnchors = anchors();
    if (!currentAnchors.length) {
      setPosition({ current: 0, total: 0 });
      return;
    }
    const index = clamp(selectionOptions.index, 0, currentAnchors.length - 1);
    const selected = selectCurrentHunk({
      index,
      scroll: selectionOptions.scroll,
      root: root(),
    });
    syncPosition(currentAnchors, selected === null ? -1 : index);
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
    const candidateAnchorEntries = currentAnchors.flatMap((anchor, index) =>
      anchor !== null && anchorIsVisible(anchor) ? [{ anchor, index }] : [],
    );
    if (candidateAnchorEntries.length === 0) {
      return;
    }

    let nextIndex = candidateAnchorEntries[0].index;
    let nextDistance = anchorDistanceToReadingLine(
      candidateAnchorEntries[0].anchor,
      readingLineY,
    );
    for (const { anchor, index } of candidateAnchorEntries.slice(1)) {
      const distance = anchorDistanceToReadingLine(anchor, readingLineY);
      if (distance < nextDistance) {
        nextIndex = index;
        nextDistance = distance;
      }
    }

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
        setPosition({ current: 0, total: 0 });
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

    const currentAnchor = currentSelectableAnchor(
      currentAnchors,
      currentIndex(),
    );
    if (currentAnchor !== null && !anchorIsVisible(currentAnchor)) {
      ignoreScrollFollowFor(PROGRAMMATIC_SCROLL_IGNORE_MS);
      select({ index: currentIndex(), scroll: true });
      return;
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
    position,
    reconcileWhen,
    scrollNext: () => scroll(1),
    scrollPrev: () => scroll(-1),
  };
}
