/**
 * Renders immutable text-diff rows as the established split or inline grid.
 *
 * The module exports DiffGrid and its row contracts. It owns the imperative DOM
 * kernel, fold-row projection, syntax decoration, side-selection behavior, and
 * hunk anchor attributes for one FullFile body. Callers provide fully validated
 * backend rows and complete presentation inputs. It must not fetch data, own
 * ChangeSet state, navigate hunks, virtualize files, or render notebook framing.
 */
import { createEffect } from "solid-js";
import type {
  DiffRow,
  FoldHint,
  InlineToken,
  RowStatus,
  SyntaxSpan,
} from "../api/api";
import type { DiffViewMode } from "./App";
import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";
import { clamp } from "../utils";

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
 * The value controls which backend line, text, token, and syntax fields are
 * read; it does not represent a user selection or persistent application state.
 */
type Side = "left" | "right";

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
 * the complete set that inline projection may emit.
 */
type InlineRowStatus = "equal" | "delete" | "insert" | "replace" | "move";

/**
 * Renders one complete immutable text FileDiff using the established grid DOM.
 *
 * Callers provide the stable manifest file index, labels, validated rows and
 * fold hints, current view, and both fold policies. The component owns only its
 * rendered row DOM; it performs no fetching, navigation, or file-level state.
 */
export function DiffGrid(props: {
  fileIndex: number;
  displayName: string;
  leftLabel: string;
  rightLabel: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  viewMode: DiffViewMode;
  aggressiveFolds: boolean;
  collapseInsertOnlyReplaceRows: boolean;
}) {
  return (
    <div
      class="diff-grid"
      classList={{
        "diff-grid-inline": props.viewMode === "inline",
        "diff-grid-collapse-insert-only-replace": Boolean(
          props.collapseInsertOnlyReplaceRows,
        ),
      }}
    >
      {props.viewMode === "inline" ? (
        <InlineHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      ) : (
        <SplitHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      )}
      <ImperativeDiffLines
        fileIndex={props.fileIndex}
        displayName={props.displayName}
        rows={props.rows}
        foldHints={props.foldHints}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        viewMode={props.viewMode}
        aggressiveFolds={props.aggressiveFolds}
        collapseInsertOnlyReplaceRows={props.collapseInsertOnlyReplaceRows}
      />
    </div>
  );
}

/**
 * Renders the two required side labels above a split diff grid.
 *
 * Both labels are complete backend display strings and remain visible for the
 * lifetime of this header; the component owns no row or view state.
 */
function SplitHeader(props: { leftLabel: string; rightLabel: string }) {
  return (
    <div class="diff-header-row">
      <div class="diff-pane-header diff-side-header">{props.leftLabel}</div>
      <div class="diff-pane-header diff-side-header">{props.rightLabel}</div>
    </div>
  );
}

/**
 * Renders the fixed old/new/code labels above an inline diff grid.
 *
 * Full backend labels remain available through the two line-column tooltips;
 * the component must not abbreviate or reinterpret them elsewhere.
 */
function InlineHeader(props: { leftLabel: string; rightLabel: string }) {
  return (
    <div class="diff-header-row inline-header-row">
      <div class="diff-pane-header inline-line-header" title={props.leftLabel}>
        old
      </div>
      <div class="diff-pane-header inline-line-header" title={props.rightLabel}>
        new
      </div>
      <div class="diff-pane-header">Code</div>
    </div>
  );
}

/**
 * Extends a validated backend row with its render-only hunk-boundary flag.
 *
 * The flag is always present after `markHunkAnchors` projects raw rows. It is
 * never backend data and must not become selected-hunk state.
 */
export type HunkDiffRow = DiffRow & {
  isHunkAnchor: boolean;
};

/**
 * Represents a row accepted by the hunk-aware imperative rendering kernel.
 *
 * Members are either projected backend rows or FoldRows. Callers discriminate
 * folds by status and retain hunk flags only on the ordinary-row variant.
 */
