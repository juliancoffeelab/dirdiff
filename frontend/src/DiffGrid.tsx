import { createEffect } from "solid-js";
import type {
  DiffRow,
  FoldHint,
  InlineToken,
  RowStatus,
  SyntaxSpan,
} from "./api";
import { addFoldRows, isFoldRow, type FoldRow, type RenderRow } from "./folds";

const suppressedSyntaxClassPrefixes = [
  "ts-punctuation",
  "ts-operator",
  "ts-variable",
  "ts-parameter",
  "ts-field",
  "ts-local",
];

type Side = "left" | "right";
type InlineMarker = " " | "-" | "+";
type InlineRowStatus = "equal" | "delete" | "insert" | "replace";

export type DiffViewMode = "split" | "inline";

export function DiffGrid(props: {
  displayName: string;
  leftLabel: string;
  rightLabel: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  viewMode: DiffViewMode;
  semanticReplaceRows?: boolean;
}) {
  return (
    <div
      class="diff-grid"
      classList={{
        "diff-grid-inline": props.viewMode === "inline",
        "diff-grid-semantic-replace": Boolean(props.semanticReplaceRows),
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
        displayName={props.displayName}
        rows={props.rows}
        foldHints={props.foldHints}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        viewMode={props.viewMode}
        semanticReplaceRows={props.semanticReplaceRows}
      />
    </div>
  );
}

function SplitHeader(props: { leftLabel: string; rightLabel: string }) {
  return (
    <div class="diff-header-row">
      <div class="diff-pane-header diff-side-header">{props.leftLabel}</div>
      <div class="diff-pane-header diff-side-header">{props.rightLabel}</div>
    </div>
  );
}

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

type HunkRenderRow = RenderRow & {
  isHunkAnchor?: boolean;
};

type InlineLineNumberState = {
  leftNo: number | null;
  rightNo: number | null;
};

function ImperativeDiffLines(props: {
  displayName: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  leftLabel: string;
  rightLabel: string;
  viewMode: DiffViewMode;
  semanticReplaceRows?: boolean;
}) {
  let root!: HTMLDivElement;
  const expandedFolds = new Set<number>();
  let previousDisplayName: string | undefined;
  let previousRows: DiffRow[] | undefined;
  let previousFoldHints: FoldHint[] | undefined;
  let previousLeftLabel: string | undefined;
  let previousRightLabel: string | undefined;
  let previousViewMode: DiffViewMode | undefined;
  let previousSemanticReplaceRows: boolean | undefined;

  const render = () => {
    if (
      props.displayName !== previousDisplayName ||
      props.rows !== previousRows ||
      props.foldHints !== previousFoldHints ||
      props.leftLabel !== previousLeftLabel ||
      props.rightLabel !== previousRightLabel ||
      props.viewMode !== previousViewMode ||
      props.semanticReplaceRows !== previousSemanticReplaceRows
    ) {
      expandedFolds.clear();
      previousDisplayName = props.displayName;
      previousRows = props.rows;
      previousFoldHints = props.foldHints;
      previousLeftLabel = props.leftLabel;
      previousRightLabel = props.rightLabel;
      previousViewMode = props.viewMode;
      previousSemanticReplaceRows = props.semanticReplaceRows;
    }

    const rows = markHunkAnchors(addFoldRows(props.rows, props.foldHints));
    const fileLabel = props.displayName;
    const fragment =
      props.viewMode === "inline" && props.semanticReplaceRows === true
        ? renderSemanticInlineRowsDom(rows, fileLabel, expandedFolds)
        : props.viewMode === "inline"
          ? renderInlineRowsDom(rows, fileLabel, expandedFolds)
          : renderSplitRowsDom(
              rows,
              fileLabel,
              props.leftLabel,
              props.rightLabel,
              expandedFolds,
            );
    root.replaceChildren(fragment);
  };

  createEffect(render);

  return <div ref={root} class="diff-lines" />;
}

function renderSplitRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
  startRow = 0,
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
        ),
      );
      cursor += row.count;
    } else if (row.status === "elided") {
      fragment.append(renderSplitElidedRowDom(row, leftLabel, rightLabel));
      cursor += 1;
    } else {
      fragment.append(renderSplitDiffRowDom(row, rowIndex, fileLabel));
      cursor += 1;
    }
  });
  return fragment;
}

function renderInlineRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  expandedFolds: Set<number>,
  startRow = 0,
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
        renderInlineFoldDom(row, rowIndex, fileLabel, expandedFolds),
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
          undefined,
          lineNumberState,
        ),
      );
      cursor += 1;
    }
  });
  return fragment;
}

function renderSemanticInlineRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  expandedFolds: Set<number>,
  startRow = 0,
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
        renderInlineFoldDom(row, rowIndex, fileLabel, expandedFolds, true),
      );
      lineNumberState.leftNo = null;
      lineNumberState.rightNo = null;
      cursor += row.count;
    } else {
      fragment.append(
        renderSemanticInlineDiffRowsDom(
          row,
          rowIndex,
          fileLabel,
          undefined,
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
  fileLabel: string,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    renderFold();
  };

  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const fragment = renderSplitRowsDom(
        markHunkAnchors(row.foldedRows),
        fileLabel,
        leftLabel,
        rightLabel,
        expandedFolds,
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
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

function renderInlineFoldDom(
  row: FoldRow,
  rowIndex: number,
  fileLabel: string,
  expandedFolds: Set<number>,
  semanticReplaceRows = false,
): HTMLElement {
  const wrapper = document.createElement("div");
  wrapper.style.display = "contents";

  const toggle = () => {
    if (expandedFolds.has(rowIndex)) {
      expandedFolds.delete(rowIndex);
    } else {
      expandedFolds.add(rowIndex);
    }
    renderFold();
  };

  const renderFold = () => {
    const expanded = expandedFolds.has(rowIndex);
    if (expanded) {
      const rows = markHunkAnchors(row.foldedRows);
      const fragment =
        semanticReplaceRows === true
          ? renderSemanticInlineRowsDom(
              rows,
              fileLabel,
              expandedFolds,
              row.startRow,
            )
          : renderInlineRowsDom(rows, fileLabel, expandedFolds, row.startRow);
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
    wrapper.replaceChildren(button);
  };

  renderFold();
  return wrapper;
}

type FoldToggle = { expanded: boolean; onToggle: () => void };

function attachExpandedFoldToggle(
  fragment: DocumentFragment,
  onToggle: () => void,
) {
  const row = fragment.querySelector(
    ".diff-row:not(.fold-bar):not(.inline-fold-bar):not(.elided)",
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

function renderSplitDiffRowDom(
  row: DiffRow & { isHunkAnchor?: boolean },
  rowIndex: number,
  fileLabel: string,
  foldToggle?: FoldToggle,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, foldToggle);
  element.dataset.rowIndex = String(rowIndex);
  if (foldToggle !== undefined) {
    element.title = "Collapse folded rows";
    element.addEventListener("click", foldToggle.onToggle);
  }
  element.append(
    createDiffSideDom(row, "left", fileLabel, foldToggle),
    createDiffSideDom(row, "right", fileLabel, foldToggle),
  );
  return element;
}

function renderInlineDiffRowsDom(
  row: DiffRow & { isHunkAnchor?: boolean },
  rowIndex: number,
  fileLabel: string,
  foldToggle?: FoldToggle,
  lineNumberState?: InlineLineNumberState,
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
        sourceRow: row,
        foldToggle,
        lineNumberState,
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
        sourceRow: row,
        foldToggle,
        lineNumberState,
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
        sourceRow: row,
        foldToggle,
        lineNumberState,
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
            sourceRow: row,
            foldToggle,
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
            sourceRow: { ...row, isHunkAnchor: !hasLeftSide },
            foldToggle: hasLeftSide ? undefined : foldToggle,
            lineNumberState,
            tokenRowStatus: "replace",
          }),
        );
      }
      return fragment;
    }
    case "elided":
      return renderInlineDiffRowDom({
        status: "equal",
        marker: " ",
        leftNo: null,
        rightNo: null,
        text: elidedLabel(row),
        tokens: [],
        syntax: [],
        rowIndex,
        fileLabel,
        sourceRow: row,
        foldToggle,
        lineNumberState,
      });
    case "fold":
      throw new Error("Fold rows must be rendered by the fold renderer.");
    default:
      throwUnhandledRowStatus(row.status);
  }
}

