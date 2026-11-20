import type { DecoratedPart, DiffRow } from "../../../../api/api";
import { isFoldRow, type FoldRow, type RenderRow } from "./folds";
import type { RealHunkIdentity } from "../../../navigation";
import { assert } from "../../../../utils";

const suppressedSyntaxClassPrefixes = [
  "ts-punctuation",
  "ts-operator",
  "ts-variable",
  "ts-parameter",
  "ts-field",
  "ts-local",
];

export type Side = "left" | "right";

type InlineMarker = " " | "-" | "+" | "*";

type InlineRowStatus = "equal" | "delete" | "insert" | "replace" | "move";

type UniformSideStatus = "insert" | "delete" | "replace";

function uniformPartStatus(parts: DecoratedPart[]): UniformSideStatus | null {
  let status: UniformSideStatus | null = null;
  for (const part of parts) {
    if (part.move !== undefined) {
      return null;
    }
    if (part.is_whitespace && part.diff_status === "unchanged") {
      continue;
    }
    if (part.diff_status === "unchanged" || part.diff_status === "move") {
      return null;
    }
    if (status === null) {
      status = part.diff_status;
    } else if (status !== part.diff_status) {
      return null;
    }
  }
  return status;
}

type InlineLineNumberState = {
  leftNo: number | null;
  rightNo: number | null;
};

// Rendered rows of large files are streamed through fixed-size chunk
// containers so the browser skips style and layout for off-viewport spans
// (62% of measured scroll-phase CPU was layout of monolithic row subtrees;
// application JS was ~2%). Only renders past the threshold pay the chunked
// lazy-render cost: chunking every file traded large-file freezes for
// visible render pop-in on files that previously scrolled at a solid 60fps
// (reported as a regression), so smaller renders keep the exact pre-chunk
// monolithic DOM. The threshold counts renderer rows, where a folded range
// contributes one visible bar. Expanding a fold larger than the threshold
// re-enters the renderer with the folded range and deliberately chunks it, so
// a small file's zero-chunk guarantee holds until such an expansion. The chunk
// size counts emitted row ELEMENTS (an inline changed row may emit two), so
// 50 elements ≈ 1100px ≈ one viewport and each chunk
// entering view renders within a frame budget.
const ROW_CHUNK_SIZE = 50;

const ROW_CHUNK_THRESHOLD = 600;

let chunkWarmHandle: number | null = null;

let warmingChunk: Element | null = null;

function warmPendingChunk(): void {
  chunkWarmHandle = null;
  // The previous chunk has had a rendered frame; returning it to `auto`
  // keeps its now-remembered real height.
  warmingChunk?.classList.remove("diff-row-chunk-warming");
  warmingChunk = null;
  const pending = document.querySelector(
    ".diff-row-chunk:not(.diff-row-chunk-warmed)",
  );
  if (pending === null) {
    return;
  }
  pending.classList.add("diff-row-chunk-warmed", "diff-row-chunk-warming");
  warmingChunk = pending;
  chunkWarmHandle = requestIdleCallback(warmPendingChunk);
}

function requestChunkWarming(): void {
  if (chunkWarmHandle === null) {
    chunkWarmHandle = requestIdleCallback(warmPendingChunk);
  }
}

export function forceChunkLayout(
  container: HTMLElement,
): readonly HTMLElement[] {
  const pending = Array.from(
    container.querySelectorAll<HTMLElement>(
      ".diff-row-chunk:not(.diff-row-chunk-warmed)",
    ),
  );
  for (const chunk of pending) {
    chunk.classList.add("diff-row-chunk-warming");
  }
  if (pending.length > 0) {
    // One forced reflow lays the now-visible chunks out synchronously.
    void container.offsetHeight;
  }
  return pending;
}

export function finishForcedChunkLayout(chunks: readonly HTMLElement[]): void {
  for (const chunk of chunks) {
    chunk.classList.add("diff-row-chunk-warmed");
    chunk.classList.remove("diff-row-chunk-warming");
  }
}

