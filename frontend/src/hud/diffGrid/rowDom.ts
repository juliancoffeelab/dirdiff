/**
 * Builds the imperative row DOM for one DiffGrid render.
 *
 * The module exports the three complete-fragment builders —
 * renderSplitRowsDom, renderInlineRowsDom, and renderCombinedInlineRowsDom —
 * plus the Side type shared with their caller. Every function here is a pure
 * DOM constructor: given validated backend rows, fold state, and the two
 * caller behaviors (closing anchored review UI before a fold replacement and
 * the after-rows-changed notification), it returns detached DOM and touches
 * no Solid reactivity, no component state, no queries, and no globals beyond
 * the chunk-warming pass below.
 *
 * The module also owns row chunking: large renders stream rows through
 * fixed-size content-visibility containers, and one idle-paced module-level
 * warm-up pass renders each new chunk once so the browser records its real
 * height. That warming handle is the module's only mutable state; it is
 * re-armed whenever a chunk is created and dies with the last unwarmed chunk.
 *
 * It must not listen to events, own review markers or line pins, decide view
 * modes, or fetch anything; those belong to the DiffGrid component.
 */
import type { DecoratedPart, DiffRow, RowStatus } from "../../api/api";
import { isFoldRow, type FoldRow, type RenderRow } from "./folds";
import type { RealHunkIdentity } from "../navigation";
import { assert } from "../../utils";

const suppressedSyntaxClassPrefixes = [
  "ts-punctuation",
  "ts-operator",
  "ts-variable",
  "ts-parameter",
  "ts-field",
  "ts-local",
];

/**
 * Selects one validated side of a split diff row.
 *
 * The value controls which backend line, text, and decorated-part fields are
 * read; it does not represent a user selection or persistent application state.
 */
export type Side = "left" | "right";

/**
 * Represents the single visible prefix emitted for one inline diff line.
 *
 * Every inline row must provide exactly one marker from this closed set. The
 * marker communicates presentation only and is hidden from accessibility text.
 */
type InlineMarker = " " | "-" | "+" | "*";

/**
 * Represents the CSS and token treatment available to an inline render row.
 *
 * The union excludes fold rows and maps the split-only backend statuses into
 * the complete set that inline rendering may emit.
 */
type InlineRowStatus = "equal" | "delete" | "insert" | "replace" | "move";

/**
 * Tracks the most recently rendered number on each inline side.
 *
 * The state is local to one fragment render and suppresses duplicate line
 * numbers created when a backend row expands into multiple inline rows.
 */
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
// monolithic DOM. The threshold counts renderer rows, where a collapsed
// fold contributes one visible bar — expanding a fold larger than the
// threshold re-enters the renderer with the folded range and deliberately
// chunks it, so a small file's zero-chunk guarantee holds until such an
// expansion. The chunk size counts emitted row ELEMENTS (an inline replace
// row emits two), so 50 elements ≈ 1100px ≈ one viewport and each chunk
// entering view renders within a frame budget.
const ROW_CHUNK_SIZE = 50;
const ROW_CHUNK_THRESHOLD = 600;

// One idle-paced warm-up pass owns these; see requestChunkWarming.
let chunkWarmHandle: number | null = null;
let warmingChunk: Element | null = null;

/**
 * Warms every mounted `.diff-row-chunk` once so its real height is known.
 *
 * A skipped chunk's height is the 1100px estimate until its first render,
 * and every estimate-to-real replacement moves document geometry — felt as
 * scroll-time pop-in and as navigation landing short. Warming renders one
 * pending chunk per idle callback by holding `.diff-row-chunk-warming`
 * (content-visibility: visible) on it for one rendered frame, which records
 * the chunk's real height as the browser's last remembered size; the chunk
 * then returns to `auto` and keeps skipping off-viewport work with exact
 * geometry. `.diff-row-chunk-warmed` is the bookkeeping marker, so a chunk
 * warms at most once and rebuilt chunks (re-render, fold expansion) warm
 * again. Idle pacing defers warming behind load and scroll work; one chunk
 * per pass keeps the forced layout within a frame budget.
 */
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

/**
 * Starts the chunk warm-up pass unless one is already scheduled.
 *
 * Called whenever a chunked render is built, so freshly mounted chunks are
 * picked up; the pass ends itself once no unwarmed chunk remains mounted.
 */
