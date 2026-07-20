/**
 * Provides explicit DOM-backed hunk navigation for one mounted ChangeSet.
 *
 * The module exports hunk identity contracts, the closed navigation operation
 * union, one ChangeSet-scoped Provider, and its checked context accessor.
 * Renderers write identity fields directly into their own DOM; this module
 * reads those attributes only while handling an explicit operation. It must not
 * retain selected identity, build a hunk registry, calculate counters, follow
 * user scrolling, change FileTree expansion, or implement line-pin behavior.
 */
import {
  createContext,
  onCleanup,
  onMount,
  useContext,
  type Accessor,
  type JSX,
} from "solid-js";

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
 * Identifies one file-level target or one coordinate-preserving skipped hunk.
 *
 * Husk, Lazy, and zero targets represent complete file states. Skip targets
 * preserve a real hunk's coordinates after its file collapses while remaining outside the
 * traversal set. Renderers write these fields directly into DOM attributes.
 */
export type PseudoHunkIdentity =
  | {
      fileIndex: number;
      kind: "husk" | "lazy" | "zero";
    }
  | {
      fileIndex: number;
      kind: "skip";
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
 * Relative operations use current selected DOM identity, direct hunk
 * navigation accepts an already-resolved participating target, file navigation
 * scrolls to one manifest file's first current DOM target without selecting,
 * and Top scrolls the page.
 */
export type NavigationCommand =
  | { kind: "next-hunk" }
  | { kind: "previous-hunk" }
  | { kind: "hunk"; target: HTMLElement }
  | { kind: "file"; fileIndex: number }
  | { kind: "top" };

/**
 * Exposes the complete explicit navigation API for one mounted ChangeSet.
 *
 * Calls resolve after any required rich materialization, selection when the
 * operation requires it, and scroll. A disposed instance performs no later DOM
 * write or scroll.
 */
export type Navigation = {
  navigate(command: NavigationCommand): Promise<void>;
};

/**
 * Defines the required DOM root and descendants served by one Provider.
 *
 * The accessor must return this mounted ChangeSet's root. Navigation never
 * falls back to document and children must remain inside that root.
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
  waitToEnrich: () => Promise<void>;
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
 * state. Cleanup prevents pending enrichment continuations from changing the
 * page.
 */
export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element {
  let alive = true;

  /**
   * Selects one concrete hunk target by mutating only authoritative DOM.
   *
   * The target may carry `.skip` only for explicit initialization; ordinary
   * navigation resolves participating destinations before calling this
   * operation. Existing FileCard identity and visible decoration are removed
   * before the target fields are copied onto its stable owning FileCard.
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
    const numericFileIndex = Number(fileIndex);
    if (!Number.isInteger(numericFileIndex) || numericFileIndex < 0) {
      throw new Error("Hunk target has an invalid file index.");
    }
    const hunkIndex = target.dataset.hunkIndex;
    if ((kind === "real" || kind === "skip") && hunkIndex === undefined) {
      throw new Error("Coordinate hunk target is missing its hunk index.");
    }
    if (kind !== "real" && kind !== "skip" && hunkIndex !== undefined) {
      throw new Error("File-level pseudo-hunk unexpectedly has a hunk index.");
    }
    if (hunkIndex !== undefined) {
      const numericHunkIndex = Number(hunkIndex);
      if (!Number.isInteger(numericHunkIndex) || numericHunkIndex < 0) {
        throw new Error("Hunk target has an invalid hunk index.");
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
      "[data-file-card][data-selected-hunk-kind]",
    )) {
      delete previousCard.dataset.selectedHunkKind;
      delete previousCard.dataset.selectedHunkIndex;
      previousCard.classList.remove("active-hunk");
    }

    fileCard.dataset.selectedHunkKind = kind;
    if (hunkIndex === undefined) {
      delete fileCard.dataset.selectedHunkIndex;
    } else {
      fileCard.dataset.selectedHunkIndex = hunkIndex;
    }
    fileCard.classList.add("active-hunk");
    target.setAttribute("data-selected", "");
    target.setAttribute("aria-current", "true");
    target.classList.add("active-hunk");
  }

  /**
   * Resolves the selected FileCard and its current scroll-back element.
   *
   * Participating and skipped targets are matched directly by primitive DOM
   * attributes. A stale selected Husk or Lazy identity falls back to the stable
   * FileCard header after FullFile replaces its pseudo-target.
   */
  function selectedLocation(root: HTMLElement): {
    card: HTMLElement;
    element: HTMLElement;
    target: HTMLElement | null;
  } {
    const cards = root.querySelectorAll<HTMLElement>(
      "[data-file-card][data-selected-hunk-kind]",
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
    const selectedKind = card.dataset.selectedHunkKind;
    const selectedHunkIndex = card.dataset.selectedHunkIndex;
    const fileIndex = card.dataset.fileIndex;
    if (selectedKind === undefined || fileIndex === undefined) {
      throw new Error("Selected FileCard has incomplete hunk identity.");
    }
    if (
      selectedKind !== "real" &&
      selectedKind !== "husk" &&
      selectedKind !== "lazy" &&
      selectedKind !== "zero" &&
      selectedKind !== "skip"
    ) {
      throw new Error("Selected FileCard has an invalid hunk kind.");
    }
    const coordinateIdentity =
      selectedKind === "real" || selectedKind === "skip";
    if (coordinateIdentity && selectedHunkIndex === undefined) {
      throw new Error("Selected coordinate identity has no hunk index.");
    }
    if (!coordinateIdentity && selectedHunkIndex !== undefined) {
      throw new Error("Selected file-level identity has a hunk index.");
    }

    let matchingTarget: HTMLElement | null = null;
    for (const candidate of card.querySelectorAll<HTMLElement>(
      "[data-hunk-target]",
    )) {
      const kindMatches = coordinateIdentity
        ? candidate.dataset.hunkKind === "real" ||
          candidate.dataset.hunkKind === "skip"
        : candidate.dataset.hunkKind === selectedKind;
      const indexMatches = coordinateIdentity
        ? candidate.dataset.hunkIndex === selectedHunkIndex
        : candidate.dataset.hunkIndex === undefined;
      if (kindMatches && indexMatches) {
        if (matchingTarget !== null) {
          throw new Error("Selected hunk identity has duplicate DOM targets.");
        }
        matchingTarget = candidate;
      }
    }
    if (matchingTarget !== null) {
      return { card, element: matchingTarget, target: matchingTarget };
    }
    if (selectedKind !== "husk" && selectedKind !== "lazy") {
      throw new Error("Selected hunk identity has no current DOM location.");
    }
    const header = card.querySelector<HTMLElement>(".file-card-header");
    if (header === null) {
      throw new Error("Selected FileCard has no stable header.");
    }
    return { card, element: header, target: null };
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
    const enrichableCard = card as Partial<EnrichableFileCard>;
    if (typeof enrichableCard.waitToEnrich === "function") {
      await enrichableCard.waitToEnrich();
      return;
    }
    if (card.dataset.fileState === "full") {
      throw new Error("FullFile omitted waitToEnrich.");
    }
  }

  /**
   * Activates one participating destination after optional rich materialization.
   *
   * Virtual targets are identified by their primitive attributes, the owning
   * FullFile is enriched directly, and the replacement target is resolved again
   * before selection. The operation then centers the final target instantly.
   */
  async function activateTarget(
    root: HTMLElement,
    initialTarget: HTMLElement,
  ): Promise<void> {
    if (!initialTarget.matches(PARTICIPATING_HUNK_SELECTOR)) {
      throw new Error("Navigation destination does not participate.");
    }
    const card = initialTarget.closest<HTMLElement>("[data-file-card]");
    if (card === null || !root.contains(card)) {
      throw new Error("Navigation destination has no owning FileCard.");
    }
    let target = initialTarget;
    if (
      card.dataset.fileRender === "virtual" &&
      initialTarget.dataset.hunkKind === "real"
    ) {
      const kind = initialTarget.dataset.hunkKind;
      const fileIndex = initialTarget.dataset.fileIndex;
      const hunkIndex = initialTarget.dataset.hunkIndex;
      if (fileIndex === undefined || hunkIndex === undefined) {
        throw new Error("Virtual FullFile destination requires real identity.");
      }
      await waitToEnrich(card);
      if (!alive) {
        return;
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
      return;
    }
    selectHunk(root, target);
    target.scrollIntoView({ block: "center", behavior: "instant" });
  }

  /**
   * Scrolls to one manifest file's exact first current DOM target.
   *
   * The immutable file index must resolve to one stable FileCard. Coordinate
   * representations use hunk zero; Lazy and zero representations use their sole
   * file-level target. A transient Husk target makes the operation an immediate
   * no-op because later file replacement has unstable geometry. An expanded
   * virtual FullFile is enriched and resolved again before Navigation calculates
   * its hypothetical centered viewport. Virtual FileCards intersecting their own
   * exact rich-entry zones at that position are enriched one at a time. The
   * destination and hypothetical viewport are recalculated after every layout
   * change, and one final scroll occurs after geometry settles. A local set bounds
   * the operation to one enrichment per FileCard. This operation never selects,
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
     * Resolves hunk zero or the one file-level pseudo-target from current DOM.
     *
     * FullFile coordinate targets take precedence. Every other FileCard state
     * must expose exactly one target without a hunk index. Missing or duplicate
     * targets are renderer-contract failures rather than alternate destinations.
     */
    function firstTarget(): HTMLElement {
      const coordinateTargets = card.querySelectorAll<HTMLElement>(
        '[data-hunk-target][data-hunk-index="0"]',
      );
      let target: HTMLElement;
      if (coordinateTargets.length > 0) {
        if (coordinateTargets.length !== 1) {
          throw new Error("FileCard exposed duplicate hunk-zero targets.");
        }
        const coordinateTarget = coordinateTargets[0];
        if (coordinateTarget === undefined) {
          throw new Error("FileCard hunk-zero target disappeared.");
        }
        target = coordinateTarget;
      } else {
        const fileTargets = card.querySelectorAll<HTMLElement>(
          "[data-hunk-target]:not([data-hunk-index])",
        );
        if (fileTargets.length !== 1) {
          throw new Error(
            "FileCard requires exactly one first file-level target.",
          );
        }
        const fileTarget = fileTargets[0];
        if (fileTarget === undefined) {
          throw new Error("FileCard file-level target disappeared.");
        }
        target = fileTarget;
      }
      if (target.dataset.fileIndex !== String(fileIndex)) {
        throw new Error("FileCard target has the wrong manifest index.");
      }
      const kind = target.dataset.hunkKind;
      if (target.hasAttribute("data-hunk-index")) {
        if (kind !== "real" && kind !== "skip") {
          throw new Error("FileCard coordinate target has an invalid kind.");
        }
      } else if (kind !== "husk" && kind !== "lazy" && kind !== "zero") {
        throw new Error("FileCard file-level target has an invalid kind.");
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
      return;
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
   * Executes one relative explicit navigation operation from current DOM truth.
   *
   * An off-screen selected location is centered without changing selection.
   * Otherwise the operation advances through current participating DOM order,
   * wraps, enriches a virtual destination when required, selects, and scrolls.
   */
  async function navigateRelative(direction: -1 | 1): Promise<void> {
    const root = props.root();
    if (root.querySelector("[data-file-card]") === null) {
      return;
    }
    const location = selectedLocation(root);
    const rect = location.element.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= window.innerHeight) {
      await waitToEnrich(location.card);
      if (alive) {
        const enrichedLocation = selectedLocation(root);
        enrichedLocation.element.scrollIntoView({
          block: "center",
          behavior: "instant",
        });
      }
      return;
    }

    const participants = Array.from(
      root.querySelectorAll<HTMLElement>(PARTICIPATING_HUNK_SELECTOR),
    );
    if (participants.length === 0) {
      return;
    }

    let destination: HTMLElement | undefined;
    if (
      location.target !== null &&
      !location.target.classList.contains("skip")
    ) {
      const currentIndex = participants.indexOf(location.target);
      if (currentIndex === -1) {
        throw new Error(
          "Selected participating hunk is absent from DOM order.",
        );
      }
      destination =
        participants[
          (currentIndex + direction + participants.length) % participants.length
        ];
    } else if (location.target !== null) {
      const allTargets = Array.from(
        root.querySelectorAll<HTMLElement>("[data-hunk-target]"),
      );
      const currentIndex = allTargets.indexOf(location.target);
      if (currentIndex === -1) {
        throw new Error("Skipped selected hunk is absent from DOM order.");
      }
      for (let offset = 1; offset <= allTargets.length; offset += 1) {
        const candidate =
          allTargets[
            (currentIndex + direction * offset + allTargets.length) %
              allTargets.length
          ];
        if (
          candidate !== undefined &&
          candidate.matches(PARTICIPATING_HUNK_SELECTOR)
        ) {
          destination = candidate;
          break;
        }
      }
    } else {
      const ownTargets = Array.from(
        location.card.querySelectorAll<HTMLElement>(
          PARTICIPATING_HUNK_SELECTOR,
        ),
      );
      if (ownTargets.length > 0) {
        // A replaced file-level pseudo identity occupies the resulting file's
        // first position for both relative directions.
        destination = ownTargets[0];
      } else {
        const following = participants.filter(
          (candidate) =>
            (location.card.compareDocumentPosition(candidate) &
              Node.DOCUMENT_POSITION_FOLLOWING) !==
            0,
        );
        const preceding = participants.filter(
          (candidate) =>
            (location.card.compareDocumentPosition(candidate) &
              Node.DOCUMENT_POSITION_PRECEDING) !==
            0,
        );
        destination =
          direction === 1
            ? (following[0] ?? participants[0])
            : (preceding[preceding.length - 1] ??
              participants[participants.length - 1]);
      }
    }
    if (destination === undefined) {
      throw new Error(
        "Navigation could not resolve a participating destination.",
      );
    }
    await activateTarget(root, destination);
  }

  const navigation: Navigation = {
    /**
     * Executes one explicit operation against the current ChangeSet DOM.
     *
     * Callers provide a complete `NavigationCommand`. A disposed controller is a
     * no-op; otherwise this method performs only that command's documented DOM
     * selection, enrichment, and scroll behavior and lets invariant errors reject.
     */
    async navigate(command): Promise<void> {
      if (!alive) {
        return;
      }
      switch (command.kind) {
        case "next-hunk":
          await navigateRelative(1);
          return;
        case "previous-hunk":
          await navigateRelative(-1);
          return;
        case "hunk":
          await activateTarget(props.root(), command.target);
          return;
        case "file":
          await navigateToFile(command.fileIndex);
          return;
        case "top":
          window.scrollTo({ top: 0, behavior: "instant" });
          return;
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
    const firstTarget =
      firstCard.querySelector<HTMLElement>("[data-hunk-target]");
    if (firstTarget === null) {
      throw new Error("Every FileCard requires a hunk target.");
    }
    selectHunk(root, firstTarget);
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