function createRowChunkAppender(
  fragment: DocumentFragment,
  chunked: boolean,
): {
  appendRow(element: DocumentFragment | HTMLElement): void;

  appendFold(element: HTMLElement): void;
} {
  let chunk: HTMLElement | null = null;
  let chunkCount = 0;
  return {
    appendRow(element) {
      if (!chunked) {
        fragment.append(element);
        return;
      }
      if (chunk === null || chunkCount >= ROW_CHUNK_SIZE) {
        chunk = document.createElement("div");
        chunk.className = "diff-row-chunk";
        fragment.append(chunk);
        chunkCount = 0;
        // Chunks are born only here, so this is the one warming trigger;
        // the idle pass finds them once the caller mounts the fragment.
        requestChunkWarming();
      }
      // Count emitted elements, not append calls: an inline changed row may
      // arrive as a two-element fragment, and the chunk's intrinsic-height
      // estimate assumes ROW_CHUNK_SIZE uniform row elements.
      chunkCount +=
        element instanceof DocumentFragment ? element.childElementCount : 1;
      chunk.append(element);
    },
    appendFold(element) {
      chunk = null;
      chunkCount = 0;
      fragment.append(element);
    },
  };
}

export function renderSplitRowsDom(
  rows: RenderRow[],
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  bayKey: string,
  startRow: number,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const appender = createRowChunkAppender(
    fragment,
    rows.length > ROW_CHUNK_THRESHOLD,
  );
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      appender.appendFold(
        renderSplitFoldDom(
          row,
          rowIndex,
          leftLabel,
          rightLabel,
          expandedFolds,
          fileIndex,
          bayKey,
          beforeRowsReplaced,
          onRowsChanged,
        ),
      );
      cursor += row.count;
    } else {
      appender.appendRow(
        renderSplitDiffRowDom(row, rowIndex, fileIndex, bayKey),
      );
      cursor += 1;
    }
  });
  return fragment;
}

export function renderInlineRowsDom(
  rows: RenderRow[],
  interleaveInlineRows: boolean,
  expandedFolds: Set<number>,
  fileIndex: number,
  bayKey: string,
  startRow: number,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const appender = createRowChunkAppender(
    fragment,
    rows.length > ROW_CHUNK_THRESHOLD,
  );
  const lineNumberState: InlineLineNumberState = {
    leftNo: null,
    rightNo: null,
  };
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      appender.appendFold(
        renderInlineFoldDom(
          row,
          rowIndex,
          interleaveInlineRows,
          expandedFolds,
          fileIndex,
          bayKey,
          beforeRowsReplaced,
          onRowsChanged,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      appender.appendRow(
        renderInlineDiffRowsDom(
          row,
          rowIndex,
          interleaveInlineRows,
          fileIndex,
          bayKey,
          lineNumberState,
        ),
      );
      cursor += 1;
    }
  });
  return fragment;
}

function renderSplitFoldDom(
  row: FoldRow,
  rowIndex: number,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  bayKey: string,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    beforeRowsReplaced(wrapper);
    renderFold();
    onRowsChanged();
  };

  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderSplitRowsDom(
        row.foldedRows,
        leftLabel,
        rightLabel,
        expandedFolds,
        fileIndex,
        bayKey,
        row.startRow,
        beforeRowsReplaced,
        onRowsChanged,
      );
      attachExpandedFoldToggle(fragment, toggle);
      wrapper.replaceChildren(fragment);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "diff-row fold-bar";
    button.dataset.rowIndex = String(rowIndex);
    button.title = "Expand folded rows";
    button.addEventListener("click", toggle);
    button.append(
      createFoldSideDom(row.count, row.label, leftLabel),
      createFoldSideDom(row.count, row.label, rightLabel),
    );
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

