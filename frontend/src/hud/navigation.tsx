/**
 * Provides explicit DOM-backed hunk navigation for one mounted ChangeSet.
 *
 * The module exports hunk identity contracts, the closed navigation operation
 * union, one ChangeSet-scoped Provider, and its checked context accessor.
 * Renderers write identity fields directly into their own DOM; this module
 * reads those attributes only while handling an explicit operation or recognized
 * user scrolling. Exactly four callers may call the private `selectHunk`
 * operation: `nextHunk`, `prevHunk`, `scrollFollow`, and — as the one
 * initialization exception — the exported `writeInitialHunkSelection` that a
 * freshly mounted snapshot invokes. The module must not retain selected identity,
 * build a hunk registry, calculate counters, change FileTree expansion, parse
 * or retain line-pin identity, or fetch files.
 */
import {
  createContext,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";
import { useToasts } from "../comp/Toasts";
import { assert, expect } from "../utils";
import type { LinePinTarget, PreparedLine } from "./linePins";

const PARTICIPATING_HUNK_SELECTOR = "[data-hunk-target]:not(.skip)";

/**
 * Identifies one backend-produced hunk boundary inside a manifest file.
 *
 * The coordinate is compound: `bay` is the composed bay key and
 * `hunkIndex` is the bay-local index composition published, written
 * verbatim — no renderer renumbers hunks into a file-wide sequence. Renderers
 * construct this value locally and write its fields directly into DOM
 * attributes. The value itself is never stored after rendering.
 */
export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  bay: string;
  hunkIndex: number;
};

/**
 * Identifies one file-state pseudo-hunk: a Husk, Lazy, or zero-hunk target.
 *
 * These targets represent one complete file state rather than a hunk, so they
 * carry no bay and require hunk index zero. Renderers write every field
 * directly into DOM attributes; `kind` never replaces a coordinate.
 */
export type FileStateHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy" | "zero";
  hunkIndex: 0;
};

/**
 * Identifies one coordinate-preserving skipped hunk of a collapsed file.
 *
 * A skip target replaces one real hunk while its file is collapsed, so it
 * preserves that hunk's exact bay key and bay-local index. Navigation
 * traverses past it while the coordinates keep selected identity stable.
 */
export type SkippedHunkIdentity = {
  fileIndex: number;
  kind: "skip";
  bay: string;
  hunkIndex: number;
};

/**
 * Describes every non-real hunk identity a renderer may write into the DOM.
 *
 * File-state pseudo-hunks stand in for a whole file; skipped hunks preserve
 * one collapsed real coordinate. Both participate in the same DOM contract as
 * real hunks.
 */
export type PseudoHunkIdentity = FileStateHunkIdentity | SkippedHunkIdentity;

/**
 * Describes every concrete hunk identity a renderer may write into the DOM.
 *
 * This is a render-time contract rather than application state. After JSX or
 * imperative DOM construction, the attributes on the target are authoritative.
 */
export type HunkIdentity = RealHunkIdentity | PseudoHunkIdentity;

/**
 * Describes every explicit operation supported by one Navigation instance.
 *
 * Relative operations use current selected DOM identity. File navigation scrolls
 * to one manifest file's first current DOM target without selecting. Line
 * navigation requires one manifest index, one complete `LinePinTarget` — the
 * File pair, the composed bay key, a side, and a backend line — and the
 * caller's AbortSignal lifetime; it never selects a hunk. Top scrolls the page.
 */
export type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "file"; fileIndex: number }
  | {
      kind: "line";
      fileIndex: number;
      target: LinePinTarget;
      abortSignal: AbortSignal;
    }
  | { kind: "top" };

/**
 * Describes whether one explicit Navigation operation reached its destination.
 *
 * `complete` includes valid hunk-navigation no-ops. `missing` means an exact
 * prepared line is absent from its complete current file. `stopped` means the
 * caller or provider lifetime ended before its final action. The result never
 * hides an exception or treats cancellation as a missing coordinate.
 */
export type NavigationResult =
  | { state: "complete" }
  | { state: "missing" }
  | { state: "stopped" };

/**
 * Exposes the complete explicit navigation API for one mounted ChangeSet.
 *
 * Calls resolve after any required rich materialization, selection when the
 * operation requires it, and scroll. Recognized user scrolling may select only
 * rich participating real hunks. A disposed instance performs no later DOM write
 * or scroll. `root` returns the mounted ChangeSet root this instance serves,
 * so consumers that must locate navigated DOM afterwards query inside the
 * same root the navigation itself used, in every view.
 */
export type Navigation = {
  navigate(command: NavigationCommand): Promise<NavigationResult>;
  root: Accessor<HTMLElement>;
};

/**
 * Defines the required DOM root and descendants served by one Provider.
 *
 * The accessor must return this mounted ChangeSet's root. Target queries remain
 * inside that root; document scrolling supplies only viewport events and point
 * hit-testing. Children must remain inside the root.
 */
export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};

/**
 * Describes the line-preparation operation attached by FullFile.
 *
 * Every mounted FullFile exposes it as one DOM interface on its FileCard. The
 * operation prepares one exact backend line inside that card and never changes
 * selected identity, counters, or scroll position.
 */
type PreparableFileCard = HTMLElement & {
  prepareLine_impl: (
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ) => Promise<PreparedLine>;
};

/**
 * Describes the navigation geometry and rich-materialization operations
 * attached by a mounted text bay to its wrapper element.
 *
 * Every element carrying `data-bay-render` exposes both methods for exactly
 * its mounted lifetime. `waitToEnrich_impl()` materializes the bay's rich
 * grid; navigation calls `intersectsRichEntryZone()` only for a currently
 * virtual wrapper. Neither method selects, expands, calculates counters, or
 * scrolls.
 */
type EnrichableBay = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
};

const NavigationContext = createContext<Navigation>();

/**
 * Returns the Navigation instance owned by the nearest mounted NavigationProvider.
 *
 * Consumers must render under NavigationProvider. Missing context is a module
 * contract violation and throws instead of constructing an unrelated instance.
 */