function requestChunkWarming(): void {
  if (chunkWarmHandle === null) {
    chunkWarmHandle = requestIdleCallback(warmPendingChunk);
  }
}

/**
 * Groups appended row elements into `.diff-row-chunk` containers.
 *
 * With `chunked` false every append lands directly on the fragment,
 * reproducing the monolithic pre-chunk DOM. Otherwise consecutive non-fold
 * rows land in chunks of at most `ROW_CHUNK_SIZE` emitted elements; a fold
 * wrapper closes the current chunk and stays top-level because it replaces
 * its own subtree on expansion. The appender owns only the supplied
 * fragment for the duration of one build and guarantees document order is
 * exactly the append order.
 */
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
      // Count emitted elements, not append calls: an inline replace row
      // arrives as a two-element fragment, and the chunk's intrinsic-height
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

/**
 * Renders an ordered `RenderRow` range into split-view DOM.
 *
 * `startRow` is the required source-row offset for stable row identity. Folded
 * ranges advance by their represented count while ordinary rows advance once.
 */
export function renderSplitRowsDom(
  rows: RenderRow[],
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
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
          beforeRowsReplaced,
          onRowsChanged,
        ),
      );
      cursor += row.count;
    } else {
      appender.appendRow(renderSplitDiffRowDom(row, rowIndex, fileIndex));
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Renders an ordered `RenderRow` range into ordinary inline-view DOM.
 *
 * `startRow` is the required source-row offset. One backend row may emit two
 * visible rows, while shared line-number state suppresses duplicate numbers.
 */
export function renderInlineRowsDom(
  rows: RenderRow[],
  expandedFolds: Set<number>,
  fileIndex: number,
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
          expandedFolds,
          fileIndex,
          false,
          beforeRowsReplaced,
          onRowsChanged,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      appender.appendRow(
        renderInlineDiffRowsDom(row, rowIndex, fileIndex, lineNumberState),
      );
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Renders inline rows with the engine-specific insert-only replacement combination.
 *
 * The required source-row offset and fold set retain the same identity rules as
 * ordinary inline rendering; rows outside the narrow combination predicate remain
 * byte-for-byte equivalent in presentation structure.
 */
export function renderCombinedInlineRowsDom(
  rows: RenderRow[],
  expandedFolds: Set<number>,
  fileIndex: number,
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
          expandedFolds,
          fileIndex,
          true,
          beforeRowsReplaced,
          onRowsChanged,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      appender.appendRow(
        renderCombinedInlineDiffRowsDom(
          row,
          rowIndex,
          fileIndex,
          lineNumberState,
        ),
      );
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Creates one stateful split-view fold subtree around an immutable FoldRow.
 *
 * Expansion lives only in the owning DiffGrid set. Toggling replaces this
 * wrapper's children; validated folded context contains no hunk boundaries.
 */
function renderSplitFoldDom(
  row: FoldRow,
  rowIndex: number,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  /**
   * Toggles this source-row range in the owning local expansion set.
   *
   * The mutation is DiffGrid-local and immediately rebuilds only this fold
   * wrapper; it never changes file or ChangeSet expansion.
   */
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

  /**
   * Replaces this split fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows preserve source offsets and receive one fold affordance;
   * folded rows render only the backend-provided fold bar.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderSplitRowsDom(
        row.foldedRows,
        leftLabel,
        rightLabel,
        expandedFolds,
        fileIndex,
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

/**
 * Creates one stateful inline-view fold subtree around an immutable FoldRow.
 *
 * The required row-combination policy is propagated into expanded nested rows. The
 * wrapper owns only its current DOM children and local toggle listeners.
 */
function renderInlineFoldDom(
  row: FoldRow,
  rowIndex: number,
  expandedFolds: Set<number>,
  fileIndex: number,
  combineInsertOnlyReplaceRows: boolean,
  beforeRowsReplaced: (container: Node) => void,
  onRowsChanged: () => void,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  /**
   * Toggles this source-row range in the owning local expansion set.
   *
   * The mutation is DiffGrid-local and immediately rebuilds only this fold
   * wrapper; it never changes file or ChangeSet expansion.
   */
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

  /**
   * Replaces this inline fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows retain the required row-combination policy and source offsets;
   * folded rows render only the backend-provided fold bar.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const rows = row.foldedRows;
      const fragment =
        combineInsertOnlyReplaceRows === true
          ? renderCombinedInlineRowsDom(
              rows,
              expandedFolds,
              fileIndex,
              row.startRow,
              beforeRowsReplaced,
              onRowsChanged,
            )
          : renderInlineRowsDom(
              rows,
              expandedFolds,
              fileIndex,
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

/**
 * Describes the local interactive control attached to an expanded fold row.
 *
 * `expanded` determines visual treatment and `onToggle` mutates only the owning
 * DiffGrid's local fold set; it is not file-expansion or application state.
 */
type FoldToggle = { expanded: boolean; onToggle: () => void };

/**
 * Attaches the fold affordance to the first visible row of an expanded fold.
 *
 * An empty fragment is accepted and produces no affordance. The listener is
 * owned by the supplied fragment and disappears when its DOM is replaced. The
 * complete first row is the expanded fold edge, so its activation never reaches
 * delegated line-pin handling even though it displays backend line numbers.
 */
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

/**
 * Renders one ordinary backend row as a two-sided split-view element.
 *
 * The required source index and file index become stable DOM identity. Fold
 * disclosure is attached separately to the first expanded row.
 */
function renderSplitDiffRowDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, "");
  element.dataset.rowIndex = String(rowIndex);
  if (row.hunk_index !== null) {
    const identity: RealHunkIdentity = {
      fileIndex,
      kind: "real",
      hunkIndex: row.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  element.append(
    createDiffSideDom(row, "left"),
    createDiffSideDom(row, "right"),
  );
  return element;
}

/**
 * Renders one backend row as the one or two elements required by inline view.
 *
 * Equal, insert, and delete rows emit one element; replace and move rows may
 * emit both sides while transferring hunk identity to the first visible side.
 * Unsupported backend statuses throw exhaustively.
 */
function renderInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  /**
   * Reports whether one inline side has a line identity or visible text.
   *
   * Null plus empty text is the only absent-side representation; zero and an
   * empty line with a number remain present.
   */
  function inlineSideExists(lineNo: number | null, text: string): boolean {
    return lineNo !== null || text.length > 0;
  }

  const rightText = sideText(row, "right");
  const leftText = sideText(row, "left");
  // The resulting side supplies shared inline content when it is non-empty.
  const sharedText = rightText.length > 0 ? rightText : leftText;
  const sharedParts =
    row.right_parts.length > 0 ? row.right_parts : row.left_parts;

  switch (row.status) {
    case "equal":
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: sharedText,
        parts: sharedParts,
        rowIndex,
        fileIndex,
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
      });
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
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
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
        sourceRow: row,
        lineNumberState,
        tokenRowStatus: null,
      });
    case "replace": {
      const fragment = document.createDocumentFragment();
      const hasLeftSide = inlineSideExists(row.left_no, leftText);
      const hasRightSide = inlineSideExists(row.right_no, rightText);
      if (hasLeftSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "delete",
            marker: "-",
            leftNo: row.left_no,
            rightNo: null,
            text: leftText,
            parts: row.left_parts,
            rowIndex,
            fileIndex,
            sourceRow: row,
            lineNumberState,
            tokenRowStatus: "replace",
          }),
        );
      }
      if (hasRightSide) {
        fragment.append(
          renderInlineDiffRowDom({
            status: "insert",
            marker: "+",
            leftNo: null,
            rightNo: row.right_no,
            text: rightText,
            parts: row.right_parts,
            rowIndex,
            fileIndex,
            sourceRow: {
              ...row,
              hunk_index: hasLeftSide ? null : row.hunk_index,
            },
            lineNumberState,
            tokenRowStatus: "replace",
          }),
        );
      }
      return fragment;
    }
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
            sourceRow: row,
            lineNumberState,
            tokenRowStatus: null,
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
            sourceRow: {
              ...row,
              hunk_index: hasLeftSide ? null : row.hunk_index,
            },
            lineNumberState,
            tokenRowStatus: null,
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