function renderInlineFoldDom(
  row: FoldRow,
  rowIndex: number,
  interleaveInlineRows: boolean,
  expandedFolds: Set<number>,
  fileIndex: number,
  bayKey: string,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    beforeRowsReplaced(wrapper);
    renderFold();
    onRowsChanged();
  };

  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderInlineRowsDom(
        row.foldedRows,
        interleaveInlineRows,
        expandedFolds,
        fileIndex,
        bayKey,
        row.startRow,
        beforeRowsReplaced,
        onRowsChanged,
      );
      attachExpandedFoldToggle(fragment, toggle);
      wrapper.replaceChildren(fragment);
      return;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "diff-row inline-diff-row inline-fold-bar";
    button.dataset.rowIndex = String(rowIndex);
    button.title = "Expand folded rows";
    button.addEventListener("click", toggle);
    const lineText = foldLineText(row.count);
    const label =
      row.label.length > 0
        ? `... ${lineText} in ${row.label}`
        : `... ${lineText}`;
    button.append(
      createPlainLineNumberDom(".."),
      createPlainLineNumberDom(".."),
      createElementWithClass("div", "fold-label inline-fold-label", label),
    );
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

type FoldToggle = {
  expanded: boolean;

  onToggle: () => void;
};

function attachExpandedFoldToggle(
  fragment: DocumentFragment,
  onToggle: () => void,
) {
  const row = fragment.querySelector(
    ".diff-row:not(.fold-bar):not(.inline-fold-bar)",
  );
  if (!(row instanceof HTMLElement)) {
    return;
  }
  row.classList.add("fold-toggle-row", "fold-expanded");
  row.title = "Fold rows";
  row.addEventListener("click", (event) => {
    if (
      event.target instanceof Element &&
      event.target.closest(".line-comment-trigger") !== null
    ) {
      return;
    }
    event.stopPropagation();
    onToggle();
  });

  const lineNumber = row.querySelector(".line-no");
  if (lineNumber instanceof HTMLElement) {
    lineNumber.prepend(createFoldToggleButtonDom({ expanded: true, onToggle }));
  }
}

function renderSplitDiffRowDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
  bayKey: string,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, "");
  element.dataset.rowIndex = String(rowIndex);
  if (row.hunk_index !== null) {
    const identity: RealHunkIdentity = {
      fileIndex,
      kind: "real",
      bay: bayKey,
      hunkIndex: row.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkBay = identity.bay;
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  element.append(
    createDiffSideDom(row, "left"),
    createDiffSideDom(row, "right"),
  );
  return element;
}

function renderInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  interleaveInlineRows: boolean,
  fileIndex: number,
  bayKey: string,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  function inlineSideExists(lineNo: number | null, text: string): boolean {
    return lineNo !== null || text.length > 0;
  }

  const rightText = sideText(row, "right");
  const leftText = sideText(row, "left");
  // The resulting side supplies shared inline content when it is non-empty.
  const sharedText = rightText.length > 0 ? rightText : leftText;
  const sharedParts =
    row.right_parts.length > 0 ? row.right_parts : row.left_parts;

  function renderChangedRow(): DocumentFragment | HTMLElement {
    const statusIsEqual = row.status === "equal";
    if (interleaveInlineRows && statusIsEqual) {
      const parts = interleaveRowSides(row);
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: parts.map((part) => part.text).join(""),
        parts,
        rowIndex,
        fileIndex,
        bayKey,
        sourceRow: row,
        lineNumberState,
      });
    }
    if (canCombineInsertOnlyChangedRow(row)) {
      return renderInlineDiffRowDom({
        status: statusIsEqual ? "equal" : "replace",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: rightText,
        parts: row.right_parts,
        rowIndex,
        fileIndex,
        bayKey,
        sourceRow: row,
        lineNumberState,
      });
    }
    const fragment = document.createDocumentFragment();
    const hasLeftSide = inlineSideExists(row.left_no, leftText);
    const hasRightSide = inlineSideExists(row.right_no, rightText);
    if (hasLeftSide) {
      fragment.append(
        renderInlineDiffRowDom({
          status: statusIsEqual ? "equal" : "delete",
          marker: "-",
          leftNo: row.left_no,
          rightNo: null,
          text: leftText,
          parts: row.left_parts,
          rowIndex,
          fileIndex,
          bayKey,
          sourceRow: row,
          lineNumberState,
        }),
      );
    }
    if (hasRightSide) {
      fragment.append(
        renderInlineDiffRowDom({
          status: statusIsEqual ? "equal" : "insert",
          marker: "+",
          leftNo: null,
          rightNo: row.right_no,
          text: rightText,
          parts: row.right_parts,
          rowIndex,
          fileIndex,
          bayKey,
          sourceRow: {
            ...row,
            hunk_index: hasLeftSide ? null : row.hunk_index,
          },
          lineNumberState,
        }),
      );
    }
    return fragment;
  }

  switch (row.status) {
    case "equal": {
      const hasChangedContent = [...row.left_parts, ...row.right_parts].some(
        (part) => part.diff_status !== "unchanged" && !part.is_whitespace,
      );
      if (hasChangedContent) {
        return renderChangedRow();
      }
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: sharedText,
        parts: sharedParts,
        rowIndex,
        fileIndex,
        bayKey,
        sourceRow: row,
        lineNumberState,
      });
    }
    case "delete":
      return renderInlineDiffRowDom({
        status: "delete",
        marker: "-",
        leftNo: row.left_no,
        rightNo: null,
        text: leftText,
        parts: row.left_parts,
        rowIndex,
        fileIndex,
        bayKey,
        sourceRow: row,
        lineNumberState,
      });
    case "insert":
      return renderInlineDiffRowDom({
        status: "insert",
        marker: "+",
        leftNo: null,
        rightNo: row.right_no,
        text: rightText,
        parts: row.right_parts,
        rowIndex,
        fileIndex,
        bayKey,
        sourceRow: row,
        lineNumberState,
      });
    case "replace":
      return renderChangedRow();
    case "move": {
      const fragment = document.createDocumentFragment();
      const hasLeftSide = inlineSideExists(row.left_no, leftText);
      const hasRightSide = inlineSideExists(row.right_no, rightText);
      if (hasLeftSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "move",
            marker: "*",
            leftNo: row.left_no,
            rightNo: null,
            text: leftText,
            parts: row.left_parts,
            rowIndex,
            fileIndex,
            bayKey,
            sourceRow: row,
            lineNumberState,
          }),
        );
      }
      if (hasRightSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "move",
            marker: "*",
            leftNo: null,
            rightNo: row.right_no,
            text: rightText,
            parts: row.right_parts,
            rowIndex,
            fileIndex,
            bayKey,
            sourceRow: {
              ...row,
              hunk_index: hasLeftSide ? null : row.hunk_index,
            },
            lineNumberState,
          }),
        );
      }
      return fragment;
    }
    default: {
      // Keep this assignment exhaustive when the backend RowStatus union grows.
      const unhandledStatus: never = row.status;
      throw new Error(`Unhandled diff row status: ${String(unhandledStatus)}.`);
    }
  }
}

function interleaveRowSides(row: DiffRow): DecoratedPart[] {
  const parts: DecoratedPart[] = [];
  let left = 0;
  let right = 0;
  // Characters of `row.right_parts[right]` already matched against the left.
  let rightOffset = 0;
  while (left < row.left_parts.length || right < row.right_parts.length) {
    while (row.left_parts[left]?.diff_status === "delete") {
      parts.push(row.left_parts[left]);
      left += 1;
    }
    while (row.right_parts[right]?.diff_status === "insert") {
      assert(
        rightOffset === 0,
        "Display-line insertion started inside a shared part.",
      );
      parts.push(row.right_parts[right]);
      right += 1;
    }
    if (left >= row.left_parts.length && right >= row.right_parts.length) {
      break;
    }
    const shared = row.left_parts[left];
    assert(
      shared !== undefined && shared.diff_status === "unchanged",
      "Display-line row sides disagree on their shared text.",
    );
    left += 1;
    // Characters of `shared.text` matched so far, and where the part slice
    // emitted next begins; they differ only after an insertion split it.
    let matched = 0;
    let sliceStart = 0;
    while (matched < shared.text.length) {
      while (row.right_parts[right]?.diff_status === "insert") {
        assert(
          rightOffset === 0,
          "Display-line insertion started inside a shared part.",
        );
        if (matched > sliceStart) {
          parts.push({
            ...shared,
            text: shared.text.slice(sliceStart, matched),
            // Only the first slice can still lead the line.
            is_leading_whitespace:
              sliceStart === 0 && shared.is_leading_whitespace,
          });
          sliceStart = matched;
        }
        parts.push(row.right_parts[right]);
        right += 1;
      }
      const counterpart = row.right_parts[right];
      assert(
        counterpart !== undefined && counterpart.diff_status === "unchanged",
        "Display-line row sides disagree on their shared text.",
      );
      const available = counterpart.text.slice(rightOffset);
      const taken = Math.min(available.length, shared.text.length - matched);
      assert(
        available.slice(0, taken) ===
          shared.text.slice(matched, matched + taken),
        "Display-line row sides disagree on their shared text.",
      );
      matched += taken;
      rightOffset += taken;
      if (rightOffset === counterpart.text.length) {
        right += 1;
        rightOffset = 0;
      }
    }
    parts.push(
      sliceStart === 0
        ? shared
        : {
            ...shared,
            text: shared.text.slice(sliceStart),
            is_leading_whitespace: false,
          },
    );
  }
  return parts;
}