export function useNavigation(): Navigation {
  const navigation = useContext(NavigationContext);
  if (navigation === undefined) {
    throw new Error("Hunk navigation requires NavigationProvider.");
  }
  return navigation;
}

/**
 * Selects one concrete hunk target by mutating only authoritative DOM.
 *
 * Exactly four callers exist, each calling directly: `nextHunk`, `prevHunk`,
 * and `scrollFollow` change an existing selection, and
 * `writeInitialHunkSelection` is the explicit initialization exception for a
 * freshly mounted snapshot. Existing FileCard identity and visible decoration
 * are removed before the target fields are copied onto its stable FileCard.
 */
function selectHunk(root: HTMLElement, target: HTMLElement): void {
  if (!target.matches("[data-hunk-target]")) {
    throw new Error("Selected element is not a hunk target.");
  }
  if (!root.contains(target)) {
    throw new Error("Selected hunk target belongs to another ChangeSet.");
  }
  const fileCard = target.closest<HTMLElement>("[data-file-card]");
  if (fileCard === null || !root.contains(fileCard)) {
    throw new Error("Selected hunk target has no owning FileCard.");
  }
  const kind = target.dataset.hunkKind;
  const fileIndex = target.dataset.fileIndex;
  if (
    kind !== "real" &&
    kind !== "husk" &&
    kind !== "lazy" &&
    kind !== "zero" &&
    kind !== "skip"
  ) {
    throw new Error("Hunk target has an invalid kind.");
  }
  if (fileIndex === undefined || fileCard.dataset.fileIndex !== fileIndex) {
    throw new Error("Hunk target and FileCard indices do not match.");
  }
  if (!/^(?:0|[1-9]\d*)$/.test(fileIndex)) {
    throw new Error("Hunk target has an invalid file index.");
  }
  const hunkIndex = target.dataset.hunkIndex;
  if (hunkIndex === undefined) {
    throw new Error("Hunk target is missing its hunk index.");
  }
  if (!/^(?:0|[1-9]\d*)$/.test(hunkIndex)) {
    throw new Error("Hunk target has an invalid hunk index.");
  }
  const bay = target.dataset.hunkBay;
  if (kind === "real" || kind === "skip") {
    if (bay === undefined || bay.length === 0) {
      throw new Error(`${kind} hunk target requires a bay key.`);
    }
  } else {
    if (bay !== undefined) {
      throw new Error(`${kind} pseudo-hunk must not carry a bay key.`);
    }
    if (hunkIndex !== "0") {
      throw new Error(`${kind} pseudo-hunk requires hunk index zero.`);
    }
  }

  for (const previousTarget of root.querySelectorAll<HTMLElement>(
    "[data-hunk-target][data-selected]",
  )) {
    previousTarget.removeAttribute("data-selected");
    previousTarget.removeAttribute("aria-current");
    previousTarget.classList.remove("active-hunk");
  }
  for (const previousCard of root.querySelectorAll<HTMLElement>(
    "[data-file-card][data-selected-hunk-index]",
  )) {
    delete previousCard.dataset.selectedHunkIndex;
    delete previousCard.dataset.selectedHunkBay;
    delete previousCard.dataset.selectedHunkKind;
    previousCard.classList.remove("active-hunk");
  }

  fileCard.dataset.selectedHunkKind = kind;
  fileCard.dataset.selectedHunkIndex = hunkIndex;
  if (bay === undefined) {
    delete fileCard.dataset.selectedHunkBay;
  } else {
    fileCard.dataset.selectedHunkBay = bay;
  }
  fileCard.classList.add("active-hunk");
  target.setAttribute("data-selected", "");
  target.setAttribute("aria-current", "true");
  target.classList.add("active-hunk");
}

/**
 * Resolves one FileCard's stored selected identity to its current hunk target.
 *
 * selectHunk() writes the data-selected-hunk-* attributes, so this module
 * also owns reading them back; navigation and the hunk display observer both
 * resolve through this one operation. The declared kind picks the resolution
 * strategy: a file-state selection (husk, lazy, zero) names the File's own
 * stop — the first of `targets` in DOM order, which is how a Husk-time
 * selection survives admission into composed DOM — while a hunk selection
 * (real, skip) matches strictly on bay key and bay-local hunk index,
 * with the kind excluded from that match because collapse and expansion
 * interconvert real and skip targets at the same coordinates.
 *
 * `targets` must be the card's hunk targets in DOM order. Every identity
 * defect — a missing or invalid attribute, a contradictory kind/bay/index
 * combination, zero or duplicate matches, a file-index mismatch — throws.
 */
export function storedHunkTarget(
  card: HTMLElement,
  targets: readonly HTMLElement[],
): HTMLElement {
  const kind = card.dataset.selectedHunkKind;
  const bay = card.dataset.selectedHunkBay;
  const hunkIndex = card.dataset.selectedHunkIndex;
  const fileIndex = card.dataset.fileIndex;
  assert(
    kind !== undefined && hunkIndex !== undefined && fileIndex !== undefined,
    "Selected FileCard has incomplete hunk identity.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(fileIndex) && /^(?:0|[1-9]\d*)$/.test(hunkIndex),
    "Selected FileCard has an invalid hunk coordinate.",
  );
  if (kind === "husk" || kind === "lazy" || kind === "zero") {
    assert(
      bay === undefined && hunkIndex === "0",
      `Selected ${kind} pseudo-hunk must be the bayless file-state zero stop.`,
    );
    const target = expect(
      targets[0],
      `Selected hunk (${fileIndex}, ${kind}, 0) has no first DOM target.`,
    );
    assert(
      target.dataset.fileIndex === fileIndex,
      `Selected hunk (${fileIndex}, ${kind}, 0) resolves to another file's target.`,
    );
    return target;
  }
  assert(
    kind === "real" || kind === "skip",
    `Selected FileCard declares invalid hunk kind "${kind}".`,
  );
  assert(
    bay !== undefined,
    `Selected ${kind} hunk (${fileIndex}, ${hunkIndex}) is missing its bay key.`,
  );
  const matchingTargets = targets.filter(
    (candidate) =>
      candidate.dataset.fileIndex === fileIndex &&
      candidate.dataset.hunkBay === bay &&
      candidate.dataset.hunkIndex === hunkIndex,
  );
  assert(
    matchingTargets.length === 1,
    `Selected hunk (${fileIndex}, ${bay}, ${hunkIndex}) requires exactly one DOM target.`,
  );
  return expect(
    matchingTargets[0],
    "Selected hunk target disappeared during resolution.",
  );
}

