import { createEffect } from "solid-js";
import type { DiffRow, FileEntry, InlineToken, SyntaxSpan } from "./api";
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

export type DiffViewMode = "split" | "inline";

export function DiffGrid(props: { file: FileEntry; viewMode: DiffViewMode }) {
  return (
    <div
      class="diff-grid"
      classList={{ "diff-grid-inline": props.viewMode === "inline" }}
    >
      {props.viewMode === "inline" ? (
        <InlineHeader
          leftLabel={props.file.left_label || "left"}
          rightLabel={props.file.right_label || "right"}
        />
      ) : (
        <SplitHeader
          leftLabel={props.file.left_label || "left"}
          rightLabel={props.file.right_label || "right"}
        />
      )}
      <ImperativeDiffLines file={props.file} viewMode={props.viewMode} />
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
        theirs
      </div>
      <div class="diff-pane-header inline-line-header" title={props.rightLabel}>
        ours
      </div>
      <div class="diff-pane-header">Code</div>
    </div>
  );
}

type HunkRenderRow = RenderRow & {
  isHunkAnchor?: boolean;
};

function ImperativeDiffLines(props: {
  file: FileEntry;
  viewMode: DiffViewMode;
}) {
  let root!: HTMLDivElement;
  const expandedFolds = new Set<number>();
  let previousFile: FileEntry | undefined;
  let previousViewMode: DiffViewMode | undefined;

  const render = () => {
    if (props.file !== previousFile || props.viewMode !== previousViewMode) {
      expandedFolds.clear();
      previousFile = props.file;
      previousViewMode = props.viewMode;
    }

    const rows = markHunkAnchors(
      addFoldRows(props.file.rows ?? [], props.file.fold_hints),
    );
    const fileLabel = fileDisplayLabel(props.file);
    const fragment =
      props.viewMode === "inline"
        ? renderInlineRowsDom(rows, fileLabel, expandedFolds)
        : renderSplitRowsDom(
            rows,
            fileLabel,
            props.file.left_label || "left",
            props.file.right_label || "right",
            expandedFolds,
          );
    root.replaceChildren(fragment);
  };

  createEffect(render);

  return <div ref={root} class="diff-lines" />;
}

function fileDisplayLabel(file: FileEntry): string {
  return (
    file.display_name || file.right_path || file.left_path || "(unknown file)"
  );
}

function renderSplitRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  leftLabel: string,
  rightLabel: string,
  expandedFolds: Set<number>,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  rows.forEach((row, index) => {
    if (isFoldRow(row)) {
      fragment.append(
        renderSplitFoldDom(
          row,
          index,
          fileLabel,
          leftLabel,
          rightLabel,
          expandedFolds,
        ),
      );
      return;
    }
    fragment.append(renderSplitDiffRowDom(row, index, fileLabel));
  });
  return fragment;
}

