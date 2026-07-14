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
import {
  type RenderedFile,
  type RenderedFileEntry,
  fileElementId,
  fileKey,
} from "./fileUtils";
import { wrapIndex } from "./utils";

type HunkNavigationOptions = {
  files: Accessor<RenderedFile[]>;
  manifestFileCount: Accessor<number>;
  diffRevision: Accessor<number>;
  onDevirtualizeFile: (file: RenderedFile) => void;
  onSelectionClear: () => void;
  onSelectionChange?: (selection: {
    target: HunkSelection;
    selected: HTMLElement;
  }) => void;
};

const READING_LINE_RATIO = 0.5;
const RICH_PRELOAD_FILE_RADIUS = 2;

type HunkAnchor = HTMLElement;
export type HunkIdentity = {
  fileIndex: number;
  hunkIndex: number;
};
export type HunkSelection =
  | {
      fileIndex: number;
      hunkIndex: number;
    }
  | {
      fileIndex: number;
      hunkIndex: null;
      entryDirection: 1 | -1;
    };
export type HunkPosition = {
  current: number;
  total: number;
  incomplete: boolean;
  selected: HunkSelection | null;
};

/**
 * Return the eager file-card ids kept rich around `activeFileId`.
 *
 * The result always includes the first and last eager files. When the active id
 * belongs to an eager file, it also includes the circular two-file neighborhood
 * on either side. An absent or lazy active id receives only the boundary files.
 */