/**
 * Renders one inline row with the narrow insert-only replacement optimization.
 *
 * Rows failing the exact combination predicate delegate to ordinary inline
 * rendering. Combined rows retain both backend line numbers and hunk identity.
 */
function renderCombinedInlineDiffRowsDom(
  row: DiffRow,
  rowIndex: number,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  if (!canCombineInsertOnlyReplaceRow(row)) {
    return renderInlineDiffRowsDom(row, rowIndex, fileIndex, lineNumberState);
  }

  const rightText = sideText(row, "right");
  return renderInlineDiffRowDom({
    status: "replace",
    marker: " ",
    leftNo: row.left_no,
    rightNo: row.right_no,
    text: rightText,
    parts: row.right_parts,
    rowIndex,
    fileIndex,
    sourceRow: row,
    lineNumberState,
    tokenRowStatus: null,
  });
}

/**
 * Recognizes replacement rows whose changed tokens exist only as insertions.
 *
 * The predicate is deliberately strict: any changed old-side token or any
 * non-insert new-side token preserves the ordinary two-line representation.
 */
function canCombineInsertOnlyReplaceRow(row: DiffRow): boolean {
  if (row.status !== "replace") {
    return false;
  }
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

/**
 * Creates one concrete inline row element from a complete render description.
 *
 * Callers provide both line numbers, decorated text inputs, stable source-row
 * identity, and explicit token-row override state. Missing sides and absent
 * overrides use null, never undefined; only the new DOM element is mutated.
 */
function renderInlineDiffRowDom(props: {
  status: InlineRowStatus;
  marker: InlineMarker;
  leftNo: number | null;
  rightNo: number | null;
  text: string;
  parts: DecoratedPart[];
  rowIndex: number;
  fileIndex: number;
  sourceRow: DiffRow;
  lineNumberState: InlineLineNumberState;
  tokenRowStatus: InlineRowStatus | null;
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
      hunkIndex: props.sourceRow.hunk_index,
    };
    element.dataset.hunkTarget = "";
    element.dataset.hunkKind = identity.kind;
    element.dataset.fileIndex = String(identity.fileIndex);
    element.dataset.hunkIndex = String(identity.hunkIndex);
  }
  element.append(
    createLineNumberDom(displayedLeftNo, "left"),
    createLineNumberDom(displayedRightNo, "right"),
    createInlineLineCodeDom(
      props.marker,
      props.text,
      props.parts,
      props.tokenRowStatus === null ? props.status : props.tokenRowStatus,
    ),
  );
  return element;
}

