/**
 * Connect hunk selection to the rendered diff, navigation commands, and
 * recognized user scrolling.
 *
 * Callers provide the DOM root containing `.hunk-anchor` elements and may
 * subscribe to selection changes to update file highlighting and rich-file
 * preloading. This module may move the viewport only for explicit next/previous
 * navigation. Scroll-follow observes the existing viewport and must never pick
 * a hunk outside both the visible viewport and the file at the reading line.
 */
import { createEffect, createSignal, onCleanup, type Accessor } from "solid-js";
import type { FileEntry } from "./api";
import { fileElementId, fileKey } from "./fileUtils";
import { clamp, wrapIndex } from "./utils";

type HunkNavigationOptions = {
  afterReconcile?: () => void;
  onSelectionChange?: (selection: {
    anchors: HunkAnchor[];
    index: number;
    selected: HTMLElement;
  }) => void;
};

const READING_LINE_RATIO = 0.5;
const RICH_PRELOAD_FILE_RADIUS = 2;

type HunkAnchor = HTMLElement;
type HunkPosition = {
  current: number;
  total: number;
};

/**
 * Return the DOM id of the file card containing `anchor`.
 *
 * Callers may pass a virtual or rich hunk anchor. An anchor outside a mounted
 * `.file-card` returns `null`, allowing preload calculation to fall back to its
 * boundary files instead of inventing a file association.
 */
export function fileIdForHunkAnchor(anchor: HTMLElement): string | null {
  const fileCard = anchor.closest<HTMLElement>(".file-card");
  if (fileCard === null) {
    return null;
  }
  return fileCard.id;
}

/**
 * Return the eager file-card ids that should remain rich around `anchor`.
 *
 * Lazy files are intentionally excluded because their content is unavailable;
 * the first and last eager files remain included for boundary navigation.
 */
export function richPreloadFileIdsForAnchor(
  anchor: HTMLElement,
  files: FileEntry[],
): string[] {
  return richPreloadFileIdsForFileId(fileIdForHunkAnchor(anchor), files);
}

/**
 * Return the eager file-card ids kept rich around `activeFileId`.
 *
 * The result always includes the first and last eager files. When the active id
 * belongs to an eager file, it also includes the circular two-file neighborhood
 * on either side. An absent or lazy active id receives only the boundary files.
 */
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

/**
 * Report whether global hunk shortcuts must leave this key event untouched.
 *
 * Modified/default-prevented events and events from editable controls belong to
 * the browser or focused control, not to diff navigation.
 */
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

/**
 * Return every mounted hunk anchor below `root`, in document order.
 *
 * DOM order is the canonical global hunk order used by both navigation and the
 * visible `current/total` number; this function performs no visibility or
 * selectability filtering.
 */
function hunkAnchorElements(root: ParentNode | undefined): HTMLElement[] {
  return [
    ...hunkAnchorRoot(root).querySelectorAll<HTMLElement>(".hunk-anchor"),
  ];
}

/**
 * Resolve the DOM scope used for anchor queries.
 *
 * Tests and embedded views may provide a narrower root. The application omits
 * it and therefore queries the document; keeping that fallback here makes every
 * anchor read use the same scope.
 */
function hunkAnchorRoot(root: ParentNode | undefined): ParentNode {
  if (root === undefined) {
    return document;
  }
  return root;
}

/**
 * Return the ordered hunk anchors used for indexing and the hunk number.
 *
 * The alias gives navigation code the `HunkAnchor` vocabulary while delegating
 * the actual DOM query and root fallback to the functions above.
 */
function hunkAnchors(root: ParentNode | undefined): HunkAnchor[] {
  return hunkAnchorElements(root);
}

/**
 * Report whether an anchor occupies vertical space inside the viewport.
 *
 * Zero-height and fully off-screen anchors cannot represent what the user is
 * reading, so scroll-follow excludes them before applying the current-file
 * constraint.
 */
function anchorIsVisible(anchor: HTMLElement): boolean {
  const rect = anchor.getBoundingClientRect();
  return rect.height > 0 && rect.bottom > 0 && rect.top < window.innerHeight;
}