/**
 * Writes the initial hunk selection directly into one mounted snapshot root.
 *
 * ChangeSetSnapshot calls this once after its FileCards mount, so every
 * snapshot replacement — including an engine switch under a surviving
 * NavigationProvider — starts with the first FileCard's required first hunk
 * selected. An empty snapshot and terminal renderer damage stay unselected.
 * Initial selection is part of mounting the authoritative DOM; it is the one
 * sanctioned initialization caller of `selectHunk`, and the fresh snapshot is
 * asserted unselected before that single write.
 */
export function writeInitialHunkSelection(root: HTMLElement): void {
  if (root.querySelector("[data-file-render-error]") !== null) {
    // A critical renderer failure is terminal local damage. Initialization
    // must not synthesize a target, repair selection, or escalate the error.
    return;
  }
  const cards = root.querySelectorAll<HTMLElement>("[data-file-card]");
  if (cards.length === 0) {
    return;
  }
  for (const card of cards) {
    if (card.querySelector("[data-hunk-target]") === null) {
      throw new Error("Every FileCard requires a hunk target.");
    }
  }
  const firstCard = cards[0];
  if (firstCard === undefined) {
    throw new Error("Non-empty FileCard collection has no first card.");
  }
  const firstFileIndex = firstCard.dataset.fileIndex;
  if (firstFileIndex === undefined) {
    throw new Error("First FileCard has no manifest file index.");
  }
  // Bays each number their hunks from zero, so an index alone names no
  // single target; the snapshot's first hunk is the first target in DOM
  // order, which renderers keep equal to document order.
  const firstTarget =
    firstCard.querySelector<HTMLElement>("[data-hunk-target]");
  if (firstTarget === null) {
    throw new Error("First hunk target disappeared during initialization.");
  }
  if (firstTarget.dataset.fileIndex !== firstFileIndex) {
    throw new Error("First hunk target has the wrong manifest index.");
  }
  if (
    root.querySelector(
      "[data-hunk-target][data-selected], [data-file-card][data-selected-hunk-index]",
    ) !== null
  ) {
    throw new Error("Initial hunk selection requires unselected DOM.");
  }
  selectHunk(root, firstTarget);
}

/**
 * Provides one disposable explicit-navigation instance for one ChangeSet root.
 *
 * The mounted snapshot owns initial selection through
 * `writeInitialHunkSelection`; this provider's operations read current DOM
 * identity and retain no selected-hunk state. Recognized browser scrolling
 * selects rich real hunks at the reading line, while cleanup prevents pending
 * browser work from changing the page.
 */