/**
 * Suppresses immediately repeated inline line numbers on one rendered side.
 *
 * A missing local state intentionally disables suppression. Null is a real
 * absent-side value and never changes the remembered number.
 */
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

/**
 * Builds the complete established class list for one rendered diff row.
 *
 * The required extra class may be empty and affects presentation only;
 * whitespace and hunk classes derive from validated row data.
 */
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

/**
 * Creates one split-view side with line identity and decorated code content.
 *
 * Callers supply a validated backend row and exact side. Null line numbers and
 * empty text produce the established empty-side treatment rather than a husk.
 */
function createDiffSideDom(row: DiffRow, side: Side): HTMLElement {
  const lineNo = side === "left" ? row.left_no : row.right_no;
  const text = sideText(row, side);
  const parts = side === "left" ? row.left_parts : row.right_parts;
  const element = document.createElement("div");
  element.className = `diff-side side-${side}${
    lineNo === null && text === "" ? " empty-side" : ""
  }`;
  const codeElement = document.createElement("code");
  codeElement.className = "line-code";
  appendDecoratedText(codeElement, text, parts, row.status);
  element.append(createLineNumberDom(lineNo, side), codeElement);
  return element;
}

/**
 * Normalizes nullable backend text for one selected side to a render string.
 *
 * Backend null means that side is absent and becomes the empty string; no other
 * text normalization or whitespace trimming is performed.
 */
function sideText(row: DiffRow, side: Side): string {
  const text = side === "left" ? row.left_text : row.right_text;
  if (text === null) {
    return "";
  }
  return text;
}

/**
 * Creates one half of a split fold bar with its side-specific accessible label.
 *
 * The count must be the positive size of the represented FoldRow. The returned
 * element owns no toggle listener; its parent fold bar handles activation.
 */
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