function sharedSideText(leftText: string, rightText: string): string {
  if (rightText.length > 0) {
    return rightText;
  }
  return leftText;
}

function sharedSideTokens(row: DiffRow): InlineToken[] {
  if (row.right_tokens.length > 0) {
    return row.right_tokens;
  }
  return row.left_tokens;
}

function sharedSideSyntax(row: DiffRow): SyntaxSpan[] {
  if (row.right_syntax.length > 0) {
    return row.right_syntax;
  }
  return row.left_syntax;
}

function inlineSideExists(lineNo: number | null, text: string): boolean {
  return lineNo !== null || text.length > 0;
}

function renderSemanticInlineDiffRowsDom(
  row: DiffRow & { isHunkAnchor?: boolean },
  rowIndex: number,
  fileLabel: string,
  foldToggle?: FoldToggle,
  lineNumberState?: InlineLineNumberState,
): DocumentFragment | HTMLElement {
  if (!canCollapseSemanticInlineRow(row)) {
    return renderInlineDiffRowsDom(
      row,
      rowIndex,
      fileLabel,
      foldToggle,
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
    sourceRow: row,
    foldToggle,
    lineNumberState,
  });
}

function canCollapseSemanticInlineRow(row: DiffRow): boolean {
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
  sourceRow: DiffRow & { isHunkAnchor?: boolean };
  foldToggle?: FoldToggle;
  lineNumberState?: InlineLineNumberState;
  tokenRowStatus?: InlineRowStatus;
}): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(
    props.status,
    props.sourceRow,
    props.foldToggle,
    "inline-diff-row",
  );
  element.dataset.rowIndex = String(props.rowIndex);
  if (props.foldToggle !== undefined) {
    element.title = "Collapse folded rows";
    element.addEventListener("click", props.foldToggle.onToggle);
  }
  element.append(
    createLineNumberDom(
      inlineDisplayLineNo(props.leftNo, "left", props.lineNumberState),
      "left",
      props.fileLabel,
      props.foldToggle,
      props.leftNo,
    ),
    createLineNumberDom(
      inlineDisplayLineNo(props.rightNo, "right", props.lineNumberState),
      "right",
      props.fileLabel,
      undefined,
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

function inlineTokenRowStatus(props: {
  status: InlineRowStatus;
  tokenRowStatus?: InlineRowStatus;
}): InlineRowStatus {
  if (props.tokenRowStatus === undefined) {
    return props.status;
  }
  return props.tokenRowStatus;
}

function inlineDisplayLineNo(
  lineNo: number | null,
  side: Side,
  state?: InlineLineNumberState,
): number | null {
  if (state === undefined || lineNo === null) {
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
  row: DiffRow & { isHunkAnchor?: boolean },
  foldToggle?: FoldToggle,
  extraClass = "",
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
  if (foldToggle !== undefined) {
    classes.push("fold-toggle-row");
    if (foldToggle.expanded === true) {
      classes.push("fold-expanded");
    }
  }
  return classes.join(" ");
}

function createDiffSideDom(
  row: DiffRow,
  side: Side,
  fileLabel: string,
  foldToggle?: FoldToggle,
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
    createLineNumberDom(lineNo, side, fileLabel, foldToggle),
    createLineCodeDom(text, tokens, syntax, row.status),
  );
  return element;
}

function renderSplitElidedRowDom(
  row: DiffRow,
  leftLabel: string,
  rightLabel: string,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "diff-row elided";
  element.append(
    createFoldSideDom(
      requiredRowCount(row, "elided"),
      elidedLabel(row),
      leftLabel,
    ),
    createFoldSideDom(
      requiredRowCount(row, "elided"),
      elidedLabel(row),
      rightLabel,
    ),
  );
  return element;
}

function sideText(row: DiffRow, side: Side): string {
  const text = side === "left" ? row.left_text : row.right_text;
  if (text === null) {
    return "";
  }
  return text;
}

function requiredRowCount(row: DiffRow, status: string): number {
  if (typeof row.count !== "number") {
    throw new Error(`${status} row is missing count.`);
  }
  return row.count;
}

function elidedLabel(row: DiffRow): string {
  if (typeof row.label !== "string" || row.label.length === 0) {
    throw new Error("Elided row is missing label.");
  }
  return row.label;
}

function throwUnhandledRowStatus(status: never): never {
  throw new Error(`Unhandled diff row status: ${String(status)}.`);
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

function foldLabel(row: FoldRow): string {
  const lineText = foldLineText(row.count);
  if (row.label.length > 0) {
    return `... ${lineText} in ${row.label}`;
  }
  return `... ${lineText}`;
}

function createLineNumberDom(
  lineNo: number | null,
  side: Side,
  fileLabel: string,
  foldToggle?: FoldToggle,
  pinLineNo: number | null = lineNo,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "line-no";
  if (pinLineNo !== null) {
    element.dataset.linePinFile = fileLabel;
    element.dataset.linePinSide = side;
    element.dataset.linePinLine = String(pinLineNo);
    element.title = "Pin line";
  }
  if (foldToggle !== undefined) {
    element.append(createFoldToggleButtonDom(foldToggle));
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
  button.ariaLabel = foldToggle.expanded ? "Collapse fold" : "Expand fold";
  button.textContent = foldToggle.expanded ? "▾" : "▸";
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    foldToggle.onToggle();
  });
  return button;
}

function createLineCodeDom(
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus?: RowStatus,
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code";
  appendDecoratedText(element, text, tokens, syntax, rowStatus);
  return element;
}

function createInlineLineCodeDom(
  marker: InlineMarker,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus?: "equal" | "delete" | "insert" | "replace",
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
  appendDecoratedText(element, text, tokens, syntax, inlineTokenRowStatus);
  return element;
}

function appendDecoratedText(
  element: HTMLElement,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
  rowStatus?: RowStatus | "equal" | "delete" | "insert",
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
    const rowAlreadyShowsTokenChange =
      (rowStatus === "insert" && part.status === "insert") ||
      (rowStatus === "delete" && part.status === "delete");
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

function isChangedRowStatus(status: string): boolean {
  return status === "replace" || status === "insert" || status === "delete";
}

function markHunkAnchors(rows: RenderRow[]): HunkRenderRow[] {
  let previousChanged = false;
  return rows.map((row) => {
    if (isFoldRow(row)) {
      previousChanged = false;
      return row;
    }
    const changed = isChangedRowStatus(row.status);
    const isHunkAnchor = changed && !previousChanged;
    previousChanged = changed;
    return { ...row, isHunkAnchor };
  });
}

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

type TokenPart = {
  start: number;
  end: number;
  status: InlineToken["status"];
  isWhitespace: boolean;
  leading: boolean;
};

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

function visibleSyntaxClasses(classes: string[]): string[] {
  if (!classes.length || classes.every(isSuppressedSyntaxClass)) {
    return [];
  }
  return classes;
}

function isSuppressedSyntaxClass(className: string): boolean {
  return suppressedSyntaxClassPrefixes.some(
    (prefix) => className === prefix || className.startsWith(`${prefix}-`),
  );
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
