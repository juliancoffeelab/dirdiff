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
import type { LinePinTarget } from "./linePins";
import {
  bayContaining,
  fileCardAt,
  fileCardContaining,
  fileCardIndex,
  fileCardsIn,
  renderDamaged,
  virtualBays,
  type BayHandle,
  type LineCell,
} from "./fileCard/FileCard";

const PARTICIPATING_HUNK_SELECTOR = "[data-hunk-target]:not(.skip)";

export type RealHunkIdentity = {
  fileIndex: number;
  kind: "real";
  bay: string;
  // Bay-local, as composed; never a file-wide sequence.
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

// The hunk coordinate codec: these functions are the only code that spells
// hunk-target attributes, so writers and readers cannot drift apart.
export function hunkTargetAttrs(
  identity: HunkIdentity,
): Record<`data-${string}`, string> {
  return {
    "data-hunk-target": "",
    "data-hunk-kind": identity.kind,
    "data-file-index": String(identity.fileIndex),
    ...(identity.kind === "real" || identity.kind === "skip"
      ? { "data-hunk-bay": identity.bay }
      : {}),
    "data-hunk-index": String(identity.hunkIndex),
  };
}

export function readHunkIdentity(element: HTMLElement): HunkIdentity {
  assert(
    element.matches("[data-hunk-target]"),
    "Element is not a hunk target.",
  );
  const kind = element.dataset.hunkKind;
  const fileIndexText = expect(
    element.dataset.fileIndex,
    "Hunk target is missing its file index.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(fileIndexText),
    "Hunk target has an invalid file index.",
  );
  const hunkIndexText = expect(
    element.dataset.hunkIndex,
    "Hunk target is missing its hunk index.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(hunkIndexText),
    "Hunk target has an invalid hunk index.",
  );
  const fileIndex = Number(fileIndexText);
  const hunkIndex = Number(hunkIndexText);
  const bay = element.dataset.hunkBay;
  switch (kind) {
    case "real":
    case "skip":
      assert(
        bay !== undefined && bay.length > 0,
        `${kind} hunk target requires a bay key.`,
      );
      return { fileIndex, kind, bay, hunkIndex };
    case "husk":
    case "lazy":
    case "zero":
      assert(
        bay === undefined,
        `${kind} pseudo-hunk must not carry a bay key.`,
      );
      assert(hunkIndex === 0, `${kind} pseudo-hunk requires hunk index zero.`);
      return { fileIndex, kind, hunkIndex };
  }
  assert(false, "Hunk target has an invalid kind.");
}

// A card's hunk set: which kind of targets it currently exposes and whether
// they are skipped. A real set also carries its composed count.
export type HunkSet =
  | { kind: "husk" | "lazy" | "zero"; skipped: boolean }
  | { kind: "real"; count: number; skipped: boolean };

export function hunkSetAttrs(set: HunkSet): { "data-hunk-set": string } {
  const base = set.kind === "real" ? `real:${set.count}` : set.kind;
  return { "data-hunk-set": set.skipped ? `${base}:skip` : base };
}

export function readHunkSet(card: HTMLElement): HunkSet {
  const hunkSet = expect(
    card.dataset.hunkSet,
    "Every FileCard requires data-hunk-set.",
  );
  const pseudo = /^(husk|lazy|zero)(:skip)?$/.exec(hunkSet);
  if (pseudo !== null) {
    const kind = pseudo[1];
    switch (kind) {
      case "husk":
      case "lazy":
      case "zero":
        return { kind, skipped: pseudo[2] === ":skip" };
    }
    assert(false, "FileCard has an invalid pseudo-hunk set.");
  }
  const real = expect(
    /^real:([1-9]\d*)(:skip)?$/.exec(hunkSet),
    `FileCard has invalid hunk set ${hunkSet}.`,
  );
  return { kind: "real", count: Number(real[1]), skipped: real[2] === ":skip" };
}

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
  | LineNavigationCommand
  | {
      kind: "top";
    };

export type LineNavigationCommand = {
  kind: "line";
  fileIndex: number;
  target: LinePinTarget;
  abortSignal: AbortSignal;
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

// A completed line navigation hands back the line it scrolled to.
export type LineNavigationResult =
  | {
      state: "complete";
      cell: LineCell;
    }
  | {
      state: "missing";
    }
  | {
      state: "stopped";
    };

export type Navigation = {
  navigate(command: LineNavigationCommand): Promise<LineNavigationResult>;
  navigate(command: NavigationCommand): Promise<NavigationResult>;
  root: Accessor<HTMLElement>;
  readonly activeOperations: number;
};

export type NavigationProviderProps = {
  root: Accessor<HTMLElement>;
  children: JSX.Element;
};

const NavigationContext = createContext<Navigation>();

// In document order.
export function hunkTargetsOf(card: HTMLElement): HTMLElement[] {
  return Array.from(card.querySelectorAll<HTMLElement>("[data-hunk-target]"));
}

// A skipped target keeps its place and position but is never a destination.
export function hunkParticipates(target: HTMLElement): boolean {
  return !target.classList.contains("skip");
}

export function selectedFileCard(root: ParentNode): HTMLElement | null {
  const selected = fileCardsIn(root).filter(
    (card) => card.dataset.selectedHunkIndex !== undefined,
  );
  assert(selected.length <= 1, "A ChangeSet selects at most one FileCard.");
  return selected[0] ?? null;
}

export function useNavigation(): Navigation {
  const navigation = useContext(NavigationContext);
  return expect(navigation, "Hunk navigation requires NavigationProvider.");
}

// Exactly five lint-enforced callers: nextHunk, prevHunk, scrollFollow,
// navigateToFile and writeInitialHunkSelection.
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
    fileCardContaining(target),
    "Selected hunk target has no owning FileCard.",
  );
  assert(
    root.contains(fileCard),
    "Selected hunk target has no owning FileCard.",
  );
  const identity = readHunkIdentity(target);
  assert(
    fileCardIndex(fileCard) === identity.fileIndex,
    "Hunk target and FileCard indices do not match.",
  );

  for (const previousTarget of root.querySelectorAll<HTMLElement>(
    "[data-hunk-target][data-selected]",
  )) {
    previousTarget.removeAttribute("data-selected");
    previousTarget.removeAttribute("aria-current");
    previousTarget.classList.remove("active-hunk");
  }
  for (const previousCard of fileCardsIn(root).filter(
    (card) => card.dataset.selectedHunkIndex !== undefined,
  )) {
    delete previousCard.dataset.selectedHunkIndex;
    delete previousCard.dataset.selectedHunkBay;
    delete previousCard.dataset.selectedHunkKind;
    previousCard.classList.remove("active-hunk");
  }

  fileCard.dataset.selectedHunkKind = identity.kind;
  fileCard.dataset.selectedHunkIndex = String(identity.hunkIndex);
  if (identity.kind === "real" || identity.kind === "skip") {
    fileCard.dataset.selectedHunkBay = identity.bay;
  } else {
    delete fileCard.dataset.selectedHunkBay;
  }
  fileCard.classList.add("active-hunk");
  target.setAttribute("data-selected", "");
  target.setAttribute("aria-current", "true");
  target.classList.add("active-hunk");
}

