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

export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  bay: string;
  hunkIndex: number;
};

export type FileStateHunkIdentity = {
  fileIndex: number;
  kind: "husk" | "lazy" | "zero";
  hunkIndex: 0;
};

export type SkippedHunkIdentity = {
  fileIndex: number;
  kind: "skip";
  bay: string;
  hunkIndex: number;
};

export type PseudoHunkIdentity = FileStateHunkIdentity | SkippedHunkIdentity;

export type HunkIdentity = RealHunkIdentity | PseudoHunkIdentity;

export type NavigationCommand =
  | {
      kind: "next-hunk";
    }
  | {
      kind: "previous-hunk";
    }
  | {
      kind: "file";
      fileIndex: number;
    }
  | {
      kind: "file-start";
      fileIndex: number;
    }
  | {
      kind: "line";
      fileIndex: number;
      target: LinePinTarget;
      abortSignal: AbortSignal;
    }
  | {
      kind: "top";
    };

export type NavigationResult =
  | {
      state: "complete";
    }
  | {
      state: "missing";
    }
  | {
      state: "stopped";
    };

export type Navigation = {
  navigate(command: NavigationCommand): Promise<NavigationResult>;
  root: Accessor<HTMLElement>;
  readonly activeOperations: number;
};

export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};

type PreparableFileCard = HTMLElement & {
  prepareLine_impl: (
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ) => Promise<PreparedLine>;
};

function isPreparableFileCard(card: HTMLElement): card is PreparableFileCard {
  return typeof Reflect.get(card, "prepareLine_impl") === "function";
}

type EnrichableBay = HTMLElement & {
  intersectsRichEntryZone: (viewportTop: number) => boolean;
  waitToEnrich_impl: () => Promise<void>;
};

function isEnrichableBay(bay: HTMLElement): bay is EnrichableBay {
  return (
    typeof Reflect.get(bay, "intersectsRichEntryZone") === "function" &&
    typeof Reflect.get(bay, "waitToEnrich_impl") === "function"
  );
}

const NavigationContext = createContext<Navigation>();

export function useNavigation(): Navigation {
  const navigation = useContext(NavigationContext);
  return expect(navigation, "Hunk navigation requires NavigationProvider.");
}

function selectHunk(root: HTMLElement, target: HTMLElement): void {
  assert(
    target.matches("[data-hunk-target]"),
    "Selected element is not a hunk target.",
  );
  assert(
    root.contains(target),
    "Selected hunk target belongs to another ChangeSet.",
  );
  const fileCard = expect(
    target.closest<HTMLElement>("[data-file-card]"),
    "Selected hunk target has no owning FileCard.",
  );
  assert(
    root.contains(fileCard),
    "Selected hunk target has no owning FileCard.",
  );
  const kind = target.dataset.hunkKind;
  const fileIndex = target.dataset.fileIndex;
  assert(
    kind === "real" ||
      kind === "husk" ||
      kind === "lazy" ||
      kind === "zero" ||
      kind === "skip",
    "Hunk target has an invalid kind.",
  );
  assert(
    fileIndex !== undefined && fileCard.dataset.fileIndex === fileIndex,
    "Hunk target and FileCard indices do not match.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(fileIndex),
    "Hunk target has an invalid file index.",
  );
  const hunkIndex = expect(
    target.dataset.hunkIndex,
    "Hunk target is missing its hunk index.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(hunkIndex),
    "Hunk target has an invalid hunk index.",
  );
  const bay = target.dataset.hunkBay;
  if (kind === "real" || kind === "skip") {
    assert(
      bay !== undefined && bay.length > 0,
      `${kind} hunk target requires a bay key.`,
    );
  } else {
    assert(bay === undefined, `${kind} pseudo-hunk must not carry a bay key.`);
    assert(hunkIndex === "0", `${kind} pseudo-hunk requires hunk index zero.`);
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
    assert(
      card.querySelector("[data-hunk-target]") !== null,
      "Every FileCard requires a hunk target.",
    );
  }
  const firstCard = expect(
    cards[0],
    "Non-empty FileCard collection has no first card.",
  );
  const firstFileIndex = expect(
    firstCard.dataset.fileIndex,
    "First FileCard has no manifest file index.",
  );
  // Bays each number their hunks from zero, so an index alone names no
  // single target; the snapshot's first hunk is the first target in DOM
  // order, which renderers keep equal to document order.
  const firstTarget = expect(
    firstCard.querySelector<HTMLElement>("[data-hunk-target]"),
    "First hunk target disappeared during initialization.",
  );
  assert(
    firstTarget.dataset.fileIndex === firstFileIndex,
    "First hunk target has the wrong manifest index.",
  );
  assert(
    root.querySelector(
      "[data-hunk-target][data-selected], [data-file-card][data-selected-hunk-index]",
    ) === null,
    "Initial hunk selection requires unselected DOM.",
  );
  selectHunk(root, firstTarget);
}