/**
 * Formats a validated fold-row count with the correct singular or plural noun.
 *
 * Callers provide the FoldRow count; this helper does not validate ranges or
 * add context such as the fold label.
 */
function foldLineText(count: number): string {
  return `${count} line${count === 1 ? "" : "s"}`;
}

/**
 * Creates one line-number cell and its exact line-local interaction coordinates.
 *
 * A visible line number contributes its exact side and backend line coordinate;
 * the enclosing DiffGrid contributes file and region identity during pin or
 * comment activation. Its nested comment button has no listener of its own and
 * is routed by the grid's delegated listener. An absent or duplicate-suppressed
 * number is null and therefore neither pinnable nor commentable.
 */
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

/**
 * Creates a non-interactive line-number cell for fixed fold placeholders.
 *
 * The caller supplies complete visible text; no pin or fold metadata is added.
 */
function createPlainLineNumberDom(text: string): HTMLElement {
  return createElementWithClass("div", "line-no", text);
}

/**
 * Creates the accessible disclosure control attached to an expanded fold row.
 *
 * Activation stops row propagation and invokes exactly the supplied local
 * toggle callback; it does not mutate ChangeSet or browser state.
 */
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

/**
 * Creates one inline-view code cell including its visible change marker.
 *
 * The marker is aria-hidden, while all supplied text remains native searchable
 * content. Token decoration uses the inline status mapped to backend semantics.
 */
function createInlineLineCodeDom(
  marker: InlineMarker,
  text: string,
  parts: DecoratedPart[],
  rowStatus: InlineRowStatus,
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code inline-line-code";
  const markerElement = document.createElement("span");
  markerElement.className = "inline-marker";
  markerElement.ariaHidden = "true";
  markerElement.textContent = marker;
  element.append(markerElement);
  const inlineTokenRowStatus =
    rowStatus === "insert" || rowStatus === "delete" ? "replace" : rowStatus;
  appendDecoratedText(element, text, parts, inlineTokenRowStatus);
  return element;
}

/**
 * Appends native-searchable text from its backend-woven decoration parts.
 *
 * The parts must reconstruct `text` exactly. The function suppresses redundant
 * diff coloring already expressed by the row and mutates only `element`.
 */
function appendDecoratedText(
  element: HTMLElement,
  text: string,
  parts: DecoratedPart[],
  rowStatus: RowStatus,
) {
  /**
   * Recognizes tree-sitter syntax classes hidden by the established diff theme.
   *
   * Exact prefix names and their hyphenated descendants are suppressed;
   * unrelated class names remain visible.
   */
  function isSuppressedSyntaxClass(className: string): boolean {
    return suppressedSyntaxClassPrefixes.some(
      (prefix) => className === prefix || className.startsWith(`${prefix}-`),
    );
  }

  /**
   * Removes syntax decoration when every class is intentionally suppressed.
   *
   * Mixed visible/suppressed class lists pass through unchanged so established
   * token styling can choose the visible class; the input array is not mutated.
   */
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
  for (const part of parts) {
    const tokenChanged = part.diff_status !== "unchanged";
    const syntaxClasses = visibleSyntaxClasses(part.syntax_classes);
    if (syntaxClasses.length === 0 && !tokenChanged) {
      element.append(part.text);
      continue;
    }
    const span = document.createElement("span");
    const classes = syntaxClasses.length
      ? ["ts-token", ...new Set(syntaxClasses)]
      : [];
    const rowAlreadyShowsTokenChange =
      (rowStatus === "insert" || rowStatus === "delete") &&
      rowStatus === part.diff_status;
    const showTokenChange = tokenChanged && !rowAlreadyShowsTokenChange;
    if (showTokenChange) {
      classes.push("token-changed", `token-${part.diff_status}`);
    }
    if (showTokenChange && part.is_whitespace) {
      classes.push("whitespace");
    }
    if (showTokenChange && part.is_leading_whitespace) {
      classes.push("whitespace-leading");
    }
    span.className = classes.join(" ");
    if (showTokenChange && part.is_whitespace) {
      span.title = "Whitespace changed";
    }
    span.textContent = part.text;
    element.append(span);
  }
}

/**
 * Creates one typed HTML element with complete class and text content.
 *
 * The tag must be a standard HTMLElement tag. The returned element has no
 * event listeners, data attributes, or retained application state.
 */
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