// A file-state selection (husk/lazy/zero) resolves to the card's first target,
// so it survives admission into composed DOM. Hunk selections match on bay and
// index only, not kind: collapse/expand turns real and skip targets into each
// other at the same coordinates.
export function storedHunkTarget(
  card: HTMLElement,
  targets: readonly HTMLElement[],
): HTMLElement {
  const kind = card.dataset.selectedHunkKind;
  const bay = card.dataset.selectedHunkBay;
  const hunkIndex = card.dataset.selectedHunkIndex;
  const fileIndex = String(fileCardIndex(card));
  assert(
    kind !== undefined && hunkIndex !== undefined,
    "Selected FileCard has incomplete hunk identity.",
  );
  assert(
    /^(?:0|[1-9]\d*)$/.test(hunkIndex),
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
      String(readHunkIdentity(target).fileIndex) === fileIndex,
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
  const matchingTargets = targets.filter((candidate) => {
    const identity = readHunkIdentity(candidate);
    return (
      String(identity.fileIndex) === fileIndex &&
      (identity.kind === "real" || identity.kind === "skip") &&
      identity.bay === bay &&
      String(identity.hunkIndex) === hunkIndex
    );
  });
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
  if (renderDamaged(root)) {
    // Terminal renderer damage stays unselected.
    return;
  }
  const cards = fileCardsIn(root);
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
  const firstFileIndex = fileCardIndex(firstCard);
  // Bays each number their hunks from zero, so an index alone names no
  // single target; the snapshot's first hunk is the first target in DOM
  // order, which renderers keep equal to document order.
  const firstTarget = expect(
    firstCard.querySelector<HTMLElement>("[data-hunk-target]"),
    "First hunk target disappeared during initialization.",
  );
  assert(
    readHunkIdentity(firstTarget).fileIndex === firstFileIndex,
    "First hunk target has the wrong manifest index.",
  );
  assert(
    root.querySelector("[data-hunk-target][data-selected]") === null &&
      selectedFileCard(root) === null,
    "Initial hunk selection requires unselected DOM.",
  );
  selectHunk(root, firstTarget);
}

export function NavigationProvider(
  props: NavigationProviderProps,
): JSX.Element {
  const toast = useToasts();
  let alive = true;
  // Overlapping commands keep bay virtualization paused until the last one
  // settles; each releases its increment in finally, even after disposal.
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

      // Direction is of document movement: a finger moving up scrolls down.
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
      const card = fileCardContaining(element);
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
    const card = expect(
      selectedFileCard(root),
      "A non-empty ChangeSet requires exactly one selected hunk.",
    );
    return { card, target: storedHunkTarget(card, hunkTargetsOf(card)) };
  }

  // The scroll top that centers `target`, clamped to the document. It is
  // computed from offsets so a destination can be tested without moving the
  // page.
  function centeredViewportTop(target: HTMLElement): number {
    const viewportHeight = window.innerHeight;
    assert(
      viewportHeight > 0,
      "Navigation requires a positive viewport height.",
    );
    let targetDocumentTop = 0;
    let offsetElement: HTMLElement | null = target;
    while (offsetElement !== null) {
      targetDocumentTop += offsetElement.offsetTop;
      const parent: Element | null = offsetElement.offsetParent;
      assert(
        parent === null || parent instanceof HTMLElement,
        "Navigation target has a non-HTML offset parent.",
      );
      offsetElement = parent;
    }
    const targetHeight = target.offsetHeight;
    assert(
      Number.isFinite(targetDocumentTop) && Number.isFinite(targetHeight),
      "Navigation requires finite target geometry.",
    );
    const desiredTop =
      targetDocumentTop + targetHeight / 2 - viewportHeight / 2;
    const scrollingElement = expect(
      document.scrollingElement,
      "Navigation requires a document scroll element.",
    );
    const maximumTop = Math.max(
      0,
      scrollingElement.scrollHeight - viewportHeight,
    );
    return Math.min(maximumTop, Math.max(0, desiredTop));
  }

  // The first virtual bay, in document order, that the viewport at
  // `viewportTop` would bring into its rich-entry zone. Each enrichment
  // changes geometry, so callers recompute `viewportTop` after every one;
  // enriching each bay at most once terminates their loops.
  function virtualBayEnteringAt(
    root: HTMLElement,
    viewportTop: number,
    enriched: Set<BayHandle>,
  ): BayHandle | null {
    for (const bay of virtualBays(root)) {
      if (!enriched.has(bay) && bay.intersectsRichEntryZone(viewportTop)) {
        return bay;
      }
    }
    return null;
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
      fileCardContaining(initialTarget),
      "Navigation destination has no owning FileCard.",
    );
    assert(
      root.contains(card),
      "Navigation destination has no owning FileCard.",
    );
    const identity = readHunkIdentity(initialTarget);
    assert(
      fileCardIndex(card) === identity.fileIndex,
      "Navigation destination has invalid hunk coordinates.",
    );
    let target = initialTarget;
    // Only targets inside a bay can be virtual.
    const bay = bayContaining(initialTarget);
    if (bay !== null && bay.mode() === "virtual" && identity.kind === "real") {
      await bay.enrich();
      if (!alive) {
        return null;
      }
      const replacements = Array.from(
        card.querySelectorAll<HTMLElement>(PARTICIPATING_HUNK_SELECTOR),
      ).filter((candidate) => {
        const replacement = readHunkIdentity(candidate);
        return (
          replacement.kind === "real" &&
          replacement.fileIndex === identity.fileIndex &&
          replacement.bay === identity.bay &&
          replacement.hunkIndex === identity.hunkIndex
        );
      });
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
    const cards = fileCardsIn(root).filter(
      (card) => fileCardIndex(card) === fileIndex,
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
        readHunkIdentity(target).fileIndex === fileIndex,
        "FileCard target has the wrong manifest index.",
      );
      return target;
    }

    const enrichedBays = new Set<BayHandle>();

    let target = firstTarget();
    assert(
      readHunkIdentity(target).kind !== "husk",
      "File navigation cannot target a HuskFile.",
    );
    const destinationBay = bayContaining(target);
    if (
      destinationBay !== null &&
      destinationBay.mode() === "virtual" &&
      readHunkIdentity(target).kind === "real"
    ) {
      enrichedBays.add(destinationBay);
      await destinationBay.enrich();
      if (!alive) {
        return;
      }
      target = firstTarget();
      // The first target of any bay is that bay's hunk zero, so the
      // enriched bay must still lead with a real index-zero target.
      const enriched = readHunkIdentity(target);
      assert(
        enriched.kind === "real" && enriched.hunkIndex === 0,
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
      // page. Each enrichment changes geometry, so the next pass recalculates
      // it.
      // `enrich()` spans a rendered frame when it warms fresh chunks, so
      // observer-driven render-mode flips may interleave; that is safe because
      // enrichment completes with real geometry and every mode transition
      // measures real heights, so each pass recalculates against settled
      // layout and the loop's one-enrichment-per-bay set still terminates it.
      const enteringBay = virtualBayEnteringAt(root, viewportTop, enrichedBays);
      if (enteringBay === null) {
        break;
      }
      enrichedBays.add(enteringBay);
      await enteringBay.enrich();
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
    const cards = fileCardsIn(root).filter(
      (card) => fileCardIndex(card) === fileIndex,
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
  ): Promise<LineNavigationResult> {
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
    const card = fileCardAt(root, fileIndex);
    const enrichedBays = new Set<BayHandle>();

    while (alive && !abortSignal.aborted) {
      const prepared = await card.prepareLine(target, abortSignal);
      if (prepared.state !== "ready") {
        return prepared.state === "missing"
          ? { state: "missing" }
          : { state: "stopped" };
      }
      // A line row is never empty, so zero height means it is not laid out.
      assert(
        prepared.cell.row.offsetHeight > 0,
        "Line navigation requires positive target geometry.",
      );
      const viewportTop = centeredViewportTop(prepared.cell.row);
      const enteringBay = virtualBayEnteringAt(root, viewportTop, enrichedBays);
      if (enteringBay === null) {
        if (abortSignal.aborted || !alive) {
          return { state: "stopped" };
        }
        scrollGuard.stop();
        await settleCenteredScroll(prepared.cell.row);
        // A view switch or File collapse during the settle replaces or removes
        // the row; its cell must not become an anchor for review UI.
        if (abortSignal.aborted || !alive || !prepared.cell.row.isConnected) {
          return { state: "stopped" };
        }
        return { state: "complete", cell: prepared.cell };
      }
      enrichedBays.add(enteringBay);
      await enteringBay.enrich();
    }
    return { state: "stopped" };
  }

  async function relativeDestination(
    direction: "next" | "previous",
  ): Promise<HTMLElement | null> {
    const root = props.root();
    if (fileCardsIn(root).length === 0) {
      return null;
    }
    const location = selectedLocation(root);
    const rect = location.target.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.top >= window.innerHeight) {
      // Only targets inside a bay can have estimated geometry.
      await bayContaining(location.target)?.enrich();
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
    if (hunkParticipates(location.target)) {
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
    navigate,
  };

  function navigate(
    command: LineNavigationCommand,
  ): Promise<LineNavigationResult>;
  function navigate(command: NavigationCommand): Promise<NavigationResult>;
  async function navigate(
    command: NavigationCommand,
  ): Promise<NavigationResult | LineNavigationResult> {
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
  }

  // Native document listeners: the scroll source can be wheel, touch, keyboard
  // or a nested FileTree outside JSX events. One AbortController removes them
  // all on cleanup, which also revokes permission for a queued frame.
  onMount(() => {
    const root = props.root();
    if (renderDamaged(root)) {
      return;
    }
    // Loading and error shells have no navigable DOM.
    if (fileCardsIn(root).length === 0) {
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
