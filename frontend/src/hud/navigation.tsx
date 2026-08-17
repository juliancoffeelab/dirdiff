/**
 * Provides explicit DOM-backed hunk navigation for one mounted ChangeSet.
 *
 * The module exports hunk identity contracts, the closed navigation operation
 * union, one ChangeSet-scoped Provider, and its checked context accessor.
 * Renderers write identity fields directly into their own DOM; this module
 * reads those attributes only while handling an explicit operation or recognized
 * user scrolling. Exactly `nextHunk`, `prevHunk`, and `scrollFollow` may call the
 * private `selectHunk` operation. The module must not retain selected identity,
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
import { expect } from "../utils";
import type { LinePinTarget, PreparedLine } from "./linePins";

const PARTICIPATING_HUNK_SELECTOR = "[data-hunk-target]:not(.skip)";

/**
 * Identifies one backend-produced hunk boundary inside a manifest file.
 *
 * Renderers construct this value locally and write its fields directly into
 * DOM attributes. The value itself is never stored after rendering.
 */
export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  hunkIndex: number;
};

/**
 * Identifies one file-state pseudo-hunk or coordinate-preserving skipped hunk.
 *
 * Husk, Lazy, and zero targets represent complete file states and require
 * `hunkIndex === 0`. Skip targets preserve the nonnegative index of the real
 * hunk replaced when its file collapses. Renderers write every field directly
 * into DOM attributes; `kind` never replaces either coordinate.
 */
export type PseudoHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy" | "zero" | "skip";
  hunkIndex: number;
};

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
 * navigation requires one manifest index, a null ordinary-file region or
 * non-empty notebook region, an exact side and backend line, and the caller's
 * AbortSignal lifetime; it never selects a hunk. Top scrolls the page.
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
 * or scroll.
 */