type HunkRenderRow = RenderRow<HunkDiffRow>;

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

/**
 * Synchronizes reactive DiffGrid inputs into one exclusively owned DOM root.
 *
 * The Solid effect runs initially and whenever rows, labels, view, file name,
 * or fold policies change. It clears local expanded-fold state when any such
 * renderer input changes, replaces every child of its root atomically, and
 * needs no disposal callback because the component owns no external listener
 * or resource beyond listeners attached to that replaced or disposed DOM.
 */
function ImperativeDiffLines(props: {
  fileIndex: number;
  displayName: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  leftLabel: string;
  rightLabel: string;
  viewMode: DiffViewMode;
  aggressiveFolds: boolean;
  collapseInsertOnlyReplaceRows: boolean;
}) {
  let root!: HTMLDivElement;
  const expandedFolds = new Set<number>();
  let previousDisplayName: string | undefined;
  let previousRows: DiffRow[] | undefined;
  let previousFoldHints: FoldHint[] | undefined;
  let previousLeftLabel: string | undefined;
  let previousRightLabel: string | undefined;
  let previousViewMode: DiffViewMode | undefined;
  let previousAggressiveFolds: boolean | undefined;
  let previousCollapseInsertOnlyReplaceRows: boolean | undefined;

  /**
   * Rebuilds the complete owned row subtree from current reactive inputs.
   *
   * The function resets local fold expansion only when an identity-bearing
   * renderer input changes and atomically replaces every child of `root`.
   */
  const render = () => {
    const inputChanged = [
      props.displayName !== previousDisplayName,
      props.rows !== previousRows,
      props.foldHints !== previousFoldHints,
      props.leftLabel !== previousLeftLabel,
      props.rightLabel !== previousRightLabel,
      props.viewMode !== previousViewMode,
      props.aggressiveFolds !== previousAggressiveFolds,
      props.collapseInsertOnlyReplaceRows !==
        previousCollapseInsertOnlyReplaceRows,
    ].some(Boolean);
    if (inputChanged) {
      expandedFolds.clear();
      previousDisplayName = props.displayName;
      previousRows = props.rows;
      previousFoldHints = props.foldHints;
      previousLeftLabel = props.leftLabel;
      previousRightLabel = props.rightLabel;
      previousViewMode = props.viewMode;
      previousAggressiveFolds = props.aggressiveFolds;
      previousCollapseInsertOnlyReplaceRows =
        props.collapseInsertOnlyReplaceRows;
    }

    const rows = addFoldRows(
      markHunkAnchors(props.rows),
      props.foldHints,
      props.aggressiveFolds,
    );
    const fileLabel = props.displayName;
    const fragment =
      props.viewMode === "inline" &&
      props.collapseInsertOnlyReplaceRows === true
        ? renderCollapsedInlineRowsDom(
            rows,
            fileLabel,
            expandedFolds,
            props.fileIndex,
            0,
          )
        : props.viewMode === "inline"
          ? renderInlineRowsDom(
              rows,
              fileLabel,
              expandedFolds,
              props.fileIndex,
              0,
            )
          : renderSplitRowsDom(
              rows,
              fileLabel,
              props.leftLabel,
              props.rightLabel,
              expandedFolds,
              props.fileIndex,
              0,
            );
    root.replaceChildren(fragment);
  };

  createEffect(render);

  return <div ref={root} class="diff-lines" />;
}

/**
 * Renders an ordered projected row range into split-view DOM.
 *
 * `startRow` is the required source-row offset for stable row identity. Folded
 * ranges advance by their represented count while ordinary rows advance once.
 */
function renderSplitRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderSplitFoldDom(
          row,
          rowIndex,
          fileLabel,
          leftLabel,
          rightLabel,
          expandedFolds,
          fileIndex,
        ),
      );
      cursor += row.count;
    } else {
      fragment.append(
        renderSplitDiffRowDom(row, rowIndex, fileLabel, fileIndex),
      );
      cursor += 1;
    }
  });
  return fragment;
}