function canCombineInsertOnlyChangedRow(row: DiffRow): boolean {
  const oldSideHasChanges = row.left_parts.some(
    (part) => part.diff_status !== "unchanged",
  );
  const rightChangedStatuses = row.right_parts
    .filter((part) => part.diff_status !== "unchanged")
    .map((part) => part.diff_status);
  return (
    !oldSideHasChanges &&
    rightChangedStatuses.length > 0 &&
    rightChangedStatuses.every((status) => status === "insert")
  );
}

function renderInlineDiffRowDom(props: {
  status: InlineRowStatus;

  marker: InlineMarker;

  leftNo: number | null;

  rightNo: number | null;

  text: string;

  parts: DecoratedPart[];

  rowIndex: number;

  fileIndex: number;

  bayKey: string;

  sourceRow: DiffRow;

  lineNumberState: InlineLineNumberState;
}): HTMLElement {
  const element = document.createElement("div");
  const displayedLeftNo = inlineDisplayLineNo(
    props.leftNo,
    "left",
    props.lineNumberState,
  );
  const displayedRightNo = inlineDisplayLineNo(
    props.rightNo,
    "right",
    props.lineNumberState,
  );
  element.className = diffRowClass(
    props.status,
    props.sourceRow,
    "inline-diff-row",
  );
  element.dataset.rowIndex = String(props.rowIndex);
  if (props.sourceRow.hunk_index !== null) {
    const identity: RealHunkIdentity = {
      fileIndex: props.fileIndex,
      kind: "real",
      bay: props.bayKey,
      hunkIndex: props.sourceRow.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkBay = identity.bay;
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  const code = createInlineLineCodeDom(
    props.marker,
    props.text,
    props.parts,
    props.parts === props.sourceRow.left_parts ? "left" : "right",
    props.sourceRow.status === "move" ? null : uniformPartStatus(props.parts),
  );
  const sourceSide =
    props.parts === props.sourceRow.left_parts ? "left" : "right";
  code.dataset.sourceSide = sourceSide;
  const sourceLine = sourceSide === "left" ? props.leftNo : props.rightNo;
  if (sourceLine !== null) {
    code.dataset.sourceLine = String(sourceLine);
  }
  element.append(
    createLineNumberDom(displayedLeftNo, "left"),
    createLineNumberDom(displayedRightNo, "right"),
    code,
  );
  return element;
}

function inlineDisplayLineNo(
  lineNo: number | null,
  side: Side,
  state: InlineLineNumberState,
): number | null {
  if (lineNo === null) {
    return lineNo;
  }
  const previousLineNo = side === "left" ? state.leftNo : state.rightNo;
  if (side === "left") {
    state.leftNo = lineNo;
  } else {
    state.rightNo = lineNo;
  }
  return previousLineNo === lineNo ? null : lineNo;
}

function diffRowClass(
  status: string,
  row: DiffRow,
  extraClass: string,
): string {
  const classes = ["diff-row"];
  if (extraClass.length > 0) {
    classes.push(extraClass);
  }
  classes.push(status);
  if (row.move !== undefined) {
    assert(status === "move", "Unpaired move data requires move row status.");
    assert(
      (row.left_no === null) !== (row.right_no === null),
      "An unpaired move row must contain exactly one source side.",
    );
    classes.push(
      row.left_no === null ? "move-new" : "move-old",
      `move-${row.move.group}`,
    );
    if (row.move.dimmed) {
      classes.push("move-dimmed");
    }
  }
  if (row.hunk_index !== null) {
    classes.push("hunk-anchor");
  }
  if (["replace", "insert", "delete", "move"].includes(status)) {
    const changedParts = [...row.left_parts, ...row.right_parts].filter(
      (part) => part.diff_status !== "unchanged",
    );
    if (
      changedParts.length > 0 &&
      changedParts.every((part) => part.is_whitespace)
    ) {
      classes.push("whitespace-only-change");
    }
  }
  return classes.join(" ");
}

function createDiffSideDom(row: DiffRow, side: Side): HTMLElement {
  const lineNo = side === "left" ? row.left_no : row.right_no;
  const text = sideText(row, side);
  const parts = side === "left" ? row.left_parts : row.right_parts;
  const uniformStatus = row.status === "move" ? null : uniformPartStatus(parts);
  const element = document.createElement("div");
  element.className = `diff-side side-${side}${
    lineNo === null && text === "" ? " empty-side" : ""
  }${uniformStatus === null ? "" : ` uniform-${uniformStatus}`}`;
  const codeElement = document.createElement("code");
  codeElement.className = "line-code";
  codeElement.dataset.sourceSide = side;
  if (lineNo !== null) {
    codeElement.dataset.sourceLine = String(lineNo);
  }
  appendDecoratedText(codeElement, text, parts, side, uniformStatus !== null);
  element.append(createLineNumberDom(lineNo, side), codeElement);
  return element;
}

function sideText(row: DiffRow, side: Side): string {
  const text = side === "left" ? row.left_text : row.right_text;
  if (text === null) {
    return "";
  }
  return text;
}

function createFoldSideDom(
  count: number,
  label: string,
  sideLabel: string,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "diff-side fold-side";
  element.dataset.sideLabel = sideLabel;
  element.append(
    createPlainLineNumberDom(".."),
    createElementWithClass(
      "div",
      "fold-label",
      label
        ? `... ${foldLineText(count)} in ${label}`
        : `... ${foldLineText(count)}`,
    ),
  );
  return element;
}

function foldLineText(count: number): string {
  return `${count} line${count === 1 ? "" : "s"}`;
}

function createLineNumberDom(lineNo: number | null, side: Side): HTMLElement {
  const element = document.createElement("div");
  element.className = "line-no";
  if (lineNo !== null) {
    element.dataset.linePinSide = side;
    element.dataset.linePinLine = String(lineNo);
    element.title = "Pin line";
    const commentTriggers = document.createElement("span");
    commentTriggers.className = "line-comment-triggers";
    element.append(commentTriggers);
  }
  element.append(lineNo === null ? "" : String(lineNo));
  return element;
}

function createPlainLineNumberDom(text: string): HTMLElement {
  return createElementWithClass("div", "line-no", text);
}

function createFoldToggleButtonDom(foldToggle: FoldToggle): HTMLButtonElement {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "inline-fold-toggle";
  button.ariaLabel = foldToggle.expanded ? "Fold rows" : "Expand folded rows";
  button.textContent = foldToggle.expanded ? "▾" : "▸";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    foldToggle.onToggle();
  });
  return button;
}