export type Navigation = {
  navigate(command: NavigationCommand): Promise<NavigationResult>;
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
 * Describes the navigation geometry and rich-materialization operations attached
 * by FullFile.
 *
 * Every mounted FullFile exposes both methods. `waitToEnrich()` is the general
 * materialization operation. Navigation calls `intersectsRichEntryZone()` only
 * for a virtualizable text FullFile currently exposing `.virtual-file-body`.
 * Neither method selects, expands, calculates counters, or scrolls.
 */
type EnrichableFileCard = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
  prepareLine_impl: (
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ) => Promise<PreparedLine>;
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
 * Provides one disposable explicit-navigation instance for one ChangeSet root.
 *
 * Selection is initialized once from the first FileCard's required first hunk
 * target. Later operations read current DOM identity and retain no selected-hunk
 * state. Recognized browser scrolling selects rich real hunks at the reading
 * line, while cleanup prevents pending browser work from changing the page.
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
   * Selects one concrete hunk target by mutating only authoritative DOM.
   *
   * Exactly `nextHunk`, `prevHunk`, and `scrollFollow` may call this operation,
   * and each calls it directly. Existing FileCard identity and visible decoration
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
    if (
      (kind === "husk" || kind === "lazy" || kind === "zero") &&
      hunkIndex !== "0"
    ) {
      throw new Error(`${kind} pseudo-hunk requires hunk index zero.`);
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
      previousCard.classList.remove("active-hunk");
    }

    fileCard.dataset.selectedHunkIndex = hunkIndex;
    fileCard.classList.add("active-hunk");
    target.setAttribute("data-selected", "");
    target.setAttribute("aria-current", "true");
    target.classList.add("active-hunk");
  }

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
   * Resolves the exact selected hunk target from its FileCard coordinates.
   *
   * Every hunk representation carries both file and hunk indices. Missing or
   * duplicate coordinates are application errors; this operation never
   * substitutes a FileCard header or another target.
   */
  function selectedLocation(root: HTMLElement): {
    card: HTMLElement;
    target: HTMLElement;
  } {
    const cards = root.querySelectorAll<HTMLElement>(
      "[data-file-card][data-selected-hunk-index]",
    );
    if (cards.length !== 1) {
      throw new Error(
        "A non-empty ChangeSet requires exactly one selected hunk.",
      );
    }
    const card = cards[0];
    if (card === undefined) {
      throw new Error("Selected FileCard disappeared during navigation.");
    }
    const selectedHunkIndex = card.dataset.selectedHunkIndex;
    const fileIndex = card.dataset.fileIndex;
    if (selectedHunkIndex === undefined || fileIndex === undefined) {
      throw new Error("Selected FileCard has incomplete hunk identity.");
    }
    if (
      !/^(?:0|[1-9]\d*)$/.test(fileIndex) ||
      !/^(?:0|[1-9]\d*)$/.test(selectedHunkIndex)
    ) {
      throw new Error("Selected FileCard has an invalid hunk coordinate.");
    }

    const matchingTargets = Array.from(
      card.querySelectorAll<HTMLElement>("[data-hunk-target]"),
    ).filter(
      (candidate) =>
        candidate.dataset.fileIndex === fileIndex &&
        candidate.dataset.hunkIndex === selectedHunkIndex,
    );
    if (matchingTargets.length !== 1) {
      throw new Error(
        `Selected hunk (${fileIndex}, ${selectedHunkIndex}) requires exactly one DOM target.`,
      );
    }
    const target = matchingTargets[0];
    if (target === undefined) {
      throw new Error("Selected hunk target disappeared during navigation.");
    }
    return { card, target };
  }

  /**
   * Waits for one selected or destination FileCard to expose required rich DOM.
   *
   * FullFile supplies the direct operation for rich, virtual, file-collapsed, zero,
   * and notebook presentations. Husk and Lazy cards intentionally have no such
   * method and are immediate no-ops. A FullFile without the operation violates
   * its DOM interface and throws instead of silently skipping enrichment.
   */
  async function waitToEnrich(card: HTMLElement): Promise<void> {
    switch (card.dataset.fileState) {
      case "husk":
      case "lazy":
        return;
      case "full":
        break;
      default:
        throw new Error("FileCard has an invalid file state.");
    }

    const enrichableCard = card as Partial<EnrichableFileCard>;
    if (typeof enrichableCard.waitToEnrich_impl !== "function") {
      throw new Error("FullFile omitted waitToEnrich_impl.");
    }
    await enrichableCard.waitToEnrich_impl();
  }

  /**
   * Resolves one participating destination after optional rich materialization.
   *
   * Virtual targets are identified by their primitive attributes, the owning
   * FullFile is enriched directly, and the replacement target is resolved again
   * before returning it. This operation never selects or scrolls.
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
    if (
      card.dataset.fileRender === "virtual" &&
      initialTarget.dataset.hunkKind === "real"
    ) {
      const kind = initialTarget.dataset.hunkKind;
      await waitToEnrich(card);
      if (!alive) {
        return null;
      }
      const replacements = Array.from(
        card.querySelectorAll<HTMLElement>(PARTICIPATING_HUNK_SELECTOR),
      ).filter(
        (candidate) =>
          candidate.dataset.hunkKind === kind &&
          candidate.dataset.fileIndex === fileIndex &&
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
   * Scrolls to one manifest file's exact first current DOM target.
   *
   * The immutable file index must resolve to one stable FileCard. Every
   * representation exposes hunk zero. A transient Husk target violates the
   * caller contract because Husk navigation controls are disabled while it and
   * adjacent Husks have unstable layout. An expanded virtual FullFile is enriched
   * and resolved again before Navigation calculates its hypothetical centered
   * viewport. Virtual FileCards intersecting their own exact rich-entry zones at
   * that position are enriched one at a time. The destination and hypothetical
   * viewport are recalculated after every layout change, and one final scroll
   * occurs after geometry settles. A local set bounds the operation to one
   * enrichment per FileCard. The operation never selects its destination,
   * expands, collapses, fetches, calculates counters, or updates the FileTree.
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
     * Resolves hunk zero from the FileCard's current DOM representation.
     *
     * Every real or pseudo hunk carries an index. Missing or duplicate hunk-zero
     * targets are renderer-contract failures rather than alternate destinations.
     */
    function firstTarget(): HTMLElement {
      const targets = card.querySelectorAll<HTMLElement>(
        '[data-hunk-target][data-hunk-index="0"]',
      );
      if (targets.length !== 1) {
        throw new Error("FileCard requires exactly one hunk-zero target.");
      }
      const target = targets.item(0);
      if (target === null) {
        throw new Error("FileCard hunk-zero target disappeared.");
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

    const enrichedFileCards = new Set<HTMLElement>();

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
    if (
      card.dataset.fileRender === "virtual" &&
      target.dataset.hunkKind === "real"
    ) {
      enrichedFileCards.add(card);
      await waitToEnrich(card);
      if (!alive) {
        return;
      }
      target = firstTarget();
      if (
        target.dataset.hunkKind !== "real" ||
        target.dataset.hunkIndex !== "0"
      ) {
        throw new Error("Enriched FullFile omitted real hunk zero.");
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
      // `waitToEnrich()` completes through Solid's mount microtask only: browser
      // input and IntersectionObserver callbacks cannot interleave before the
      // single final scroll.
      let intersectingVirtualCard: HTMLElement | undefined;
      for (const candidate of Array.from(
        root.querySelectorAll<HTMLElement>(
          '[data-file-card][data-file-render="virtual"]',
        ),
      )) {
        if (
          enrichedFileCards.has(candidate) ||
          candidate.querySelector(".virtual-file-body") === null
        ) {
          continue;
        }
        const enrichableCard = candidate as Partial<EnrichableFileCard>;
        if (typeof enrichableCard.intersectsRichEntryZone !== "function") {
          throw new Error("Virtual FullFile omitted rich-entry geometry.");
        }
        if (enrichableCard.intersectsRichEntryZone(viewportTop)) {
          intersectingVirtualCard = candidate;
          break;
        }
      }
      if (intersectingVirtualCard === undefined) {
        break;
      }
      enrichedFileCards.add(intersectingVirtualCard);
      await waitToEnrich(intersectingVirtualCard);
      if (!alive) {
        return;
      }
    }

    target = firstTarget();
    target.scrollIntoView({ block: "center", behavior: "instant" });
    card.classList.remove("file-card-flash");
    void card.offsetWidth;
    card.classList.add("file-card-flash");
  }

  /**
   * Scrolls to one exact rendered backend line without changing hunk selection.
   *
   * The file sequence has already expanded, loaded, and admitted the target
   * FullFile. Navigation enriches virtual layout, repeatedly resolves the target
   * from required coordinates, prepares intersecting virtual FileCards for the
   * hypothetical centered viewport, and performs exactly one final scroll.
   */
  async function navigateToLine(
    fileIndex: number,
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<NavigationResult> {
    if (!Number.isInteger(fileIndex) || fileIndex < 0) {
      throw new Error("Line navigation requires a valid manifest index.");
    }
    if (target.file.length === 0) {
      throw new Error("Line navigation requires a canonical file path.");
    }
    if (target.region !== null && target.region.length === 0) {
      throw new Error("Line navigation requires a non-empty notebook region.");
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
    ) as Partial<EnrichableFileCard>;
    if (typeof card.prepareLine_impl !== "function") {
      throw new Error("Line navigation requires FullFile preparation.");
    }
    const enrichedFileCards = new Set<HTMLElement>();

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
      let intersectingVirtualCard: HTMLElement | undefined;
      for (const candidate of Array.from(
        root.querySelectorAll<HTMLElement>(
          '[data-file-card][data-file-render="virtual"]',
        ),
      )) {
        if (
          enrichedFileCards.has(candidate) ||
          candidate.querySelector(".virtual-file-body") === null
        ) {
          continue;
        }
        const enrichableCard = candidate as Partial<EnrichableFileCard>;
        if (typeof enrichableCard.intersectsRichEntryZone !== "function") {
          throw new Error("Virtual FullFile omitted rich-entry geometry.");
        }
        if (enrichableCard.intersectsRichEntryZone(viewportTop)) {
          intersectingVirtualCard = candidate;
          break;
        }
      }
      if (intersectingVirtualCard === undefined) {
        if (abortSignal.aborted || !alive) {
          return { state: "stopped" };
        }
        scrollGuard.stop();
        prepared.row.scrollIntoView({
          block: "center",
          behavior: "instant",
        });
        return { state: "complete" };
      }
      enrichedFileCards.add(intersectingVirtualCard);
      await waitToEnrich(intersectingVirtualCard);
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
      await waitToEnrich(location.card);
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
   * This is one of exactly three operations permitted to call `selectHunk`.
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
   * This is one of exactly three operations permitted to call `selectHunk`.
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
    const firstTargets = Array.from(
      firstCard.querySelectorAll<HTMLElement>(
        '[data-hunk-target][data-hunk-index="0"]',
      ),
    ).filter((target) => target.dataset.fileIndex === firstFileIndex);
    if (firstTargets.length !== 1) {
      throw new Error(
        `First hunk (${firstFileIndex}, 0) requires exactly one DOM target.`,
      );
    }
    const firstTarget = firstTargets[0];
    if (firstTarget === undefined) {
      throw new Error("First hunk target disappeared during initialization.");
    }
    if (
      root.querySelector(
        "[data-hunk-target][data-selected], [data-file-card][data-selected-hunk-index]",
      ) !== null
    ) {
      throw new Error("Initial hunk selection requires unselected DOM.");
    }
    // Initial selection is part of mounting the authoritative DOM. It is not a
    // navigation action and must not create a fourth `selectHunk` caller.
    firstCard.dataset.selectedHunkIndex = "0";
    firstCard.classList.add("active-hunk");
    firstTarget.setAttribute("data-selected", "");
    firstTarget.setAttribute("aria-current", "true");
    firstTarget.classList.add("active-hunk");

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