/**
 * Renders an ordered projected row range into ordinary inline-view DOM.
 *
 * `startRow` is the required source-row offset. One backend row may emit two
 * visible rows, while shared line-number state suppresses duplicate numbers.
 */
function renderInlineRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const lineNumberState: InlineLineNumberState = {
    leftNo: null,
    rightNo: null,
  };
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderInlineFoldDom(
          row,
          rowIndex,
          fileLabel,
          expandedFolds,
          fileIndex,
          false,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      fragment.append(
        renderInlineDiffRowsDom(
          row,
          rowIndex,
          fileLabel,
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
 * Renders inline rows with the engine-specific insert-only replacement collapse.
 *
 * The required source-row offset and fold set retain the same identity rules as
 * ordinary inline rendering; rows outside the narrow collapse predicate remain
 * byte-for-byte equivalent in presentation structure.
 */
function renderCollapsedInlineRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  startRow: number,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  const lineNumberState: InlineLineNumberState = {
    leftNo: null,
    rightNo: null,
  };
  let cursor = startRow;
  rows.forEach((row) => {
    const rowIndex = isFoldRow(row) ? row.startRow : cursor;
    if (isFoldRow(row)) {
      fragment.append(
        renderInlineFoldDom(
          row,
          rowIndex,
          fileLabel,
          expandedFolds,
          fileIndex,
          true,
        ),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      fragment.append(
        renderCollapsedInlineDiffRowsDom(
          row,
          rowIndex,
          fileLabel,
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
 * wrapper's children and preserves hidden hunk anchors for later DOM navigation.
 */
function renderSplitFoldDom(
  row: FoldRow<HunkDiffRow>,
  rowIndex: number,
  fileLabel: string,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
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
    renderFold();
  };

  /**
   * Replaces this split fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows preserve source offsets and receive one collapse affordance;
   * folded rows retain hidden hunk identity anchors.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderSplitRowsDom(
        row.foldedRows,
        fileLabel,
        leftLabel,
        rightLabel,
        expandedFolds,
        fileIndex,
        row.startRow,
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
    wrapper.replaceChildren(
      ...hunkSkipAnchors(row.foldedRows, fileIndex),
      button,
    );
  };

  renderFold();
  return wrapper;
}

/**
 * Creates one stateful inline-view fold subtree around an immutable FoldRow.
 *
 * The required collapse policy is propagated into expanded nested rows. The
 * wrapper owns only its current DOM children and local toggle listeners.
 */
function renderInlineFoldDom(
  row: FoldRow<HunkDiffRow>,
  rowIndex: number,
  fileLabel: string,
  expandedFolds: Set<number>,
  fileIndex: number,
  collapseInsertOnlyReplaceRows: boolean,
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
    renderFold();
  };

  /**
   * Replaces this inline fold wrapper with its bar or complete expanded rows.
   *
   * Expanded rows retain the required collapse policy and source offsets;
   * folded rows retain hidden hunk identity anchors.
   */
  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const rows = row.foldedRows;
      const fragment =
        collapseInsertOnlyReplaceRows === true
          ? renderCollapsedInlineRowsDom(
              rows,
              fileLabel,
              expandedFolds,
              fileIndex,
              row.startRow,
            )
          : renderInlineRowsDom(
              rows,
              fileLabel,
              expandedFolds,
              fileIndex,
              row.startRow,
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
    button.append(
      createPlainLineNumberDom(".."),
      createPlainLineNumberDom(".."),
      createElementWithClass(
        "div",
        "fold-label inline-fold-label",
        foldLabel(row),
      ),
    );
    wrapper.replaceChildren(
      ...hunkSkipAnchors(row.foldedRows, fileIndex),
      button,
    );
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
 * Attaches the collapse affordance to the first visible row of an expanded fold.
 *
 * An empty fragment is accepted and produces no affordance. The listener is
 * owned by the supplied fragment and disappears when its DOM is replaced.
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
  row.title = "Collapse folded rows";
  row.addEventListener("click", onToggle);

  const lineNumber = row.querySelector(".line-no");
  if (lineNumber instanceof HTMLElement) {
    lineNumber.prepend(createFoldToggleButtonDom({ expanded: true, onToggle }));
  }
}

/**
 * Materializes hidden anchors for hunks contained by a collapsed fold subtree.
 *
 * The returned elements preserve backend `(fileIndex, hunkIndex)` identity but
 * contain no visible code and are marked aria-hidden.
 */
function hunkSkipAnchors(
  rows: HunkRenderRow[],
  fileIndex: number,
): HTMLElement[] {
  return rows.flatMap((row) => {
    if (isFoldRow(row)) {
      return hunkSkipAnchors(row.foldedRows, fileIndex);
    }
    if (row.isHunkAnchor) {
      const anchor = document.createElement("span");
      anchor.className = "diff-row hunk-anchor hunk-skip";
      applyHunkIdentity(anchor, row, fileIndex);
      anchor.setAttribute("aria-hidden", "true");
      return [anchor];
    }
    return [];
  });
}

/**
 * Renders one ordinary backend row as a two-sided split-view element.
 *
 * The required source index and file index become stable DOM identity. Fold
 * disclosure is attached separately to the first expanded row.
 */
function renderSplitDiffRowDom(
  row: HunkDiffRow,
  rowIndex: number,
  fileLabel: string,
  fileIndex: number,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, "");
  element.dataset.rowIndex = String(rowIndex);
  applyHunkIdentity(element, row, fileIndex);
  element.append(
    createDiffSideDom(row, "left", fileLabel),
    createDiffSideDom(row, "right", fileLabel),
  );
  return element;
}

/**
 * Projects one backend row into the one or two elements required by inline view.
 *
 * Equal, insert, and delete rows emit one element; replace and move rows may
 * emit both sides while transferring hunk identity to the first visible side.
 * Unsupported backend statuses throw exhaustively.
 */
function renderInlineDiffRowsDom(
  row: HunkDiffRow,
  rowIndex: number,
  fileLabel: string,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  const rightText = sideText(row, "right");
  const leftText = sideText(row, "left");
  const sharedText = sharedSideText(leftText, rightText);
  const sharedTokens = sharedSideTokens(row);
  const sharedSyntax = sharedSideSyntax(row);

  switch (row.status) {
    case "equal":
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: row.left_no,
        rightNo: row.right_no,
        text: sharedText,
        tokens: sharedTokens,
        syntax: sharedSyntax,
        rowIndex,
        fileLabel,
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
        tokens: row.left_tokens,
        syntax: row.left_syntax,
        rowIndex,
        fileLabel,
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
        tokens: row.right_tokens,
        syntax: row.right_syntax,
        rowIndex,
        fileLabel,
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
            tokens: row.left_tokens,
            syntax: row.left_syntax,
            rowIndex,
            fileLabel,
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
            tokens: row.right_tokens,
            syntax: row.right_syntax,
            rowIndex,
            fileLabel,
            fileIndex,
            sourceRow: { ...row, isHunkAnchor: !hasLeftSide },
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
            tokens: row.left_tokens,
            syntax: row.left_syntax,
            rowIndex,
            fileLabel,
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
            tokens: row.right_tokens,
            syntax: row.right_syntax,
            rowIndex,
            fileLabel,
            fileIndex,
            sourceRow: { ...row, isHunkAnchor: !hasLeftSide },
            lineNumberState,
            tokenRowStatus: null,
          }),
        );
      }
      return fragment;
    }
    default:
      throwUnhandledRowStatus(row.status);
  }
}

/**
 * Chooses the side text used for a shared inline equal row.
 *
 * Non-empty right text wins because it represents the resulting side; otherwise
 * the left text is returned unchanged.
 */
function sharedSideText(leftText: string, rightText: string): string {
  if (rightText.length > 0) {
    return rightText;
  }
  return leftText;
}

/**
 * Chooses the token list used for a shared inline equal row.
 *
 * Non-empty right-side tokens win; otherwise the exact left-side array is
 * returned without copying or mutation.
 */
function sharedSideTokens(row: DiffRow): InlineToken[] {
  if (row.right_tokens.length > 0) {
    return row.right_tokens;
  }
  return row.left_tokens;
}

/**
 * Chooses the syntax spans used for a shared inline equal row.
 *
 * Non-empty right-side syntax wins; otherwise the exact left-side array is
 * returned without copying or mutation.
 */
function sharedSideSyntax(row: DiffRow): SyntaxSpan[] {
  if (row.right_syntax.length > 0) {
    return row.right_syntax;
  }
  return row.left_syntax;
}

/**
 * Reports whether one inline side has a line identity or visible text.
 *
 * Null plus empty text is the only absent-side representation; zero and an
 * empty line with a number remain present.
 */
function inlineSideExists(lineNo: number | null, text: string): boolean {
  return lineNo !== null || text.length > 0;
}

/**
 * Renders one inline row with the narrow insert-only replacement optimization.
 *
 * Rows failing the exact collapse predicate delegate to ordinary inline
 * rendering. Collapsed rows retain both backend line numbers and hunk identity.
 */
function renderCollapsedInlineDiffRowsDom(
  row: HunkDiffRow,
  rowIndex: number,
  fileLabel: string,
  fileIndex: number,
  lineNumberState: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  if (!canCollapseInsertOnlyReplaceRow(row)) {
    return renderInlineDiffRowsDom(
      row,
      rowIndex,
      fileLabel,
      fileIndex,
      lineNumberState,
    );
  }

  const rightText = sideText(row, "right");
  return renderInlineDiffRowDom({
    status: "replace",
    marker: " ",
    leftNo: row.left_no,
    rightNo: row.right_no,
    text: rightText,
    tokens: row.right_tokens,
    syntax: row.right_syntax,
    rowIndex,
    fileLabel,
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
function canCollapseInsertOnlyReplaceRow(row: DiffRow): boolean {
  if (row.status !== "replace") {
    return false;
  }
  const leftTokens = row.left_tokens;
  const rightTokens = row.right_tokens;
  const oldSideHasChanges = leftTokens.some(
    (token) => token.status !== "unchanged",
  );
  const rightChangedStatuses = rightTokens
    .filter((token) => token.status !== "unchanged")
    .map((token) => token.status);
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
  tokens: InlineToken[];
  syntax: SyntaxSpan[];
  rowIndex: number;
  fileLabel: string;
  fileIndex: number;
  sourceRow: HunkDiffRow;
  lineNumberState: InlineLineNumberState;
  tokenRowStatus: InlineRowStatus | null;
}): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(
    props.status,
    props.sourceRow,
    "inline-diff-row",
  );
  element.dataset.rowIndex = String(props.rowIndex);
  applyHunkIdentity(element, props.sourceRow, props.fileIndex);
  element.append(
    createLineNumberDom(
      inlineDisplayLineNo(props.leftNo, "left", props.lineNumberState),
      "left",
      props.fileLabel,
      props.leftNo,
    ),
    createLineNumberDom(
      inlineDisplayLineNo(props.rightNo, "right", props.lineNumberState),
      "right",
      props.fileLabel,
      props.rightNo,
    ),
    createInlineLineCodeDom(
      props.marker,
      props.text,
      props.tokens,
      props.syntax,
      inlineTokenRowStatus(props),
    ),
  );
  return element;
}

/**
 * Resolves the status used only for token-decoration suppression.
 *
 * A provided override wins, including every valid InlineRowStatus. Undefined
 * means the row's visible status is the complete contract.
 */
function inlineTokenRowStatus(props: {
  status: InlineRowStatus;
  tokenRowStatus: InlineRowStatus | null;
}): InlineRowStatus {
  if (props.tokenRowStatus === null) {
    return props.status;
  }
  return props.tokenRowStatus;
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
  row: HunkDiffRow,
  extraClass: string,
): string {
  const classes = ["diff-row"];
  if (extraClass.length > 0) {
    classes.push(extraClass);
  }
  classes.push(status);
  if (row.isHunkAnchor === true) {
    classes.push("hunk-anchor");
  }
  if (isChangedRowStatus(status) && isWhitespaceOnlyChange(row)) {
    classes.push("whitespace-only-change");
  }
  return classes.join(" ");
}

/**
 * Attach the backend-provided snapshot-local identity to one anchor element.
 *
 * Rendering modes may replace the element, but every replacement must expose
 * the same `(fileIndex, hunkIndex)` pair. Non-anchor rows are left untouched;
 * an anchor without a backend hunk index is an invalid file-diff payload.
 */
function applyHunkIdentity(
  element: HTMLElement,
  row: HunkDiffRow,
  fileIndex: number,
): void {
  if (row.isHunkAnchor !== true) {
    return;
  }
  if (row.hunk_index === null) {
    throw new Error("Hunk anchor is missing its backend hunk index.");
  }
  element.dataset.fileIndex = String(fileIndex);
  element.dataset.hunkIndex = String(row.hunk_index);
}

/**
 * Creates one split-view side with line identity and decorated code content.
 *
 * Callers supply a validated backend row and exact side. Null line numbers and
 * empty text produce the established empty-side treatment rather than a husk.
 */
function createDiffSideDom(
  row: HunkDiffRow,
  side: Side,
  fileLabel: string,
): HTMLElement {
  const lineNo = side === "left" ? row.left_no : row.right_no;
  const text = sideText(row, side);
  const tokens = side === "left" ? row.left_tokens : row.right_tokens;
  const syntax = side === "left" ? row.left_syntax : row.right_syntax;
  const element = document.createElement("div");
  element.className = `diff-side side-${side}${
    lineNo === null && text === "" ? " empty-side" : ""
  }`;
  element.append(
    createLineNumberDom(lineNo, side, fileLabel, lineNo),
    createLineCodeDom(text, tokens, syntax, row.status),
  );
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
 * Enforces exhaustive handling of the backend RowStatus union.
 *
 * Callers may invoke this only from an exhaustive switch; a runtime value that
 * reaches it is a backend or schema contract violation and throws visibly.
 */
function throwUnhandledRowStatus(status: never): never {
  throw new Error(`Unhandled diff row status: ${String(status)}.`);
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
 * Formats the complete visible inline fold label from one FoldRow.
 *
 * Empty backend labels omit the `in …` suffix while the represented row count
 * always remains visible.
 */
function foldLabel(row: FoldRow): string {
  const lineText = foldLineText(row.count);
  if (row.label.length > 0) {
    return `... ${lineText} in ${row.label}`;
  }
  return `... ${lineText}`;
}

/**
 * Creates one line-number cell and its required pin identity input.
 *
 * Null is the required absent-line value. `pinLineNo` may intentionally differ
 * from the displayed number for duplicate-suppressed inline rows. Callers pass
 * null explicitly when no pinnable backend line exists.
 */
function createLineNumberDom(
  lineNo: number | null,
  side: Side,
  fileLabel: string,
  pinLineNo: number | null,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "line-no";
  if (pinLineNo !== null) {
    element.dataset.linePinFile = fileLabel;
    element.dataset.linePinSide = side;
    element.dataset.linePinLine = String(pinLineNo);
    element.title = "Pin line";
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
  button.ariaLabel = foldToggle.expanded ? "Collapse fold" : "Expand fold";
  button.textContent = foldToggle.expanded ? "▾" : "▸";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    foldToggle.onToggle();
  });
  return button;
}

/**
 * Creates one split-view code cell with syntax and inline-token decoration.
 *
 * Callers supply aligned backend spans and the required row status for the exact
 * text. Status suppresses redundant token emphasis expressed by the row color.
 */
function createLineCodeDom(
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus: RowStatus,
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code";
  appendDecoratedText(element, text, tokens, syntax, rowStatus);
  return element;
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
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus: InlineRowStatus,
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code inline-line-code";
  const markerElement = document.createElement("span");
  markerElement.className = "inline-marker";
  markerElement.ariaHidden = "true";
  markerElement.textContent = marker;
  element.append(markerElement);
  const inlineTokenRowStatus = inlineRowTokenStatus(rowStatus);
  appendDecoratedText(element, text, tokens, syntax, inlineTokenRowStatus);
  return element;
}

/**
 * Maps inline insert/delete rows to replacement token semantics.
 *
 * Equal, move, and replace status values pass through unchanged; this affects
 * only token emphasis, not row classes or identity.
 */
function inlineRowTokenStatus(rowStatus: InlineRowStatus): RowStatus {
  return rowStatus === "insert" || rowStatus === "delete"
    ? "replace"
    : rowStatus;
}

/**
 * Appends native-searchable text segmented by syntax and inline-token spans.
 *
 * Callers provide spans aligned to the same text. The function preserves every
 * character, suppresses redundant token coloring, and mutates only `element`.
 */
function appendDecoratedText(
  element: HTMLElement,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus: RowStatus,
) {
  const parts = decoratedParts(text, tokens, syntax);
  if (parts.length === 0) {
    element.append(text);
    return;
  }
  for (const part of parts) {
    const tokenChanged = part.status !== "unchanged";
    if (part.classes.length === 0 && !tokenChanged) {
      element.append(part.text);
      continue;
    }
    const span = document.createElement("span");
    const classes = [...part.classes];
    const rowAlreadyShowsTokenChange = rowShowsTokenChange(
      rowStatus,
      part.status,
    );
    const showTokenChange = tokenChanged && !rowAlreadyShowsTokenChange;
    if (showTokenChange) {
      classes.push("token-changed", `token-${part.status}`);
    }
    if (showTokenChange && part.isWhitespace) {
      classes.push("whitespace");
    }
    if (showTokenChange && part.isWhitespace && part.leading) {
      classes.push("whitespace-leading");
    }
    span.className = classes.join(" ");
    if (showTokenChange && part.isWhitespace) {
      span.title = "Whitespace changed";
    }
    span.textContent = part.text;
    element.append(span);
  }
}

/**
 * Reports whether the row background already communicates one token change.
 *
 * Only insert-on-insert and delete-on-delete pairs suppress token-level color;
 * all other changed tokens retain explicit emphasis.
 */
function rowShowsTokenChange(
  rowStatus: RowStatus,
  tokenStatus: InlineToken["status"],
): boolean {
  return (
    (rowStatus === "insert" || rowStatus === "delete") &&
    rowStatus === tokenStatus
  );
}

/**
 * Creates one typed HTML element with complete class and text content.
 *
 * The tag must be a standard HTMLElement tag. The returned element has no
 * event listeners, data attributes, or implicit application ownership.
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

/**
 * Recognizes statuses that may carry whitespace-only change treatment.
 *
 * Unknown strings return false; backend exhaustiveness is enforced by the row
 * render switch rather than this CSS-only predicate.
 */
function isChangedRowStatus(status: string): boolean {
  return ["replace", "insert", "delete", "move"].includes(status);
}

/**
 * Project backend hunk markers into the render-only anchor flag.
 *
 * This function does not discover hunk boundaries. Every non-null
 * `hunk_index` came from the file-diff response and survives folding and view
 * replacement as the identity rendered on the resulting anchor.
 */
export function markHunkAnchors(rows: DiffRow[]): HunkDiffRow[] {
  return rows.map((row) => ({
    ...row,
    isHunkAnchor: row.hunk_index !== null,
  }));
}

/**
 * Reports whether every changed inline token in a row contains whitespace.
 *
 * At least one changed token is required. Unchanged tokens do not affect the
 * result, and the backend token arrays are never mutated.
 */
function isWhitespaceOnlyChange(row: DiffRow): boolean {
  const leftTokens = row.left_tokens;
  const rightTokens = row.right_tokens;
  const changedTokens = [...leftTokens, ...rightTokens].filter(
    (token) => token.status !== "unchanged",
  );
  return (
    changedTokens.length > 0 && changedTokens.every((token) => token.is_ws)
  );
}

/**
 * Represents one inline token's absolute interval within its row text.
 *
 * Offsets are derived from the ordered token text, `leading` identifies only a
 * first whitespace token, and the shape does not include syntax classes.
 */
type TokenPart = {
  start: number;
  end: number;
  status: InlineToken["status"];
  isWhitespace: boolean;
  leading: boolean;
};

/**
 * Converts ordered inline tokens into contiguous absolute text intervals.
 *
 * Callers must supply tokens in backend text order. The function derives
 * offsets without mutating tokens and preserves each status and whitespace bit.
 */
function tokenParts(tokens: InlineToken[]): TokenPart[] {
  let cursor = 0;
  return tokens.map((token, index) => {
    const start = cursor;
    const end = start + token.text.length;
    cursor = end;
    return {
      start,
      end,
      status: token.status,
      isWhitespace: token.is_ws,
      leading: token.is_ws && index === 0,
    };
  });
}

/**
 * Intersects token and syntax intervals into the exact visible text segments.
 *
 * Callers supply spans for the same text. Bounds are clamped defensively,
 * suppressed syntax classes are removed, and every returned part preserves its
 * original substring and applicable change metadata.
 */
function decoratedParts(
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
) {
  if (!text || (!tokens.length && !syntax.length)) {
    return [];
  }

  const tokenNodes = tokenParts(tokens);
  const boundaries = new Set([0, text.length]);
  for (const token of tokenNodes) {
    boundaries.add(clamp(token.start, 0, text.length));
    boundaries.add(clamp(token.end, 0, text.length));
  }
  for (const span of syntax) {
    boundaries.add(clamp(span.start, 0, text.length));
    boundaries.add(clamp(span.end, 0, text.length));
  }

  const sortedBoundaries = [...boundaries].sort((left, right) => left - right);
  const parts: Array<{
    text: string;
    classes: string[];
    status: InlineToken["status"];
    isWhitespace: boolean;
    leading: boolean;
  }> = [];

  for (let index = 0; index < sortedBoundaries.length - 1; index += 1) {
    const start = sortedBoundaries[index];
    const end = sortedBoundaries[index + 1];
    if (end <= start) {
      continue;
    }
    const token = tokenNodes.find(
      (candidate) => start >= candidate.start && end <= candidate.end,
    );
    const syntaxClasses = syntax
      .filter((span) => start >= span.start && end <= span.end)
      .flatMap((span) => visibleSyntaxClasses(span.classes));
    const classes = syntaxClasses.length
      ? ["ts-token", ...new Set(syntaxClasses)]
      : [];
    const status = token === undefined ? "unchanged" : token.status;
    const isWhitespace =
      token === undefined
        ? /^\s+$/.test(text.slice(start, end))
        : token.isWhitespace;
    const leading = token === undefined ? false : token.leading;
    parts.push({
      text: text.slice(start, end),
      classes,
      status,
      isWhitespace,
      leading,
    });
  }
  return parts;
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

/**
 * Recognizes tree-sitter syntax classes hidden by the established diff theme.
 *
 * Exact prefix names and their hyphenated descendants are suppressed; unrelated
 * class names remain visible.
 */
function isSuppressedSyntaxClass(className: string): boolean {
  return suppressedSyntaxClassPrefixes.some(
    (prefix) => className === prefix || className.startsWith(`${prefix}-`),
  );
}