/**
 * Report whether navigation may select an anchor.
 *
 * `.hunk-skip` anchors preserve global indexing and layout but are deliberately
 * excluded as navigation destinations and scroll-follow results.
 */
function anchorIsSelectable(anchor: HTMLElement): boolean {
  return !anchor.classList.contains("hunk-skip");
}

/**
 * Return the selectable anchor at the clamped index, or `null` for a skipped or
 * missing anchor.
 *
 * Callers must provide at least one anchor so the clamp interval is valid.
 * Explicit navigation uses this to distinguish "re-center the current hunk"
 * from "advance to another hunk" after virtualization changes mounted nodes.
 */
function currentSelectableAnchor(
  anchors: HunkAnchor[],
  index: number,
): HTMLElement | null {
  const anchor = anchors[clamp(index, 0, anchors.length - 1)];
  if (anchor === undefined || !anchorIsSelectable(anchor)) {
    return null;
  }
  return anchor;
}

/**
 * Apply the active-hunk DOM state at `options.index` and optionally center it.
 *
 * Callers must provide a root containing at least one hunk anchor. The returned
 * element is the exact node marked with `active-hunk` and `aria-current`.
 */
function selectCurrentHunk(options: {
  index: number;
  scroll: boolean;
  root: ParentNode | undefined;
}): HTMLElement {
  const anchors = hunkAnchors(options.root);
  const anchorElements = hunkAnchorElements(options.root);
  if (anchors.length === 0) {
    throw new Error("Cannot select hunk without mounted hunk anchors.");
  }
  const selected = anchors[clamp(options.index, 0, anchors.length - 1)];
  for (const anchor of anchorElements) {
    anchor.classList.remove("active-hunk");
    anchor.removeAttribute("aria-current");
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

/**
 * Find the next selectable anchor while wrapping in `direction`.
 *
 * Navigation must step over structural `.hunk-skip` anchors without changing
 * the global index space. Returns `null` only when no selectable destination is
 * mounted.
 */
function nextSelectableIndex(
  anchors: HunkAnchor[],
  startIndex: number,
  direction: 1 | -1,
): number | null {
  if (!anchors.some(anchorIsSelectable)) {
    return null;
  }
  let index = wrapIndex(startIndex, anchors.length);
  for (let steps = 0; steps < anchors.length; steps += 1) {
    if (anchorIsSelectable(anchors[index])) {
      return index;
    }
    index = wrapIndex(index + direction, anchors.length);
  }
  return null;
}

/**
 * Create hunk navigation bound to a reactive diff DOM root.
 *
 * The returned interface has four responsibilities:
 *
 * - `position` exposes the visible `current/total` hunk number.
 * - `scrollNext` and `scrollPrev` select and center explicit navigation targets.
 * - `followScroll` installs listeners for recognized wheel, touch, and native
 *   scroll-key movement.
 * - `reconcileWhen` refreshes the total after rendering dependencies change
 *   without choosing a different hunk.
 *
 * The caller must install the listeners from a Solid owner so cleanup can
 * remove their timers and event handlers.
 */
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
  // A `scroll` event may follow user input, `scrollIntoView`, browser anchoring,
  // or a virtualization layout change. This flag is set only by recognized
  // user input and remains set through browser momentum until `scrollend`.
  let userScrollActive = false;
  // Touch events provide positions rather than a scroll direction. Retaining
  // the previous contact position lets the same boundary rule be applied to
  // touch movement as to wheel and keyboard movement.
  let previousTouchY: number | null = null;

  /**
   * Read the anchors currently mounted below the caller's DOM root.
   *
   * The root is evaluated for every operation because virtualization may
   * replace anchor nodes between navigation events.
   */
  const anchors = () => hunkAnchors(root());

  /**
   * Update the public one-based hunk number without changing DOM selection.
   *
   * An empty rendered diff is reported as `0/0`; otherwise the supplied global
   * index is converted to the number shown in each visible file header.
   */
  const syncPosition = (
    currentAnchors: HunkAnchor[],
    selectedIndex: number,
  ) => {
    if (currentAnchors.length === 0) {
      setPosition({ current: 0, total: 0 });
      return;
    }
    setPosition({
      current: selectedIndex + 1,
      total: currentAnchors.length,
    });
  };

  /**
   * Apply one indexed selection and optionally notify the application.
   *
   * Hunk selection and `currentIndex` writes are allowed only from:
   *
   * 1. `scrollNext`.
   * 2. `scrollPrev`.
   * 3. The functional `scroll` handler inside `followScroll`.
   *
   * Reconciliation, virtualization, rendering, and layout effects must not
   * choose a hunk. With no mounted anchors, this function reports `0/0` and
   * leaves selection unchanged.
   */
  const select = (selectionOptions: {
    index: number;
    notify: boolean;
    scroll: boolean;
  }) => {
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
    syncPosition(currentAnchors, index);
    if (selectionOptions.notify) {
      options.onSelectionChange?.({
        anchors: currentAnchors,
        index,
        selected,
      });
    }
  };

  /**
   * Cancel the pending render reconciliation, if one exists.
   *
   * Rendering dependencies can change repeatedly in one burst. Cancellation
   * ensures only the latest mounted-anchor set updates the displayed total.
   */
  const cancelReconcileTimer = () => {
    if (reconcileTimer === null) {
      return;
    }
    clearTimeout(reconcileTimer);
    reconcileTimer = null;
  };

  /**
   * Find the hunk represented at the viewport reading line.
   *
   * Candidates must be selectable, visible, and inside the file card crossing
   * the viewport midpoint. Within that file, the last visible hunk at or above
   * the midpoint wins; before its first visible hunk, that first hunk wins. If
   * either the viewport or current file has no candidate, return `null` so the
   * visible hunk number remains unchanged.
   */
  const hunkIndexAtReadingLine = (currentAnchors: HunkAnchor[]) => {
    if (currentAnchors.length === 0) {
      return null;
    }

    const readingLineY = window.innerHeight * READING_LINE_RATIO;
    const candidateAnchorEntries = currentAnchors.flatMap((anchor, index) =>
      anchorIsSelectable(anchor) && anchorIsVisible(anchor)
        ? [{ anchor, index }]
        : [],
    );
    if (candidateAnchorEntries.length === 0) {
      return null;
    }

    const readingFile = [
      ...document.querySelectorAll<HTMLElement>(".file-card"),
    ].find((fileCard) => {
      const rect = fileCard.getBoundingClientRect();
      return rect.top <= readingLineY && rect.bottom >= readingLineY;
    });
    if (readingFile === undefined) {
      return null;
    }
    const readingFileAnchors = candidateAnchorEntries.filter(
      ({ anchor }) => anchor.closest(".file-card") === readingFile,
    );
    if (readingFileAnchors.length === 0) {
      return null;
    }

    const precedingAnchors = readingFileAnchors.filter(
      ({ anchor }) => anchor.getBoundingClientRect().top <= readingLineY,
    );
    const eligibleAnchors =
      precedingAnchors.length > 0 ? precedingAnchors : readingFileAnchors;
    return eligibleAnchors.reduce((selected, candidate) => {
      const selectedTop = selected.anchor.getBoundingClientRect().top;
      const candidateTop = candidate.anchor.getBoundingClientRect().top;
      if (precedingAnchors.length > 0) {
        return candidateTop > selectedTop ? candidate : selected;
      }
      return candidateTop < selectedTop ? candidate : selected;
    }).index;
  };

  /**
   * Refresh the public hunk total after rendered anchors settle.
   *
   * The short delay coalesces Solid DOM updates. Reconciliation clamps the
   * displayed value to the mounted anchor count but never selects another hunk
   * or moves the viewport.
   */
  const reconcile = () => {
    cancelReconcileTimer();
    reconcileTimer = window.setTimeout(() => {
      reconcileTimer = null;
      const currentAnchors = anchors();
      if (currentAnchors.length === 0) {
        setPosition({ current: 0, total: 0 });
        options.afterReconcile?.();
        return;
      }

      const nextIndex = clamp(currentIndex(), 0, currentAnchors.length - 1);
      syncPosition(currentAnchors, nextIndex);
      options.afterReconcile?.();
    }, 120);
  };

  /**
   * Resolve the target for explicit next/previous navigation.
   *
   * A selected hunk that became off-screen is re-centered before advancing.
   * Otherwise navigation wraps to the next selectable mounted anchor. Calling
   * without mounted anchors is an application error.
   */
  const explicitScrollTarget = (direction: 1 | -1) => {
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
      return { index: currentIndex(), scroll: true };
    }

    const nextIndex = nextSelectableIndex(
      currentAnchors,
      currentIndex() + direction,
      direction,
    );
    if (nextIndex === null) {
      return { index: 0, scroll: false };
    }

    return { index: nextIndex, scroll: true };
  };

  /**
   * Select and center the next hunk requested by the `n` key or HUD button.
   *
   * It clears scroll-follow state before centering so the resulting
   * `scrollIntoView` events cannot replace the explicitly requested hunk.
   */
  const scrollNext = () => {
    // Its `scrollIntoView` must not be processed as continued user scrolling.
    userScrollActive = false;
    const nextTarget = explicitScrollTarget(1);
    setCurrentIndex(nextTarget.index);
    select({
      index: nextTarget.index,
      notify: true,
      scroll: nextTarget.scroll,
    });
  };

  /**
   * Select and center the previous hunk requested by `N` or the HUD button.
   *
   * It clears scroll-follow state before centering for the same reason as
   * `scrollNext`: programmatic movement must preserve the requested hunk.
   */
  const scrollPrev = () => {
    // Its `scrollIntoView` must not be processed as continued user scrolling.
    userScrollActive = false;
    const nextTarget = explicitScrollTarget(-1);
    setCurrentIndex(nextTarget.index);
    select({
      index: nextTarget.index,
      notify: true,
      scroll: nextTarget.scroll,
    });
  };

  /**
   * Update hunk selection during scrolling initiated by recognized user input.
   *
   * Input gate:
   *
   * Wheel, touch, and supported scroll-key events set `userScrollActive` when
   * the viewport can move in their direction. `scroll` events update selection
   * only while that flag is set. `scrollend` clears the flag. Explicit
   * next/previous-hunk navigation clears it before calling `scrollIntoView`.
   * Input blocked at the top or bottom does not set it because that input may
   * produce no subsequent `scrollend`.
   *
   * On each allowed `scroll` event:
   *
   * 1. Find the file card intersecting the reading line.
   * 2. If that file has visible selectable anchors, select its last anchor at
   *    or above the reading line, or its first anchor if none has reached it.
   * 3. If the file has no visible selectable anchor, exit.
   * 4. Reapply selection when the index changed or the indexed anchor is not
   *    the active DOM hunk.
   * 5. Update selection without moving the viewport.
   *
   * Stop conditions:
   *
   * - Only consider hunks in the viewport. If there are none, leave the hunk
   *   index unchanged.
   * - Only consider hunks in the current file. If there are none, leave the
   *   hunk index unchanged.
   *
   * Why `scroll` alone is insufficient:
   *
   * A `scroll` event does not distinguish user input from programmatic
   * scrolling or layout changes, so it cannot enable hunk synchronization by
   * itself.
   */
  const followScroll = () => {
    /**
     * Mark an input as capable of producing user scrolling in `direction`.
     *
     * Input at the matching document boundary is ignored. For example, a
     * downward wheel gesture at the bottom may emit neither `scroll` nor
     * `scrollend`; setting the flag there would let a later virtualization
     * layout correction change the hunk number despite no intervening user
     * movement.
     */
    const followAfterUserScroll = (direction: -1 | 1) => {
      const maximumScrollY =
        document.documentElement.scrollHeight - window.innerHeight;
      if (
        (direction === -1 && window.scrollY <= 0) ||
        (direction === 1 && window.scrollY >= maximumScrollY)
      ) {
        return;
      }
      userScrollActive = true;
    };

    const controller = new AbortController();
    const passiveListener = { passive: true, signal: controller.signal };

    // This is the functional scroll-follow trigger. It is the only listener in
    // this function that calculates or selects a hunk; the listeners below only
    // decide whether this handler may run for a given `scroll` event.
    window.addEventListener(
      "scroll",
      () => {
        if (!userScrollActive) {
          return;
        }
        const currentAnchors = anchors();
        const nextIndex = hunkIndexAtReadingLine(currentAnchors);
        if (nextIndex === null) {
          return;
        }
        if (
          nextIndex === currentIndex() &&
          currentAnchors[nextIndex]?.classList.contains("active-hunk") === true
        ) {
          return;
        }

        // Virtual/rich replacement may replace the DOM node at the same numeric
        // index. Re-select that node so the header, file-tree highlight, and
        // preload files match what the user sees. `scroll: false` preserves the
        // user's viewport.
        setCurrentIndex(nextIndex);
        select({ index: nextIndex, notify: true, scroll: false });
      },
      passiveListener,
    );

    // These listeners only enable or disable the functional `scroll` handler
    // above, or retain enough touch state to determine its direction. They do
    // not calculate or select a hunk themselves.
    window.addEventListener(
      "scrollend",
      () => {
        // scrollend covers wheel/touch momentum and browser-animated keyboard
        // scrolling without an arbitrary idle timeout.
        userScrollActive = false;
      },
      passiveListener,
    );
    window.addEventListener(
      "wheel",
      (event) => {
        // Horizontal gestures do not review later/earlier lines. ctrl+wheel is
        // browser zoom/pinch input and must not enable hunk selection.
        if (event.deltaY === 0 || event.ctrlKey) {
          return;
        }
        followAfterUserScroll(event.deltaY < 0 ? -1 : 1);
      },
      passiveListener,
    );
    window.addEventListener(
      "touchstart",
      (event) => {
        previousTouchY = event.touches[0]?.clientY ?? null;
      },
      passiveListener,
    );
    window.addEventListener(
      "touchmove",
      (event) => {
        const touchY = event.touches[0]?.clientY;
        if (touchY === undefined || previousTouchY === null) {
          previousTouchY = touchY ?? null;
          return;
        }
        const deltaY = previousTouchY - touchY;
        previousTouchY = touchY;
        if (deltaY !== 0) {
          followAfterUserScroll(deltaY < 0 ? -1 : 1);
        }
      },
      passiveListener,
    );
    window.addEventListener(
      "touchend",
      () => {
        previousTouchY = null;
      },
      passiveListener,
    );
    window.addEventListener(
      "keydown",
      (event) => {
        // Only keys whose native default action scrolls the page may enable the
        // scroll handler. Space is excluded on a focused button/link because it
        // activates the control there; that layout change is not review scroll.
        if (
          ![
            "ArrowUp",
            "ArrowDown",
            "PageUp",
            "PageDown",
            "Home",
            "End",
            " ",
          ].includes(event.key) ||
          shouldIgnoreGlobalHotkeyEvent(event) ||
          (event.key === " " &&
            event.target instanceof HTMLElement &&
            event.target.closest("button, a[href]") !== null)
        ) {
          return;
        }
        const upward =
          ["ArrowUp", "PageUp", "Home"].includes(event.key) ||
          (event.key === " " && event.shiftKey);
        followAfterUserScroll(upward ? -1 : 1);
      },
      { signal: controller.signal },
    );
    onCleanup(() => {
      controller.abort();
    });
  };

  /**
   * Reconcile the public hunk total whenever any supplied accessor changes.
   *
   * Accessors are read inside one Solid effect solely to establish reactive
   * dependencies. The subsequent reconciliation never chooses a new hunk.
   */
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
  });

  return {
    followScroll,
    position,
    reconcileWhen,
    scrollNext,
    scrollPrev,
  };
}
