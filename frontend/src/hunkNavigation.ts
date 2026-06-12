import { createEffect, createSignal, onCleanup, type Accessor } from "solid-js";

type HunkNavigationOptions = {
  afterReconcile?: () => void;
};

const SCROLL_FOLLOW_INTERVAL_MS = 100;
const PROGRAMMATIC_SCROLL_IGNORE_MS = 150;
const READING_LINE_RATIO = 0.5;

function hunkAnchors(root: ParentNode | undefined): HTMLElement[] {
  return [...(root ?? document).querySelectorAll<HTMLElement>(".hunk-anchor")];
}

function selectCurrentHunk(options: {
  index: number;
  scroll: boolean;
  root: ParentNode | undefined;
}) {
  const anchors = hunkAnchors(options.root);
  if (!anchors.length) {
    return;
  }
  const selected = anchors[clamp(options.index, 0, anchors.length - 1)];
  for (const anchor of anchors) {
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
}

function wrapIndex(index: number, length: number): number {
  return ((index % length) + length) % length;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
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

  const select = (options: { index: number; scroll: boolean }) => {
    selectCurrentHunk({ ...options, root: root() });
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
      if (!currentAnchors.length) {
        setCurrentIndex(0);
        options.afterReconcile?.();
        return;
      }

      const nextIndex = clamp(currentIndex(), 0, currentAnchors.length - 1);
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

    const nextIndex = wrapIndex(
      currentIndex() + direction,
      currentAnchors.length,
    );
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