function renderInlineRowsDom(
  rows: HunkRenderRow[],
  fileLabel: string,
  expandedFolds: Set<number>,
): DocumentFragment {
  const fragment = document.createDocumentFragment();
  rows.forEach((row, index) => {
    if (isFoldRow(row)) {
      fragment.append(
        renderInlineFoldDom(row, index, fileLabel, expandedFolds),
      );
      return;
    }
    fragment.append(renderInlineDiffRowsDom(row, index, fileLabel));
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
      const fragment = document.createDocumentFragment();
      const firstRow = row.foldedRows[0];
      if (firstRow) {
        fragment.append(
          renderSplitDiffRowDom(
            {
              ...firstRow,
              isHunkAnchor: isChangedRowStatus(firstRow.status),
            },
            rowIndex,
            fileLabel,
            { expanded: true, onToggle: toggle },
          ),
        );
      }
      row.foldedRows.slice(1).forEach((foldedRow, foldedIndex) => {
        fragment.append(
          renderSplitDiffRowDom(
            foldedRow,
            rowIndex + foldedIndex + 1,
            fileLabel,
          ),
        );
      });
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
      const fragment = document.createDocumentFragment();
      const firstRow = row.foldedRows[0];
      if (firstRow) {
        fragment.append(
          renderInlineDiffRowsDom(
            {
              ...firstRow,
              isHunkAnchor: isChangedRowStatus(firstRow.status),
            },
            rowIndex,
            fileLabel,
            { expanded: true, onToggle: toggle },
          ),
        );
      }
      row.foldedRows.slice(1).forEach((foldedRow, foldedIndex) => {
        fragment.append(
          renderInlineDiffRowsDom(
            foldedRow,
            rowIndex + foldedIndex + 1,
            fileLabel,
          ),
        );
      });
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

function renderSplitDiffRowDom(
  row: DiffRow & { isHunkAnchor?: boolean },
  rowIndex: number,
  fileLabel: string,
  foldToggle?: FoldToggle,
): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(row.status, row, foldToggle);
  element.dataset.rowIndex = String(rowIndex);
  if (foldToggle) {
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
): DocumentFragment | HTMLElement {
  const rightText = row.right_text ?? "";
  const leftText = row.left_text ?? "";
  const sharedText = rightText || leftText;
  const sharedTokens =
    (row.right_tokens?.length ? row.right_tokens : row.left_tokens) ?? [];
  const sharedSyntax =
    (row.right_syntax?.length ? row.right_syntax : row.left_syntax) ?? [];

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
      });
    case "delete":
      return renderInlineDiffRowDom({
        status: "delete",
        marker: "-",
        leftNo: row.left_no,
        rightNo: null,
        text: leftText,
        tokens: row.left_tokens ?? [],
        syntax: row.left_syntax ?? [],
        rowIndex,
        fileLabel,
        sourceRow: row,
        foldToggle,
      });
    case "insert":
      return renderInlineDiffRowDom({
        status: "insert",
        marker: "+",
        leftNo: null,
        rightNo: row.right_no,
        text: rightText,
        tokens: row.right_tokens ?? [],
        syntax: row.right_syntax ?? [],
        rowIndex,
        fileLabel,
        sourceRow: row,
        foldToggle,
      });
    case "replace": {
      const fragment = document.createDocumentFragment();
      fragment.append(
        renderInlineDiffRowDom({
          status: "delete",
          marker: "-",
          leftNo: row.left_no,
          rightNo: null,
          text: leftText,
          tokens: row.left_tokens ?? [],
          syntax: row.left_syntax ?? [],
          rowIndex,
          fileLabel,
          sourceRow: row,
          foldToggle,
        }),
        renderInlineDiffRowDom({
          status: "insert",
          marker: "+",
          leftNo: null,
          rightNo: row.right_no,
          text: rightText,
          tokens: row.right_tokens ?? [],
          syntax: row.right_syntax ?? [],
          rowIndex,
          fileLabel,
          sourceRow: { ...row, isHunkAnchor: false },
        }),
      );
      return fragment;
    }
    default:
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
      });
  }
}

function renderInlineDiffRowDom(props: {
  status: "equal" | "delete" | "insert";
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
}): HTMLElement {
  const element = document.createElement("div");
  element.className = diffRowClass(
    props.status,
    props.sourceRow,
    props.foldToggle,
    "inline-diff-row",
  );
  element.dataset.rowIndex = String(props.rowIndex);
  if (props.foldToggle) {
    element.title = "Collapse folded rows";
    element.addEventListener("click", props.foldToggle.onToggle);
  }
  element.append(
    createLineNumberDom(
      props.leftNo,
      "left",
      props.fileLabel,
      props.foldToggle,
    ),
    createLineNumberDom(props.rightNo, "right", props.fileLabel),
    createInlineLineCodeDom(
      props.marker,
      props.text,
      props.tokens,
      props.syntax,
    ),
  );
  return element;
}

