import type { LinePin } from "./fileUtils";

const linePinHashKey = "pin";

export function getLinePinFromHash(): LinePin | null {
  const params = new URLSearchParams(window.location.hash.slice(1));
  const rawPin = params.get(linePinHashKey);
  if (rawPin === null || rawPin.length === 0) {
    return null;
  }
  try {
    const pin = JSON.parse(rawPin) as Partial<LinePin>;
    if (
      pin !== null &&
      typeof pin.file === "string" &&
      (pin.side === "left" || pin.side === "right") &&
      typeof pin.line === "string" &&
      pin.line.length > 0
    ) {
      return {
        file: pin.file,
        side: pin.side,
        line: pin.line,
      };
    }
  } catch {
    return null;
  }
  return null;
}

export function setLinePinInHash(pin: LinePin) {
  const params = new URLSearchParams(window.location.hash.slice(1));
  params.set(linePinHashKey, JSON.stringify(pin));
  history.replaceState(
    {},
    "",
    `${window.location.pathname}${window.location.search}#${params.toString()}`,
  );
}

export function clearLinePinInHash() {
  const params = new URLSearchParams(window.location.hash.slice(1));
  params.delete(linePinHashKey);
  const hash = params.toString();
  history.replaceState(
    {},
    "",
    `${window.location.pathname}${window.location.search}${hash ? `#${hash}` : ""}`,
  );
}

export function linePinFromElement(lineNo: HTMLElement): LinePin | null {
  const file = lineNo.dataset.linePinFile;
  const side = lineNo.dataset.linePinSide;
  const line = lineNo.dataset.linePinLine;
  if (
    file === undefined ||
    file.length === 0 ||
    (side !== "left" && side !== "right") ||
    line === undefined ||
    line.length === 0
  ) {
    return null;
  }
  return { file, side, line };
}

function findPinnedLine(root: ParentNode, pin: LinePin): HTMLElement | null {
  for (const lineNo of root.querySelectorAll<HTMLElement>(
    ".line-no[data-line-pin-line]",
  )) {
    if (
      lineNo.dataset.linePinFile === pin.file &&
      lineNo.dataset.linePinSide === pin.side &&
      lineNo.dataset.linePinLine === pin.line
    ) {
      return lineNo;
    }
  }
  return null;
}

export function highlightPinnedLine(root: ParentNode, row: HTMLElement | null) {
  for (const node of root.querySelectorAll(".pinned-line")) {
    node.classList.remove("pinned-line");
  }
  row?.classList.add("pinned-line");
}

export function restorePinnedLine(
  root: ParentNode,
  restoredLinePinKey: string,
  setRestoredLinePinKey: (pinKey: string) => void,
) {
  const pin = getLinePinFromHash();
  if (pin === null) {
    highlightPinnedLine(root, null);
    setRestoredLinePinKey("");
    return;
  }
  const pinKey = JSON.stringify(pin);
  const lineNo = findPinnedLine(root, pin);
  if (lineNo === null) {
    highlightPinnedLine(root, null);
    return;
  }
  const row = lineNo.closest<HTMLElement>(".diff-row");
  if (
    restoredLinePinKey === pinKey &&
    row?.classList.contains("pinned-line") === true
  ) {
    return;
  }
  setRestoredLinePinKey(pinKey);
  highlightPinnedLine(root, row);
  row?.scrollIntoView({ block: "center", behavior: "instant" });
}