function createInlineLineCodeDom(
  marker: InlineMarker,
  text: string,
  parts: DecoratedPart[],
  side: Side,
  uniformStatus: UniformSideStatus | null,
): HTMLElement {
  const element = document.createElement("code");
  element.className = `line-code inline-line-code${
    uniformStatus === null ? "" : ` uniform-${uniformStatus}`
  }`;
  const markerElement = document.createElement("span");
  markerElement.className = "inline-marker";
  markerElement.ariaHidden = "true";
  markerElement.textContent = marker;
  element.append(markerElement);
  appendDecoratedText(element, text, parts, side, uniformStatus !== null);
  return element;
}

function appendDecoratedText(
  element: HTMLElement,
  text: string,
  parts: DecoratedPart[],
  side: Side,
  cellCarriesFill: boolean,
) {
  function isSuppressedSyntaxClass(className: string): boolean {
    return suppressedSyntaxClassPrefixes.some(
      (prefix) => className === prefix || className.startsWith(`${prefix}-`),
    );
  }

  function visibleSyntaxClasses(classes: string[]): string[] {
    if (!classes.length || classes.every(isSuppressedSyntaxClass)) {
      return [];
    }
    return classes;
  }

  assert(
    parts.map((part) => part.text).join("") === text,
    "Decorated parts must reconstruct their complete row text.",
  );
  // A line-local enclosure groups syntax fragments with the same move. It
  // belongs to this code cell; no DOM or move state survives row replacement.
  let enclosure: HTMLSpanElement | null = null;
  for (const [index, part] of parts.entries()) {
    if (part.move === undefined) {
      enclosure = null;
    } else {
      const { left, right } = part.move;
      const key = [left, right]
        .flatMap((span) => [
          span.start_line,
          span.start_column,
          span.end_line,
          span.end_column,
        ])
        .join(":");
      if (enclosure?.dataset.moveKey !== key) {
        enclosure = document.createElement("span");
        enclosure.className = "token-moved";
        enclosure.dataset.moveKey = key;
        enclosure.dataset.moveSide = side;
        const target = side === "left" ? right : left;
        enclosure.title = `Moved ${side === "left" ? "to" : "from"} ${target.start_line === target.end_line ? `line ${target.start_line}` : `lines ${target.start_line}–${target.end_line}`} (old ${left.start_line}:${left.start_column + 1}–${left.end_line}:${left.end_column + 1} → new ${right.start_line}:${right.start_column + 1}–${right.end_line}:${right.end_column + 1}; ends exclusive)`;
        element.append(enclosure);
      }
    }
    const tokenChanged = part.diff_status !== "unchanged";
    // An unchanged whitespace part between two changed parts of one status is
    // painted with that status, so a run of changed words reads as one band
    // instead of a checkerboard. The part's data is untouched: it keeps no
    // change class of its own beyond the paint, and no title.
    const previous = index > 0 ? parts[index - 1] : undefined;
    const next = parts[index + 1];
    const bridgedStatus =
      !tokenChanged &&
      part.is_whitespace &&
      part.move === undefined &&
      previous !== undefined &&
      next !== undefined &&
      previous.diff_status !== "unchanged" &&
      previous.diff_status === next.diff_status &&
      previous.move === undefined &&
      next.move === undefined
        ? previous.diff_status
        : null;
    const syntaxClasses = visibleSyntaxClasses(part.syntax_classes);
    if (
      syntaxClasses.length === 0 &&
      !tokenChanged &&
      bridgedStatus === null &&
      part.move === undefined
    ) {
      element.append(part.text);
      continue;
    }
    const span = document.createElement("span");
    const classes = syntaxClasses.length
      ? ["ts-token", ...new Set(syntaxClasses)]
      : [];
    const paintedStatus = bridgedStatus ?? part.diff_status;
    const showTokenChange =
      (tokenChanged || bridgedStatus !== null) && !cellCarriesFill;
    if (showTokenChange) {
      classes.push("token-changed", `token-${paintedStatus}`);
    }
    if (showTokenChange && part.is_whitespace) {
      classes.push("whitespace");
    }
    if (showTokenChange && part.is_leading_whitespace) {
      classes.push("whitespace-leading");
    }
    span.className = classes.join(" ");
    if (
      tokenChanged &&
      showTokenChange &&
      part.is_whitespace &&
      part.move === undefined
    ) {
      span.title = "Whitespace changed";
    }
    span.textContent = part.text;
    (enclosure ?? element).append(span);
  }
}

function createElementWithClass<K extends keyof HTMLElementTagNameMap>(
  tagName: K,
  className: string,
  text: string,
): HTMLElementTagNameMap[K] {
  const element = document.createElement(tagName);
  element.className = className;
  element.textContent = text;
  return element;
}