function diffRowClass(
  status: string,
  row: DiffRow & { isHunkAnchor?: boolean },
  foldToggle?: FoldToggle,
  extraClass = "",
): string {
  const classes = ["diff-row"];
  if (extraClass) {
    classes.push(extraClass);
  }
  classes.push(status);
  if (row.isHunkAnchor) {
    classes.push("hunk-anchor");
  }
  if (isChangedRowStatus(status) && isWhitespaceOnlyChange(row)) {
    classes.push("whitespace-only-change");
  }
  if (foldToggle) {
    classes.push("fold-toggle-row");
    if (foldToggle.expanded) {
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
  const text = (side === "left" ? row.left_text : row.right_text) ?? "";
  const tokens = (side === "left" ? row.left_tokens : row.right_tokens) ?? [];
  const syntax = (side === "left" ? row.left_syntax : row.right_syntax) ?? [];
  const element = document.createElement("div");
  element.className = `diff-side side-${side}${
    lineNo === null && text === "" ? " empty-side" : ""
  }`;
  element.append(
    createLineNumberDom(lineNo, side, fileLabel, foldToggle),
    createLineCodeDom(text, tokens, syntax),
  );
  return element;
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
  return row.label ? `... ${lineText} in ${row.label}` : `... ${lineText}`;
}

function createLineNumberDom(
  lineNo: number | null,
  side: Side,
  fileLabel: string,
  foldToggle?: FoldToggle,
): HTMLElement {
  const element = document.createElement("div");
  element.className = "line-no";
  if (lineNo !== null) {
    element.dataset.linePinFile = fileLabel;
    element.dataset.linePinSide = side;
    element.dataset.linePinLine = String(lineNo);
    element.title = "Pin line";
  }
  if (foldToggle) {
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
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code";
  appendDecoratedText(element, text, tokens, syntax);
  return element;
}

function createInlineLineCodeDom(
  marker: InlineMarker,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
): HTMLElement {
  const element = document.createElement("code");
  element.className = "line-code inline-line-code";
  const markerElement = document.createElement("span");
  markerElement.className = "inline-marker";
  markerElement.ariaHidden = "true";
  markerElement.textContent = marker;
  element.append(markerElement);
  appendDecoratedText(element, text, tokens, syntax);
  return element;
}

function appendDecoratedText(
  element: HTMLElement,
  text: string,
  tokens: InlineToken[],
  syntax: SyntaxSpan[],
) {
  const tokenNodes = tokenParts(tokens);
  if (tokenNodes.length > 0) {
    for (const part of tokenNodes) {
      const span = document.createElement("span");
      const classes = [];
      if (part.changed) {
        classes.push("token-changed");
      }
      if (part.changed && part.isWhitespace) {
        classes.push("whitespace");
      }
      if (part.changed && part.isWhitespace && part.leading) {
        classes.push("whitespace-leading");
      }
      if (classes.length) {
        span.className = classes.join(" ");
      }
      if (part.changed && part.isWhitespace) {
        span.title = "Whitespace changed";
      }
      span.textContent = part.text;
      element.append(span);
    }
    return;
  }

  const syntaxNodes = syntaxParts(text, syntax);
  if (syntaxNodes.length === 0) {
    element.append(text);
    return;
  }
  for (const part of syntaxNodes) {
    if (part.classes.length === 0) {
      element.append(part.text);
      continue;
    }
    const span = document.createElement("span");
    span.className = `ts-token ${part.classes.join(" ")}`;
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
  const leftTokens = row.left_tokens ?? [];
  const rightTokens = row.right_tokens ?? [];
  const changedTokens = [...leftTokens, ...rightTokens].filter(
    (token) => token.changed,
  );
  return (
    changedTokens.length > 0 && changedTokens.every((token) => token.is_ws)
  );
}

function tokenParts(tokens: InlineToken[]) {
  return tokens.map((token, index) => ({
    text: token.text,
    changed: token.changed,
    isWhitespace: token.is_ws,
    leading: token.is_ws && index === 0,
  }));
}

function syntaxParts(text: string, syntax: SyntaxSpan[]) {
  if (!text || !syntax.length) {
    return [];
  }

  const parts: Array<{ text: string; classes: string[] }> = [];
  let cursor = 0;
  for (const span of syntax) {
    const start = clamp(span.start, 0, text.length);
    const end = clamp(span.end, start, text.length);
    if (start > cursor) {
      parts.push({ text: text.slice(cursor, start), classes: [] });
    }
    if (end > start) {
      const classes = visibleSyntaxClasses(span.classes);
      parts.push({
        text: text.slice(start, end),
        classes,
      });
    }
    cursor = Math.max(cursor, end);
  }
  if (cursor < text.length) {
    parts.push({ text: text.slice(cursor), classes: [] });
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