export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element {
  const toast = useToasts();
  let alive = true;

  /**
   * Records whether recognized user input currently permits scroll-follow.
   *
   * The guard contains no DOM policy. Callers reject input that cannot move the
   * document before calling `input()`. `ok()` only reads the current state.
   */
  const scrollGuard = (() => {
    let state: "idle" | "input" | "document" = "idle";
    let pendingExpiryHandle: number | null = null;

    /**
     * Cancels an expiry shared by replacement input and resulting document scroll.
     */
    function cancelPendingExpiry(): void {
      if (pendingExpiryHandle === null) {
        return;
      }

      cancelAnimationFrame(pendingExpiryHandle);
      pendingExpiryHandle = null;
    }

    return {
      /**
       * Records recognized input that can move the document.
       *
       * Wheel and touch permission expires before the next repaint when no scroll
       * occurs. Keyboard permission remains until `stop()`.
       */
      input(input: "wheel" | "touch" | "keyboard"): void {
        cancelPendingExpiry();
        state = "input";

        if (input === "keyboard") {
          return;
        }

        pendingExpiryHandle = requestAnimationFrame(() => {
          pendingExpiryHandle = null;
          state = "idle";
        });
      },

      /**
       * Records where scrolling occurred after recognized input.
       *
       * A nested scroll rejects input before document scrolling starts. Once the
       * document is scrolling, nested FileTree movement may follow selection
       * without ending the document sequence.
       */
      scrolled(scroller: "document" | "nested"): void {
        if (scroller === "nested") {
          state = state === "input" ? "idle" : state;
          return;
        }

        cancelPendingExpiry();
        state = "document";
      },

      /**
       * Reports whether document scrolling may update hunk selection.
       */
      ok(): boolean {
        return state !== "idle";
      },

      /**
       * Prohibits subsequent document scrolling from updating hunk selection.
       */
      stop(): void {
        state = "idle";
      },
    };
  })();

  /**
   * Tracks vertical movement between consecutive events in one touch sequence.
   *
   * Directions describe document movement: an upward-moving finger scrolls the
   * document down. Horizontal movement has no vertical direction.
   */
  const touchController = (() => {
    let previousTouchY: number | null = null;

    /**
     * Extracts the required primary touch position from a touch event.
     */
    function primaryTouchY(event: TouchEvent): number {
      const touch = event.touches.item(0);

      if (touch === null) {
        throw new Error("Touch input requires a primary touch.");
      }

      return touch.clientY;
    }

    return {
      /**
       * Records the initial vertical position for a touch sequence.
       */
      set(event: TouchEvent): void {
        previousTouchY = primaryTouchY(event);
      },

      /**
       * Compares the current touch with the preceding position and advances it.
       *
       * `null` means that the touch moved without changing its vertical position.
       */
      comparedDirection(event: TouchEvent): "up" | "down" | null {
        if (previousTouchY === null) {
          throw new Error(
            "Touch movement requires a preceding touch position.",
          );
        }

        const currentTouchY = primaryTouchY(event);
        const previousY = previousTouchY;
        previousTouchY = currentTouchY;

        if (currentTouchY === previousY) {
          return null;
        }

        return currentTouchY < previousY ? "down" : "up";
      },
    };
  })();

  /**
   * Follows recognized user scrolling by selecting the hunk at the reading line.
   *
   * The calculation hit-tests the visible file list, considers only rich,
   * participating real targets in the intersected FileCard, and acts directly
   * through `selectHunk`. Virtual real targets, pseudo-hunks, skipped targets,
   * and FileCards with no eligible visible target leave selection unchanged.
   * This operation never scrolls, enriches, expands, fetches, or reads counters.
   */
  function scrollFollow(root: HTMLElement): void {
    const fileList = root.querySelector<HTMLElement>(".file-list");
    if (fileList === null) {
      throw new Error("Scroll following requires the ChangeSet file list.");
    }
    const fileListRect = fileList.getBoundingClientRect();
    const visibleLeft = Math.max(0, fileListRect.left);
    const visibleRight = Math.min(window.innerWidth, fileListRect.right);
    if (visibleRight <= visibleLeft || window.innerHeight <= 0) {
      return;
    }
    const readingLineY = window.innerHeight * 0.5;
    const readingLineX = visibleLeft + (visibleRight - visibleLeft) * 0.5;
    let readingCard: HTMLElement | null = null;
    for (const element of document.elementsFromPoint(
      readingLineX,
      readingLineY,
    )) {
      const card = element.closest<HTMLElement>("[data-file-card]");
      if (card !== null && root.contains(card)) {
        readingCard = card;
        break;
      }
    }
    if (readingCard === null) {
      return;
    }

    const targets = readingCard.querySelectorAll<HTMLElement>(
      '[data-hunk-target][data-hunk-kind="real"]:not(.skip):not(.virtual-hunk-anchor)',
    );
    if (targets.length === 0) {
      return;
    }
    // Targets are row elements in document order, so their tops are strictly
    // increasing: binary-search the last target at or above the reading line
    // instead of measuring every hunk row in the card on each followed
    // scroll. Uniform row heights make the visibility screen below identical
    // to the former every-target scan. Probed targets may sit inside a
    // `.diff-row-chunk` whose `content-visibility: auto` currently skips it;
    // rect queries then force that chunk's layout and return real geometry
    // (measured ~0.1ms per probe), which this search depends on. A zero rect
    // would break monotonicity silently, so the chunk style must stay
    // `auto`, never `hidden` — see the .diff-row-chunk rule in styles.css.
    let low = 0;
    let high = targets.length - 1;
    let precedingIndex = -1;
    while (low <= high) {
      const middle = (low + high) >> 1;
      const candidate = expect(
        targets[middle],
        "Hunk target disappeared during scroll following.",
      );
      if (candidate.getBoundingClientRect().top <= readingLineY) {
        precedingIndex = middle;
        low = middle + 1;
      } else {
        high = middle - 1;
      }
    }

    let target: HTMLElement | null = null;
    if (precedingIndex >= 0) {
      const preceding = expect(
        targets[precedingIndex],
        "Hunk target disappeared during scroll following.",
      );
      const rect = preceding.getBoundingClientRect();
      if (rect.bottom > 0 && rect.top < window.innerHeight) {
        target = preceding;
      }
    }
    if (target === null && precedingIndex + 1 < targets.length) {
      const following = expect(
        targets[precedingIndex + 1],
        "Hunk target disappeared during scroll following.",
      );
      const rect = following.getBoundingClientRect();
      if (rect.bottom > 0 && rect.top < window.innerHeight) {
        target = following;
      }
    }
    if (target === null || target.hasAttribute("data-selected")) {
      return;
    }
    selectHunk(root, target);
  }

  /**
   * Resolves the exact selected hunk target from its FileCard identity.
   *
   * Missing, contradictory, or duplicate coordinates are application errors;
   * this operation never substitutes a FileCard header or another target.
   */
  function selectedLocation(root: HTMLElement): {
    card: HTMLElement;
    target: HTMLElement;
  } {
    const cards = root.querySelectorAll<HTMLElement>(
      "[data-file-card][data-selected-hunk-index]",
    );
    assert(
      cards.length === 1,
      "A non-empty ChangeSet requires exactly one selected hunk.",
    );
    const card = expect(
      cards[0],
      "Selected FileCard disappeared during navigation.",
    );
    const targets = Array.from(
      card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
    );
    return { card, target: storedHunkTarget(card, targets) };
  }

  /**
   * Waits for one bay wrapper to expose required rich DOM with real geometry.
   *
   * The wrapper is the element carrying `data-bay-render`; its mounted bay
   * attaches the operation for exactly the wrapper's lifetime. A wrapper
   * without the operation violates its DOM interface and throws instead of
   * silently skipping enrichment.
   */
  async function waitToEnrich(bay: HTMLElement): Promise<void> {
    const enrichableBay = bay as Partial<EnrichableBay>;
    if (typeof enrichableBay.waitToEnrich_impl !== "function") {
      throw new Error("Bay omitted waitToEnrich_impl.");
    }
    await enrichableBay.waitToEnrich_impl();
  }

  /**
   * Resolves one participating destination after optional rich materialization.
   *
   * Virtual targets are identified by their primitive attributes, the owning
   * bay alone is enriched directly, and the replacement target is resolved
   * again before returning it. This operation never selects or scrolls.
   */
  async function enrichTarget(
    root: HTMLElement,
    initialTarget: HTMLElement,
  ): Promise<HTMLElement | null> {
    if (!initialTarget.matches(PARTICIPATING_HUNK_SELECTOR)) {
      throw new Error("Navigation destination does not participate.");
    }
    const card = initialTarget.closest<HTMLElement>("[data-file-card]");
    if (card === null || !root.contains(card)) {
      throw new Error("Navigation destination has no owning FileCard.");
    }
    const fileIndex = initialTarget.dataset.fileIndex;
    const hunkIndex = initialTarget.dataset.hunkIndex;
    const hunkBay = initialTarget.dataset.hunkBay;
    if (
      fileIndex === undefined ||
      hunkIndex === undefined ||
      !/^(?:0|[1-9]\d*)$/.test(fileIndex) ||
      !/^(?:0|[1-9]\d*)$/.test(hunkIndex) ||
      card.dataset.fileIndex !== fileIndex
    ) {
      throw new Error("Navigation destination has invalid hunk coordinates.");
    }
    let target = initialTarget;
    // Only a target inside a bay wrapper can be virtual: pseudo-hunks, bay
    // chrome anchors, and collapsed-file skips live outside every wrapper
    // and already expose their complete representation.
    const bay = initialTarget.closest<HTMLElement>("[data-bay-render]");
    if (
      bay !== null &&
      bay.dataset.bayRender === "virtual" &&
      initialTarget.dataset.hunkKind === "real"
    ) {
      const kind = initialTarget.dataset.hunkKind;
      if (hunkBay === undefined) {
        throw new Error("Real navigation destination is missing its bay.");
      }
      await waitToEnrich(bay);
      if (!alive) {
        return null;
      }
      const replacements = Array.from(
        card.querySelectorAll<HTMLElement>(PARTICIPATING_HUNK_SELECTOR),
      ).filter(
        (candidate) =>
          candidate.dataset.hunkKind === kind &&
          candidate.dataset.fileIndex === fileIndex &&
          candidate.dataset.hunkBay === hunkBay &&
          candidate.dataset.hunkIndex === hunkIndex,
      );
      if (replacements.length !== 1) {
        throw new Error(
          "Rich FullFile did not mount one matching hunk target.",
        );
      }
      const replacement = replacements[0];
      if (replacement === undefined) {
        throw new Error("Rich hunk target disappeared during navigation.");
      }
      target = replacement;
    }
    if (!alive) {
      return null;
    }
    return target;
  }

  /**
   * Centers one target and re-centers it until its geometry holds still.
   *
   * content-visibility chunks near the destination render only after the
   * first scroll and replace their estimated heights with real ones (wrapped
   * lines are taller than the estimate), shifting the document under the
   * viewport; each pass waits one frame and re-centers until the target
   * stops moving, within a small bound. The operation performs no selection.
   */
  async function settleCenteredScroll(target: HTMLElement): Promise<void> {
    target.scrollIntoView({ block: "center", behavior: "instant" });
    // Chunks render lazily for several frames after arrival, so the loop
    // demands three consecutive still frames before trusting the position;
    // the pass bound keeps a pathological layout from pinning the scroll.
    let stillFrames = 0;
    for (let pass = 0; pass < 30 && alive; pass += 1) {
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (!target.isConnected) {
        return;
      }
      const rect = target.getBoundingClientRect();
      const offset = rect.top + rect.height / 2 - window.innerHeight / 2;
      if (Math.abs(offset) <= 1) {
        stillFrames += 1;
        if (stillFrames >= 3) {
          return;
        }
        continue;
      }
      stillFrames = 0;
      target.scrollIntoView({ block: "center", behavior: "instant" });
    }
  }

  /**
   * Scrolls to one manifest file's exact first current DOM target.
   *
   * The immutable file index must resolve to one stable FileCard. Every
   * representation exposes a first target. A transient Husk target violates the
   * caller contract because Husk navigation controls are disabled while it and
   * adjacent Husks have unstable layout. A virtual destination bay is enriched
   * and its target resolved again before Navigation calculates its hypothetical
   * centered viewport. Virtual bays intersecting their own exact rich-entry
   * zones at that position are enriched one at a time. The destination and
   * hypothetical viewport are recalculated after every layout change, and the
   * final centering re-runs until nearby chunk rendering stops moving the
   * destination. A local set bounds the operation to one enrichment per bay.
   * The operation never selects its destination, expands, collapses, fetches,
   * calculates counters, or updates the FileTree.
   */
  async function navigateToFile(fileIndex: number): Promise<void> {
    if (!Number.isInteger(fileIndex) || fileIndex < 0) {
      throw new Error("File navigation requires a valid manifest index.");
    }
    const root = props.root();
    const cards = root.querySelectorAll<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    if (cards.length !== 1) {
      throw new Error(
        `File navigation requires one FileCard at index ${fileIndex}.`,
      );
    }
    const card = cards[0];
    if (card === undefined) {
      throw new Error("Indexed FileCard disappeared during navigation.");
    }

    /**
     * Resolves the first target from the FileCard's current DOM representation.
     *
     * Bays each number their hunks from zero, so index zero alone names no
     * single target; the file's first hunk is its first target in DOM order,
     * which renderers keep equal to document order. A missing target is a
     * renderer-contract failure rather than an alternate destination.
     */
    function firstTarget(): HTMLElement {
      const target = card.querySelector<HTMLElement>("[data-hunk-target]");
      if (target === null) {
        throw new Error("FileCard first hunk target disappeared.");
      }
      if (target.dataset.fileIndex !== String(fileIndex)) {
        throw new Error("FileCard target has the wrong manifest index.");
      }
      const kind = target.dataset.hunkKind;
      if (
        kind !== "real" &&
        kind !== "skip" &&
        kind !== "husk" &&
        kind !== "lazy" &&
        kind !== "zero"
      ) {
        throw new Error("FileCard hunk-zero target has an invalid kind.");
      }
      return target;
    }

    const enrichedBays = new Set<HTMLElement>();

    /**
     * Calculates the document viewport produced by centered target scrolling.
     *
     * The result uses the target's normal-flow offset rather than its visible
     * rectangle, because sticky FileCard headers can move independently of their
     * document location. It is recalculated after every rich layout replacement
     * and leaves the page stationary until the final scroll.
     */
    function centeredViewportTop(currentTarget: HTMLElement): number {
      const viewportHeight = window.innerHeight;
      if (viewportHeight <= 0) {
        throw new Error("File navigation requires a positive viewport height.");
      }
      let targetDocumentTop = 0;
      let offsetElement: HTMLElement | null = currentTarget;
      while (offsetElement !== null) {
        targetDocumentTop += offsetElement.offsetTop;
        offsetElement = offsetElement.offsetParent as HTMLElement | null;
      }
      const targetHeight = currentTarget.offsetHeight;
      if (
        !Number.isFinite(targetDocumentTop) ||
        !Number.isFinite(targetHeight)
      ) {
        throw new Error("File navigation requires finite target geometry.");
      }
      const desiredTop =
        targetDocumentTop + targetHeight / 2 - viewportHeight / 2;
      const scrollingElement = document.scrollingElement;
      if (scrollingElement === null) {
        throw new Error("File navigation requires a document scroll element.");
      }
      const maximumTop = Math.max(
        0,
        scrollingElement.scrollHeight - viewportHeight,
      );
      return Math.min(maximumTop, Math.max(0, desiredTop));
    }

    let target = firstTarget();
    if (target.dataset.hunkKind === "husk") {
      throw new Error("File navigation cannot target a HuskFile.");
    }
    // Only a target inside a bay wrapper can be virtual; chrome anchors and
    // collapsed-file skips live outside every wrapper with exact geometry.
    const destinationBay = target.closest<HTMLElement>("[data-bay-render]");
    if (
      destinationBay !== null &&
      destinationBay.dataset.bayRender === "virtual" &&
      target.dataset.hunkKind === "real"
    ) {
      enrichedBays.add(destinationBay);
      await waitToEnrich(destinationBay);
      if (!alive) {
        return;
      }
      target = firstTarget();
      // The first target of any bay is that bay's hunk zero, so the
      // enriched bay must still lead with a real index-zero target.
      if (
        target.dataset.hunkKind !== "real" ||
        target.dataset.hunkIndex !== "0"
      ) {
        throw new Error("Enriched bay omitted its first real hunk.");
      }
    }
    if (!alive) {
      return;
    }

    while (alive) {
      target = firstTarget();
      const viewportTop = centeredViewportTop(target);
      // Test the viewport that the final scroll will occupy without moving the
      // page. Each enrichment changes geometry, so the next pass recalculates it.
      // `waitToEnrich()` spans a rendered frame when it warms fresh chunks, so
      // observer-driven render-mode flips may interleave; that is safe because
      // enrichment completes with real geometry and every mode transition
      // measures real heights, so each pass recalculates against settled
      // layout and the loop's one-enrichment-per-card set still terminates it.
      let intersectingVirtualBay: HTMLElement | undefined;
      for (const candidate of Array.from(
        root.querySelectorAll<HTMLElement>('[data-bay-render="virtual"]'),
      )) {
        if (enrichedBays.has(candidate)) {
          continue;
        }
        const enrichableBay = candidate as Partial<EnrichableBay>;
        if (typeof enrichableBay.intersectsRichEntryZone !== "function") {
          throw new Error("Virtual bay omitted rich-entry geometry.");
        }
        if (enrichableBay.intersectsRichEntryZone(viewportTop)) {
          intersectingVirtualBay = candidate;
          break;
        }
      }
      if (intersectingVirtualBay === undefined) {
        break;
      }
      enrichedBays.add(intersectingVirtualBay);
      await waitToEnrich(intersectingVirtualBay);
      if (!alive) {
        return;
      }
    }

    target = firstTarget();
    await settleCenteredScroll(target);
    if (!alive) {
      return;
    }
    card.classList.remove("file-card-flash");
    void card.offsetWidth;
    card.classList.add("file-card-flash");
  }

  /**
   * Scrolls to one exact rendered backend line without changing hunk selection.
   *
   * The file sequence has already expanded, loaded, and admitted the target
   * FullFile. Navigation repeatedly resolves the prepared row from required
   * coordinates, enriches virtual bays intersecting the hypothetical centered
   * viewport one at a time, and centers the destination, re-running the
   * centering until nearby chunk rendering stops moving it.
   */
  async function navigateToLine(
    fileIndex: number,
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<NavigationResult> {
    if (!Number.isInteger(fileIndex) || fileIndex < 0) {
      throw new Error("Line navigation requires a valid manifest index.");
    }
    if (!/^[1-9]\d*$/u.test(target.line)) {
      throw new Error(
        "Line navigation requires a positive decimal backend line identity.",
      );
    }
    if (abortSignal.aborted) {
      return { state: "stopped" };
    }
    const root = props.root();
    const cards = root.querySelectorAll<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    if (cards.length !== 1) {
      throw new Error(
        `Line navigation requires one FileCard at index ${fileIndex}.`,
      );
    }
    const card = expect(
      cards.item(0),
      "Line navigation FileCard disappeared.",
    ) as Partial<PreparableFileCard>;
    if (typeof card.prepareLine_impl !== "function") {
      throw new Error("Line navigation requires FullFile preparation.");
    }
    const enrichedBays = new Set<HTMLElement>();

    /**
     * Calculates the document viewport produced by centering the current row.
     */
    function centeredViewportTop(target: HTMLElement): number {
      const viewportHeight = window.innerHeight;
      if (viewportHeight <= 0) {
        throw new Error("Line navigation requires a positive viewport height.");
      }
      let targetDocumentTop = 0;
      let offsetElement: HTMLElement | null = target;
      while (offsetElement !== null) {
        targetDocumentTop += offsetElement.offsetTop;
        offsetElement = offsetElement.offsetParent as HTMLElement | null;
      }
      const targetHeight = target.offsetHeight;
      if (
        !Number.isFinite(targetDocumentTop) ||
        !Number.isFinite(targetHeight) ||
        targetHeight <= 0
      ) {
        throw new Error(
          "Line navigation requires finite, positive target geometry.",
        );
      }
      const scrollingElement = document.scrollingElement;
      if (scrollingElement === null) {
        throw new Error("Line navigation requires a document scroll element.");
      }
      const desiredTop =
        targetDocumentTop + targetHeight / 2 - viewportHeight / 2;
      const maximumTop = Math.max(
        0,
        scrollingElement.scrollHeight - viewportHeight,
      );
      return Math.min(maximumTop, Math.max(0, desiredTop));
    }

    while (alive && !abortSignal.aborted) {
      const prepared = await card.prepareLine_impl(target, abortSignal);
      if (prepared.state !== "ready") {
        return prepared.state === "missing"
          ? { state: "missing" }
          : { state: "stopped" };
      }
      const viewportTop = centeredViewportTop(prepared.row);
      let intersectingVirtualBay: HTMLElement | undefined;
      for (const candidate of Array.from(
        root.querySelectorAll<HTMLElement>('[data-bay-render="virtual"]'),
      )) {
        if (enrichedBays.has(candidate)) {
          continue;
        }
        const enrichableBay = candidate as Partial<EnrichableBay>;
        if (typeof enrichableBay.intersectsRichEntryZone !== "function") {
          throw new Error("Virtual bay omitted rich-entry geometry.");
        }
        if (enrichableBay.intersectsRichEntryZone(viewportTop)) {
          intersectingVirtualBay = candidate;
          break;
        }
      }
      if (intersectingVirtualBay === undefined) {
        if (abortSignal.aborted || !alive) {
          return { state: "stopped" };
        }
        scrollGuard.stop();
        await settleCenteredScroll(prepared.row);
        return { state: "complete" };
      }
      enrichedBays.add(intersectingVirtualBay);
      await waitToEnrich(intersectingVirtualBay);
    }
    return { state: "stopped" };
  }

  /**
   * Resolves one relative explicit-navigation destination from current DOM truth.
   *
   * An off-screen selected location is centered without changing selection.
   * Otherwise the operation advances through current participating DOM order,
   * wraps, and enriches a virtual destination when required. The caller alone
   * selects and scrolls the returned target.
   */
  async function relativeDestination(
    direction: "next" | "previous",
  ): Promise<HTMLElement | null> {
    const root = props.root();
    if (root.querySelector("[data-file-card]") === null) {
      return null;
    }
    const location = selectedLocation(root);
    const rect = location.target.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= window.innerHeight) {
      // Only a target inside a bay wrapper can have estimated geometry; a
      // pseudo-hunk, chrome anchor, or collapsed-file skip lives outside
      // every wrapper and centers exactly as it stands.
      const selectedBay =
        location.target.closest<HTMLElement>("[data-bay-render]");
      if (selectedBay !== null) {
        await waitToEnrich(selectedBay);
      }
      if (alive) {
        const enrichedLocation = selectedLocation(root);
        enrichedLocation.target.scrollIntoView({
          block: "center",
          behavior: "instant",
        });
      }
      return null;
    }

    const participants = Array.from(
      root.querySelectorAll<HTMLElement>(PARTICIPATING_HUNK_SELECTOR),
    );
    if (participants.length === 0) {
      return null;
    }

    let destination: HTMLElement | undefined;
    if (!location.target.classList.contains("skip")) {
      const currentIndex = participants.indexOf(location.target);
      if (currentIndex === -1) {
        throw new Error(
          "Selected participating hunk is absent from DOM order.",
        );
      }
      const destinationIndex =
        direction === "next" ? currentIndex + 1 : currentIndex - 1;
      destination =
        participants[
          (destinationIndex + participants.length) % participants.length
        ];
    } else {
      const allTargets = Array.from(
        root.querySelectorAll<HTMLElement>("[data-hunk-target]"),
      );
      const currentIndex = allTargets.indexOf(location.target);
      if (currentIndex === -1) {
        throw new Error("Skipped selected hunk is absent from DOM order.");
      }
      for (let offset = 1; offset <= allTargets.length; offset += 1) {
        const candidateIndex =
          direction === "next" ? currentIndex + offset : currentIndex - offset;
        const candidate =
          allTargets[(candidateIndex + allTargets.length) % allTargets.length];
        if (
          candidate !== undefined &&
          candidate.matches(PARTICIPATING_HUNK_SELECTOR)
        ) {
          destination = candidate;
          break;
        }
      }
    }
    if (destination === undefined) {
      throw new Error(
        "Navigation could not resolve a participating destination.",
      );
    }
    return await enrichTarget(root, destination);
  }

  /**
   * Selects and scrolls to the next participating hunk in current DOM order.
   *
   * This is one of exactly four operations permitted to call `selectHunk`.
   */
  async function nextHunk(): Promise<void> {
    const root = props.root();
    const target = await relativeDestination("next");
    if (target === null) {
      return;
    }
    selectHunk(root, target);
    target.scrollIntoView({ block: "center", behavior: "instant" });
  }

  /**
   * Selects and scrolls to the previous participating hunk in current DOM order.
   *
   * This is one of exactly four operations permitted to call `selectHunk`.
   */
  async function prevHunk(): Promise<void> {
    const root = props.root();
    const target = await relativeDestination("previous");
    if (target === null) {
      return;
    }
    selectHunk(root, target);
    target.scrollIntoView({ block: "center", behavior: "instant" });
  }

  const navigation: Navigation = {
    root: props.root,
    /**
     * Executes one explicit operation against the current ChangeSet DOM.
     *
     * Callers provide a complete `NavigationCommand`. A disposed controller is a
     * no-op; otherwise this method performs only that command's documented DOM
     * selection, enrichment, and scroll behavior and lets invariant errors reject.
     */
    async navigate(command): Promise<NavigationResult> {
      if (!alive) {
        return { state: "stopped" };
      }
      if (command.kind !== "line") {
        scrollGuard.stop();
      }
      switch (command.kind) {
        case "next-hunk":
          await nextHunk();
          return { state: "complete" };
        case "previous-hunk":
          await prevHunk();
          return { state: "complete" };
        case "file":
          await navigateToFile(command.fileIndex);
          return alive ? { state: "complete" } : { state: "stopped" };
        case "line":
          return await navigateToLine(
            command.fileIndex,
            command.target,
            command.abortSignal,
          );
        case "top":
          window.scrollTo({ top: 0, behavior: "instant" });
          return { state: "complete" };
        default: {
          const unexpectedCommand: never = command;
          throw new Error(
            `Navigation received an unsupported command: ${JSON.stringify(unexpectedCommand)}`,
          );
        }
      }
    },
  };

  onMount(() => {
    const root = props.root();
    if (root.querySelector("[data-file-render-error]") !== null) {
      // A critical renderer failure is terminal local damage. This provider
      // must not synthesize a target, repair selection, or escalate the error.
      return;
    }
    // The mounted snapshot has already written its own initial selection
    // (`writeInitialHunkSelection`); a shell without navigable DOM — the
    // loading and error fallbacks — installs no scroll listeners.
    if (root.querySelectorAll("[data-file-card]").length === 0) {
      return;
    }

    const scrollingElement = expect(
      document.scrollingElement,
      "Scroll following requires a document scroll element.",
    );
    const abortController = new AbortController();
    const passiveListener = {
      passive: true,
      signal: abortController.signal,
    };

    /**
     * Reports whether movement in one direction can change document scrollTop.
     *
     * It is nested because wheel, touch, and keyboard handlers share this DOM
     * policy inside the same mounted listener lifecycle.
     */
    function documentCanScroll(direction: "up" | "down"): boolean {
      const maximumScrollTop = Math.max(
        0,
        scrollingElement.scrollHeight - scrollingElement.clientHeight,
      );

      return direction === "up"
        ? scrollingElement.scrollTop > 0
        : scrollingElement.scrollTop < maximumScrollTop;
    }

    /**
     * Stops scroll-follow and reports an unexpected native-listener failure.
     *
     * It is nested because scroll and touch listeners share this failure path;
     * native callbacks do not pass thrown errors through Solid's ErrorBoundary.
     */
    function reportScrollFollowError(error: unknown): void {
      scrollGuard.stop();
      toast.showError("Scroll following failed", error);
    }

    // At most one selection calculation per animation frame. The callback
    // re-checks the guard, so any stop between scheduling and the frame
    // (scrollend, programmatic navigation, modal blocking, guard errors)
    // cancels the pending calculation instead of reselecting afterward.
    let pendingScrollFollow: number | null = null;

    document.addEventListener(
      "scroll",
      (event) => {
        // A nested scroller consumed the recognized input. It must not leave
        // permission behind unless document scrolling already started.
        if (event.target !== document) {
          scrollGuard.scrolled("nested");
          return;
        }

        if (!scrollGuard.ok()) {
          return;
        }

        scrollGuard.scrolled("document");

        if (pendingScrollFollow !== null) {
          return;
        }
        pendingScrollFollow = requestAnimationFrame(() => {
          pendingScrollFollow = null;
          if (!scrollGuard.ok() || !root.isConnected) {
            return;
          }
          try {
            scrollFollow(root);
          } catch (error: unknown) {
            reportScrollFollowError(error);
          }
        });
      },
      { ...passiveListener, capture: true },
    );
    document.addEventListener(
      "scrollend",
      () => {
        scrollGuard.stop();
      },
      passiveListener,
    );
    document.addEventListener(
      "wheel",
      (event) => {
        if (event.deltaY === 0 || event.ctrlKey) {
          return;
        }

        const direction = event.deltaY < 0 ? "up" : "down";

        if (!documentCanScroll(direction)) {
          scrollGuard.stop();
          return;
        }

        scrollGuard.input("wheel");
      },
      passiveListener,
    );
    document.addEventListener(
      "touchstart",
      (event) => {
        try {
          touchController.set(event);
        } catch (error: unknown) {
          reportScrollFollowError(error);
        }
      },
      passiveListener,
    );
    document.addEventListener(
      "touchmove",
      (event) => {
        try {
          const direction = touchController.comparedDirection(event);

          if (direction === null) {
            return;
          }

          if (!documentCanScroll(direction)) {
            scrollGuard.stop();
            return;
          }

          scrollGuard.input("touch");
        } catch (error: unknown) {
          reportScrollFollowError(error);
        }
      },
      passiveListener,
    );
    document.addEventListener(
      "keydown",
      (event) => {
        if (
          event.defaultPrevented ||
          event.metaKey ||
          event.ctrlKey ||
          event.altKey ||
          ![
            "ArrowUp",
            "ArrowDown",
            "PageUp",
            "PageDown",
            "Home",
            "End",
            " ",
          ].includes(event.key)
        ) {
          return;
        }
        const target = event.target;
        // Editable controls and Space-activated controls retain their native
        // behavior and must not arm document scroll following.
        if (
          target instanceof HTMLElement &&
          (target.isContentEditable ||
            target instanceof HTMLInputElement ||
            target instanceof HTMLTextAreaElement ||
            target instanceof HTMLSelectElement ||
            (event.key === " " && target.closest("button, a[href]") !== null))
        ) {
          return;
        }
        const direction =
          event.key === "ArrowUp" ||
          event.key === "PageUp" ||
          event.key === "Home" ||
          (event.key === " " && event.shiftKey)
            ? "up"
            : "down";

        if (!documentCanScroll(direction)) {
          scrollGuard.stop();
          return;
        }

        scrollGuard.input("keyboard");
      },
      { signal: abortController.signal },
    );

    onCleanup(() => {
      abortController.abort();
      scrollGuard.stop();
    });
  });
  onCleanup(() => {
    alive = false;
  });

  return (
    <NavigationContext.Provider value={navigation}>
      {props.children}
    </NavigationContext.Provider>
  );
}
