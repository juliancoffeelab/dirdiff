import { createEffect, createSignal, onCleanup, type Accessor } from "solid-js";

type HunkNavigationOptions = {
  afterReconcile?: () => void;
};

function hunkAnchors(root: ParentNode | undefined): HTMLElement[] {
  return [...(root ?? document).querySelectorAll<HTMLElement>(".hunk-anchor")];
}

function selectCurrentHunk(
  index: number,
  scroll: boolean,
  root: ParentNode | undefined,
) {
  const anchors = hunkAnchors(root);
  if (!anchors.length) {
    return;
  }
  const selected = anchors[clamp(index, 0, anchors.length - 1)];
  for (const anchor of anchors) {
    anchor.classList.remove("active-hunk");
    anchor.removeAttribute("aria-current");
  }
  selected.classList.add("active-hunk");
  selected.setAttribute("aria-current", "true");
  if (scroll) {
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
  let reconcileTimer = 0;

  const anchors = () => hunkAnchors(root());

  const select = (index: number, scroll: boolean) => {
    selectCurrentHunk(index, scroll, root());
  };

  const reconcile = () => {
    clearTimeout(reconcileTimer);
    reconcileTimer = window.setTimeout(() => {
      const currentAnchors = anchors();
      if (!currentAnchors.length) {
        setCurrentIndex(0);
        options.afterReconcile?.();
        return;
      }

      const nextIndex = clamp(currentIndex(), 0, currentAnchors.length - 1);
      setCurrentIndex(nextIndex);
      select(nextIndex, false);
      options.afterReconcile?.();
    }, 120);
  };

  const scroll = (direction: 1 | -1) => {
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
    select(nextIndex, true);
  };

  const reconcileWhen = (dependencies: Accessor<unknown>[]) => {
    createEffect(() => {
      for (const dependency of dependencies) {
        dependency();
      }
      reconcile();
    });
  };

  onCleanup(() => clearTimeout(reconcileTimer));

  return {
    reconcileWhen,
    scrollNext: () => scroll(1),
    scrollPrev: () => scroll(-1),
  };
}