export function richPreloadFileIdsForFileId(
  activeFileId: string | null,
  files: RenderedFile[],
): string[] {
  const preloadFileIds = files
    .filter(([, file]) => file.lazy === null)
    .map(([, file]) => fileElementId(fileKey(file)));
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
 * DOM order is used only for viewport geometry. Identity, navigation order,
 * and counters come from backend file/hunk coordinates.
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
 * Return the currently mounted hunk anchors used for geometry and resolution.
 *
 * The alias gives navigation code the `HunkAnchor` vocabulary while delegating
 * the actual DOM query and root fallback to the functions above.
 */
function hunkAnchors(root: ParentNode | undefined): HunkAnchor[] {
  return hunkAnchorElements(root);
}

/** Return every DOM element that can carry explicit navigation selection. */
function selectionAnchorElements(root: ParentNode | undefined): HTMLElement[] {
  return [
    ...hunkAnchorElements(root),
    ...hunkAnchorRoot(root).querySelectorAll<HTMLElement>(".lazy-hunk-anchor"),
  ];
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
 * `.hunk-skip` anchors preserve folded/collapsed geometry but are deliberately
 * excluded as navigation destinations and scroll-follow results.
 */
function anchorIsSelectable(anchor: HTMLElement): boolean {
  return !anchor.classList.contains("hunk-skip");
}

/**
 * Read and validate the snapshot-local identity rendered on a hunk anchor.
 *
 * Every rich, virtual, inline, split, or folded representation must expose
 * both non-negative coordinates. Missing or malformed data is an application
 * error rather than permission to infer identity from DOM order.
 */
function hunkIdentityForAnchor(anchor: HunkAnchor): HunkIdentity {
  const fileIndex = Number(anchor.dataset.fileIndex);
  const hunkIndex = Number(anchor.dataset.hunkIndex);
  if (
    !Number.isInteger(fileIndex) ||
    fileIndex < 0 ||
    !Number.isInteger(hunkIndex) ||
    hunkIndex < 0
  ) {
    throw new Error("Hunk anchor has an invalid file/hunk identity.");
  }
  return { fileIndex, hunkIndex };
}

/**
 * Return whether two snapshot-local identities name the same hunk.
 *
 * Equality deliberately ignores DOM nodes and derived global positions, both
 * of which may change while the file/hunk coordinates remain selected.
 */
function hunkIdentitiesEqual(
  left: HunkSelection | null,
  right: HunkSelection | null,
): boolean {
  if (left === null || right === null) {
    return left === right;
  }
  if (
    left.fileIndex !== right.fileIndex ||
    left.hunkIndex !== right.hunkIndex
  ) {
    return false;
  }
  if (left.hunkIndex !== null && right.hunkIndex !== null) {
    return true;
  }
  if (left.hunkIndex === null && right.hunkIndex === null) {
    return left.entryDirection === right.entryDirection;
  }
  return false;
}

/**
 * Resolve one identity against the current DOM and reject duplicate anchors.
 *
 * Node replacement is expected, so callers query on demand. At most one
 * selectable representation may exist for a hunk in the active view.
 */
function anchorForHunkIdentity(
  root: ParentNode | undefined,
  identity: HunkIdentity,
): HunkAnchor | null {
  const selector = [
    ".hunk-anchor:not(.hunk-skip)",
    `[data-file-index="${identity.fileIndex}"]`,
    `[data-hunk-index="${identity.hunkIndex}"]`,
  ].join("");
  const matches = [
    ...hunkAnchorRoot(root).querySelectorAll<HTMLElement>(selector),
  ];
  if (matches.length > 1) {
    throw new Error(
      `Duplicate selectable hunk identity: ${identity.fileIndex}:${identity.hunkIndex}.`,
    );
  }
  return matches[0] ?? null;
}

/**
 * Resolve the visible lazy plank representing one unresolved file.
 *
 * Lazy planks are navigation-only pseudo-hunks. They deliberately are not
 * `.hunk-anchor` elements, so ordinary scroll-follow does not mistake a file
 * with unknown hunks for a backend hunk.
 */
function anchorForLazyFile(
  root: ParentNode | undefined,
  fileIndex: number,
): HTMLElement | null {
  const selector = `.lazy-hunk-anchor[data-file-index="${fileIndex}"]`;
  const matches = [
    ...hunkAnchorRoot(root).querySelectorAll<HTMLElement>(selector),
  ];
  if (matches.length > 1) {
    throw new Error(`Duplicate lazy pseudo-hunk for file ${fileIndex}.`);
  }
  return matches[0] ?? null;
}

/** Return the mounted DOM target for a real hunk or lazy pseudo-hunk. */
function anchorForSelection(
  root: ParentNode | undefined,
  selection: HunkSelection,
): HTMLElement | null {
  if (selection.hunkIndex === null) {
    return anchorForLazyFile(root, selection.fileIndex);
  }
  return anchorForHunkIdentity(root, selection);
}

/**
 * Return the exact backend hunk count, or `null` for an unresolved file.
 *
 * Zero is a completed file diff with no hunks. `null` is kept distinct so the
 * global counter can display `+` and navigation can request lazy hydration.
 */
function fileHunkCount(file: RenderedFileEntry): number | null {
  if (file.hunk_count === undefined) {
    return null;
  }
  return file.hunk_count;
}

/**
 * Apply the active-hunk DOM state for one identity and optionally center it.
 *
 * Returns `null` when its representation is not mounted yet. The returned
 * element is otherwise the exact node marked `active-hunk` and `aria-current`.
 */
function selectCurrentHunk(options: {
  selection: HunkSelection;
  scroll: boolean;
  root: ParentNode | undefined;
}): HTMLElement | null {
  const anchorElements = selectionAnchorElements(options.root);
  const selected = anchorForSelection(options.root, options.selection);
  if (selected === null) {
    return null;
  }
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
  options: HunkNavigationOptions,
) {
  const [currentIdentity, setCurrentIdentity] =
    createSignal<HunkSelection | null>(null);
  const [position, setPosition] = createSignal<HunkPosition>({
    current: 0,
    total: 0,
    incomplete: false,
    selected: null,
  });
  let reconcileTimer: number | null = null;
  let selectedRevision = options.diffRevision();
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
   * Derive the public global counter from backend-owned per-file hunk counts.
   *
   * Each unresolved rendered file contributes exactly one provisional lazy
   * pseudo-hunk and marks the counter incomplete. The selected target remains
   * stable when a directly hydrated earlier file replaces its provisional slot
   * with exact backend hunks.
   */
  const syncPosition = (identity: HunkSelection | null) => {
    const currentFiles = options.files();
    let total = 0;
    let current = 0;
    let identityFound = false;
    for (const [fileIndex, file] of currentFiles) {
      const hunkCount = fileHunkCount(file);
      if (hunkCount === null) {
        if (identity !== null && fileIndex === identity.fileIndex) {
          if (identity.hunkIndex !== null) {
            throw new Error(
              `Selected real hunk ${identity.fileIndex}:${identity.hunkIndex} belongs to an unresolved file.`,
            );
          }
          current = total + 1;
          identityFound = true;
        }
        total += 1;
        continue;
      }
      if (identity !== null && fileIndex === identity.fileIndex) {
        if (identity.hunkIndex === null) {
          if (hunkCount > 0) {
            current = total + (identity.entryDirection === 1 ? 1 : hunkCount);
            identityFound = true;
          }
        } else if (identity.hunkIndex >= hunkCount) {
          throw new Error(
            `Hunk ${identity.fileIndex}:${identity.hunkIndex} exceeds its file hunk count.`,
          );
        } else {
          current = total + identity.hunkIndex + 1;
          identityFound = true;
        }
      }
      total += hunkCount;
    }
    const resolvedFiles = currentFiles.filter(
      ([, file]) => fileHunkCount(file) !== null,
    ).length;
    const incomplete = resolvedFiles < options.manifestFileCount();
    setPosition({
      current: identityFound ? current : 0,
      total,
      incomplete,
      selected: identityFound ? identity : null,
    });
  };

  /**
   * Apply one real or lazy pseudo-hunk target and optionally notify the app.
   *
   * Hunk selection and `currentIdentity` writes are allowed only from:
   *
   * 1. `scrollNext`.
   * 2. `scrollPrev`.
   * 3. The functional `scroll` handler inside `followScroll`.
   *
   * Reconciliation, virtualization, rendering, and layout effects must not
   * choose a different hunk. If the requested target has no mounted
   * selectable representation, this function returns `false` and leaves the
   * selection unchanged.
   */
  const select = (selectionOptions: {
    target: HunkSelection;
    notify: boolean;
    scroll: boolean;
  }) => {
    const selected = selectCurrentHunk({
      selection: selectionOptions.target,
      scroll: selectionOptions.scroll,
      root: root(),
    });
    if (selected === null) {
      return false;
    }
    setCurrentIdentity(selectionOptions.target);
    syncPosition(selectionOptions.target);
    if (selectionOptions.notify) {
      options.onSelectionChange?.({
        target: selectionOptions.target,
        selected,
      });
    }
    return true;
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
   * Clear selection immediately when a new diff snapshot replaces the old one.
   *
   * Snapshot-local file/hunk coordinates cannot survive a revision change.
   * This invalidation must not share the delayed reconciliation timer: file
   * payloads may keep restarting that timer throughout a long reload, leaving
   * an old coordinate attached to unrelated content in the new snapshot.
   */
  const invalidateSelectionForRevision = () => {
    const revision = options.diffRevision();
    if (revision === selectedRevision) {
      return;
    }
    selectedRevision = revision;
    setCurrentIdentity(null);
    options.onSelectionClear();
    for (const anchor of selectionAnchorElements(root())) {
      anchor.classList.remove("active-hunk");
      anchor.removeAttribute("aria-current");
    }
    syncPosition(null);
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
    const candidateAnchorEntries = currentAnchors.flatMap((anchor) =>
      anchorIsSelectable(anchor) && anchorIsVisible(anchor) ? [{ anchor }] : [],
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
    const selected = eligibleAnchors.reduce((selected, candidate) => {
      const selectedTop = selected.anchor.getBoundingClientRect().top;
      const candidateTop = candidate.anchor.getBoundingClientRect().top;
      if (precedingAnchors.length > 0) {
        return candidateTop > selectedTop ? candidate : selected;
      }
      return candidateTop < selectedTop ? candidate : selected;
    });
    return hunkIdentityForAnchor(selected.anchor);
  };

  /**
   * Refresh the public hunk total after rendered anchors settle.
   *
   * The short delay coalesces Solid DOM updates. Reconciliation clamps the
   * displayed value from backend file counts without selecting a hunk or
   * moving the viewport.
   *
   * Invariant: reconciliation must never call `select`, explicit navigation,
   * `scrollIntoView`, or any other code that can choose a hunk or move the
   * viewport. Selection is changed only by Next, Previous, or recognized user
   * scrolling.
   */
  const reconcile = () => {
    cancelReconcileTimer();
    reconcileTimer = window.setTimeout(() => {
      reconcileTimer = null;
      const identity = currentIdentity();
      syncPosition(identity);
    }, 120);
  };

  /**
   * Return the rich DOM anchor for one real hunk navigation candidate.
   *
   * A virtual anchor identifies the target but is never selected or scrolled.
   * The file is forced rich first, then the same backend identity is resolved
   * again. Solid rendering is synchronous here; retaining a virtual or missing
   * anchor after devirtualization is an application error.
   */
  const richAnchorForNavigation = (
    file: RenderedFile,
    identity: HunkIdentity,
  ): HunkAnchor | null => {
    let anchor = anchorForHunkIdentity(root(), identity);
    if (anchor === null) {
      return null;
    }
    if (!anchor.classList.contains("virtual-hunk-anchor")) {
      return anchor;
    }
    options.onDevirtualizeFile(file);
    anchor = anchorForHunkIdentity(root(), identity);
    if (anchor === null || anchor.classList.contains("virtual-hunk-anchor")) {
      throw new Error(
        `File ${identity.fileIndex} did not render rich hunk ${identity.hunkIndex} synchronously.`,
      );
    }
    return anchor;
  };

  /**
   * Return the next/previous navigation target in manifest and hunk order.
   *
   * Real hunk targets are forced rich before they can be returned. Folded
   * targets have only `.hunk-skip` anchors and are skipped. An unresolved lazy
   * file contributes one pseudo-hunk that scrolls to its plank without
   * hydrating it.
   */
  const explicitNavigationTarget = (
    direction: 1 | -1,
  ): HunkSelection | null => {
    const identity = currentIdentity();
    const filesByIndex = new Map(
      options.files().map(([fileIndex, file]) => [fileIndex, file] as const),
    );
    if (identity !== null && identity.hunkIndex !== null) {
      const currentFile = filesByIndex.get(identity.fileIndex);
      if (currentFile === undefined) {
        return null;
      }
      const currentAnchor = richAnchorForNavigation(
        [identity.fileIndex, currentFile],
        identity,
      );
      if (currentAnchor !== null && !anchorIsVisible(currentAnchor)) {
        return identity;
      }
    } else if (identity !== null) {
      const currentAnchor = anchorForLazyFile(root(), identity.fileIndex);
      if (currentAnchor !== null && !anchorIsVisible(currentAnchor)) {
        return identity;
      }
    }

    const fileCount = options.manifestFileCount();
    if (fileCount === 0) {
      throw new Error("Hunk navigation requires a loaded file manifest.");
    }
    const initialFileIndex =
      identity?.fileIndex ?? (direction === 1 ? 0 : fileCount - 1);
    const visitedFileSteps = fileCount + (identity === null ? 0 : 1);
    for (let fileStep = 0; fileStep < visitedFileSteps; fileStep += 1) {
      const fileIndex = wrapIndex(
        initialFileIndex + direction * fileStep,
        fileCount,
      );
      const file = filesByIndex.get(fileIndex);
      if (file === undefined) {
        return null;
      }
      const hunkCount = fileHunkCount(file);
      if (hunkCount === null) {
        if (
          identity !== null &&
          identity.hunkIndex === null &&
          identity.fileIndex === fileIndex &&
          fileStep === 0
        ) {
          continue;
        }
        if (anchorForLazyFile(root(), fileIndex) === null) {
          return null;
        }
        return { fileIndex, hunkIndex: null, entryDirection: direction };
      }
      if (hunkCount === 0) {
        continue;
      }

      let startHunkIndex = direction === 1 ? 0 : hunkCount - 1;
      if (
        identity !== null &&
        fileIndex === identity.fileIndex &&
        fileStep === 0
      ) {
        if (identity.hunkIndex === null) {
          if (direction !== identity.entryDirection) {
            continue;
          }
        } else {
          startHunkIndex = identity.hunkIndex + direction;
        }
      }
      for (
        let hunkIndex = startHunkIndex;
        hunkIndex >= 0 && hunkIndex < hunkCount;
        hunkIndex += direction
      ) {
        const candidate = { fileIndex, hunkIndex };
        if (richAnchorForNavigation([fileIndex, file], candidate) === null) {
          continue;
        }
        return candidate;
      }
    }
    return null;
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
    const target = explicitNavigationTarget(1);
    if (target === null) {
      return;
    }
    select({ target, notify: true, scroll: true });
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
    const target = explicitNavigationTarget(-1);
    if (target === null) {
      return;
    }
    select({ target, notify: true, scroll: true });
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
   * 4. Reapply selection when the identity changed or its current anchor is
   *    not the active DOM hunk.
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
        const nextIdentity = hunkIndexAtReadingLine(currentAnchors);
        if (nextIdentity === null) {
          return;
        }
        const selectedAnchor = anchorForHunkIdentity(root(), nextIdentity);
        if (
          hunkIdentitiesEqual(nextIdentity, currentIdentity()) &&
          selectedAnchor?.classList.contains("active-hunk") === true
        ) {
          return;
        }

        // Virtual/rich replacement may replace the DOM node for the same
        // identity. Re-select that node so the header, file-tree highlight, and
        // preload files match what the user sees. `scroll: false` preserves the
        // user's viewport.
        select({ target: nextIdentity, notify: true, scroll: false });
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
      invalidateSelectionForRevision();
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