export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element {
  const toast = useToasts();
  let alive = true;
  // This Provider owns the count across each command's preparation and scroll.
  // Each accepted call releases its increment in finally, even after disposal;
  // overlapping calls keep virtualization paused until the last one settles.
  let activeOperations = 0;

  const scrollGuard = (() => {
    let state: "idle" | "input" | "document" = "idle";
    let pendingExpiryHandle: number | null = null;

    function cancelPendingExpiry(): void {
      if (pendingExpiryHandle === null) {
        return;
      }

      cancelAnimationFrame(pendingExpiryHandle);
      pendingExpiryHandle = null;
    }

    return {
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

      scrolled(scroller: "document" | "nested"): void {
        if (scroller === "nested") {
          state = state === "input" ? "idle" : state;
          return;
        }

        cancelPendingExpiry();
        state = "document";
      },

      ok(): boolean {
        return state !== "idle";
      },

      stop(): void {
        state = "idle";
      },
    };
  })();

  const touchController = (() => {
    let previousTouchY: number | null = null;

    function primaryTouchY(event: TouchEvent): number {
      const touch = expect(
        event.touches.item(0),
        "Touch input requires a primary touch.",
      );
      return touch.clientY;
    }

    return {
      set(event: TouchEvent): void {
        previousTouchY = primaryTouchY(event);
      },

      comparedDirection(event: TouchEvent): "up" | "down" | null {
        const previousY = expect(
          previousTouchY,
          "Touch movement requires a preceding touch position.",
        );

        const currentTouchY = primaryTouchY(event);
        previousTouchY = currentTouchY;

        if (currentTouchY === previousY) {
          return null;
        }

        return currentTouchY < previousY ? "down" : "up";
      },
    };
  })();

  function scrollFollow(root: HTMLElement): void {
    const fileList = expect(
      root.querySelector<HTMLElement>(".file-list"),
      "Scroll following requires the ChangeSet file list.",
    );
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
    // `auto`, never `hidden`. See the .diff-row-chunk rule in styles.css.
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

  async function waitToEnrich(bay: HTMLElement): Promise<void> {
    assert(isEnrichableBay(bay), "Bay omitted its enrichment operations.");
    await bay.waitToEnrich_impl();
  }

  async function enrichTarget(
    root: HTMLElement,
    initialTarget: HTMLElement,
  ): Promise<HTMLElement | null> {
    assert(
      initialTarget.matches(PARTICIPATING_HUNK_SELECTOR),
      "Navigation destination does not participate.",
    );
    const card = expect(
      initialTarget.closest<HTMLElement>("[data-file-card]"),
      "Navigation destination has no owning FileCard.",
    );
    assert(
      root.contains(card),
      "Navigation destination has no owning FileCard.",
    );
    const fileIndex = initialTarget.dataset.fileIndex;
    const hunkIndex = initialTarget.dataset.hunkIndex;
    const hunkBay = initialTarget.dataset.hunkBay;
    assert(
      fileIndex !== undefined &&
        hunkIndex !== undefined &&
        /^(?:0|[1-9]\d*)$/.test(fileIndex) &&
        /^(?:0|[1-9]\d*)$/.test(hunkIndex) &&
        card.dataset.fileIndex === fileIndex,
      "Navigation destination has invalid hunk coordinates.",
    );
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
      const bayKey = expect(
        hunkBay,
        "Real navigation destination is missing its bay.",
      );
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
          candidate.dataset.hunkBay === bayKey &&
          candidate.dataset.hunkIndex === hunkIndex,
      );
      assert(
        replacements.length === 1,
        "Rich FullFile did not mount one matching hunk target.",
      );
      const replacement = expect(
        replacements[0],
        "Rich hunk target disappeared during navigation.",
      );
      target = replacement;
    }
    if (!alive) {
      return null;
    }
    return target;
  }

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

  async function navigateToFile(fileIndex: number): Promise<void> {
    assert(
      Number.isInteger(fileIndex) && fileIndex >= 0,
      "File navigation requires a valid manifest index.",
    );
    const root = props.root();
    const cards = root.querySelectorAll<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(
      cards.length === 1,
      `File navigation requires one FileCard at index ${fileIndex}.`,
    );
    const card = expect(
      cards[0],
      "Indexed FileCard disappeared during navigation.",
    );

    function firstTarget(): HTMLElement {
      const target = expect(
        card.querySelector<HTMLElement>("[data-hunk-target]"),
        "FileCard first hunk target disappeared.",
      );
      assert(
        target.dataset.fileIndex === String(fileIndex),
        "FileCard target has the wrong manifest index.",
      );
      const kind = target.dataset.hunkKind;
      assert(
        kind === "real" ||
          kind === "skip" ||
          kind === "husk" ||
          kind === "lazy" ||
          kind === "zero",
        "FileCard hunk-zero target has an invalid kind.",
      );
      return target;
    }

    const enrichedBays = new Set<HTMLElement>();

    function centeredViewportTop(currentTarget: HTMLElement): number {
      const viewportHeight = window.innerHeight;
      assert(
        viewportHeight > 0,
        "File navigation requires a positive viewport height.",
      );
      let targetDocumentTop = 0;
      let offsetElement: HTMLElement | null = currentTarget;
      while (offsetElement !== null) {
        targetDocumentTop += offsetElement.offsetTop;
        const parent: Element | null = offsetElement.offsetParent;
        assert(
          parent === null || parent instanceof HTMLElement,
          "Navigation target has a non-HTML offset parent.",
        );
        offsetElement = parent;
      }
      const targetHeight = currentTarget.offsetHeight;
      assert(
        Number.isFinite(targetDocumentTop) && Number.isFinite(targetHeight),
        "File navigation requires finite target geometry.",
      );
      const desiredTop =
        targetDocumentTop + targetHeight / 2 - viewportHeight / 2;
      const scrollingElement = expect(
        document.scrollingElement,
        "File navigation requires a document scroll element.",
      );
      const maximumTop = Math.max(
        0,
        scrollingElement.scrollHeight - viewportHeight,
      );
      return Math.min(maximumTop, Math.max(0, desiredTop));
    }

    let target = firstTarget();
    assert(
      target.dataset.hunkKind !== "husk",
      "File navigation cannot target a HuskFile.",
    );
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
      assert(
        target.dataset.hunkKind === "real" && target.dataset.hunkIndex === "0",
        "Enriched bay omitted its first real hunk.",
      );
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
        assert(
          isEnrichableBay(candidate),
          "Virtual bay omitted its enrichment operations.",
        );
        if (candidate.intersectsRichEntryZone(viewportTop)) {
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
    selectHunk(root, target);
    await settleCenteredScroll(target);
    if (!alive) {
      return;
    }
    card.classList.remove("file-card-flash");
    void card.offsetWidth;
    card.classList.add("file-card-flash");
  }

  function navigateToFileStart(fileIndex: number): void {
    assert(
      Number.isInteger(fileIndex) && fileIndex >= 0,
      "File-start navigation requires a valid manifest index.",
    );
    const root = props.root();
    const cards = root.querySelectorAll<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(
      cards.length === 1,
      `File-start navigation requires one FileCard at index ${fileIndex}.`,
    );
    const card = expect(
      cards[0],
      "Indexed FileCard disappeared during File-start navigation.",
    );
    card.scrollIntoView({ block: "start", behavior: "instant" });
  }

  async function navigateToLine(
    fileIndex: number,
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<NavigationResult> {
    assert(
      Number.isInteger(fileIndex) && fileIndex >= 0,
      "Line navigation requires a valid manifest index.",
    );
    assert(
      /^[1-9]\d*$/u.test(target.line),
      "Line navigation requires a positive decimal backend line identity.",
    );
    if (abortSignal.aborted) {
      return { state: "stopped" };
    }
    const root = props.root();
    const cards = root.querySelectorAll<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(
      cards.length === 1,
      `Line navigation requires one FileCard at index ${fileIndex}.`,
    );
    const card = expect(cards.item(0), "Line navigation FileCard disappeared.");
    assert(
      isPreparableFileCard(card),
      "Line navigation requires FullFile preparation.",
    );
    const enrichedBays = new Set<HTMLElement>();

    function centeredViewportTop(target: HTMLElement): number {
      const viewportHeight = window.innerHeight;
      assert(
        viewportHeight > 0,
        "Line navigation requires a positive viewport height.",
      );
      let targetDocumentTop = 0;
      let offsetElement: HTMLElement | null = target;
      while (offsetElement !== null) {
        targetDocumentTop += offsetElement.offsetTop;
        const parent: Element | null = offsetElement.offsetParent;
        assert(
          parent === null || parent instanceof HTMLElement,
          "Line navigation target has a non-HTML offset parent.",
        );
        offsetElement = parent;
      }
      const targetHeight = target.offsetHeight;
      assert(
        Number.isFinite(targetDocumentTop) &&
          Number.isFinite(targetHeight) &&
          targetHeight > 0,
        "Line navigation requires finite, positive target geometry.",
      );
      const scrollingElement = expect(
        document.scrollingElement,
        "Line navigation requires a document scroll element.",
      );
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
        assert(
          isEnrichableBay(candidate),
          "Virtual bay omitted its enrichment operations.",
        );
        if (candidate.intersectsRichEntryZone(viewportTop)) {
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
        await settleCenteredScroll(enrichedLocation.target);
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
      assert(
        currentIndex !== -1,
        "Selected participating hunk is absent from DOM order.",
      );
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
      assert(
        currentIndex !== -1,
        "Skipped selected hunk is absent from DOM order.",
      );
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
    return await enrichTarget(
      root,
      expect(
        destination,
        "Navigation could not resolve a participating destination.",
      ),
    );
  }

  async function nextHunk(): Promise<void> {
    const root = props.root();
    const target = await relativeDestination("next");
    if (target === null) {
      return;
    }
    selectHunk(root, target);
    await settleCenteredScroll(target);
  }

  async function prevHunk(): Promise<void> {
    const root = props.root();
    const target = await relativeDestination("previous");
    if (target === null) {
      return;
    }
    selectHunk(root, target);
    await settleCenteredScroll(target);
  }

  const navigation: Navigation = {
    root: props.root,
    get activeOperations(): number {
      return activeOperations;
    },
    async navigate(command): Promise<NavigationResult> {
      if (!alive) {
        return { state: "stopped" };
      }
      activeOperations += 1;
      try {
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
          case "file-start":
            navigateToFileStart(command.fileIndex);
            return { state: "complete" };
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
      } finally {
        activeOperations -= 1;
        if (activeOperations === 0 && alive) {
          // Exit events skipped during navigation will not necessarily fire
          // again. Tell mounted bays to observe current geometry once idle.
          props.root().dispatchEvent(new Event("navigation-idle"));
        }
      }
    },
  };

  // Scroll-follow needs native document listeners because the scroll source can
  // be wheel, touch, keyboard, or a nested FileTree outside this component's
  // JSX event path. They read the mounted root plus transient guard/controller
  // state for this Provider lifetime. One AbortController removes every listener
  // on cleanup, while the cleanup also cancels permission for a queued frame;
  // that frame rechecks both the guard and root connection before selecting.
  onMount(() => {
    const root = props.root();
    if (root.querySelector("[data-file-render-error]") !== null) {
      // A critical renderer failure is terminal local damage. This provider
      // must not synthesize a target, repair selection, or escalate the error.
      return;
    }
    // The mounted snapshot has already written its initial selection with
    // `writeInitialHunkSelection`. Loading and error shells have no navigable
    // DOM, so they install no scroll listeners.
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

    function documentCanScroll(direction: "up" | "down"): boolean {
      const maximumScrollTop = Math.max(
        0,
        scrollingElement.scrollHeight - scrollingElement.clientHeight,
      );

      return direction === "up"
        ? scrollingElement.scrollTop > 0
        : scrollingElement.scrollTop < maximumScrollTop;
    }

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
