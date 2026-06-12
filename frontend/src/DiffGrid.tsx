import { For, Show, createSignal, type JSX } from "solid-js";
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
  const rows = () =>
    markHunkAnchors(addFoldRows(props.file.rows ?? [], props.file.fold_hints));
  const fileLabel = () =>
    props.file.display_name ||
    props.file.right_path ||
    props.file.left_path ||
    "(unknown file)";

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
      <div class="diff-lines">
        {props.viewMode === "inline" ? (
          <InlineRows rows={rows()} fileLabel={fileLabel()} />
        ) : (
          <SplitRows
            rows={rows()}
            fileLabel={fileLabel()}
            leftLabel={props.file.left_label || "left"}
            rightLabel={props.file.right_label || "right"}
          />
        )}
      </div>
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

function SplitRows(props: {
  rows: HunkRenderRow[];
  fileLabel: string;
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <For each={props.rows}>
      {(row, index) => (
        <>
          {isFoldRow(row) ? (
            <FoldSection
              row={row as FoldRow}
              rowIndex={index()}
              fileLabel={props.fileLabel}
              leftLabel={props.leftLabel}
              rightLabel={props.rightLabel}
            />
          ) : (
            <DiffGridRow
              row={row as DiffRow}
              rowIndex={index()}
              fileLabel={props.fileLabel}
            />
          )}
        </>
      )}
    </For>
  );
}

function InlineRows(props: { rows: HunkRenderRow[]; fileLabel: string }) {
  return (
    <For each={props.rows}>
      {(row, index) => (
        <>
          {isFoldRow(row) ? (
            <InlineFoldSection
              row={row as FoldRow}
              rowIndex={index()}
              fileLabel={props.fileLabel}
            />
          ) : (
            <InlineDiffRows
              row={row as DiffRow & { isHunkAnchor?: boolean }}
              rowIndex={index()}
              fileLabel={props.fileLabel}
            />
          )}
        </>
      )}
    </For>
  );
}

function FoldSection(props: {
  row: FoldRow;
  rowIndex: number;
  fileLabel: string;
  leftLabel: string;
  rightLabel: string;
}) {
  const [expanded, setExpanded] = createSignal(false);
  const toggle = () => setExpanded((value) => !value);

  return (
    <>
      <Show when={!expanded()}>
        <button
          type="button"
          class="diff-row fold-bar"
          data-row-index={props.rowIndex}
          onClick={toggle}
          title="Expand folded rows"
        >
          <FoldSide
            count={props.row.count}
            label={props.row.label}
            sideLabel={props.leftLabel}
          />
          <FoldSide
            count={props.row.count}
            label={props.row.label}
            sideLabel={props.rightLabel}
          />
        </button>
      </Show>
      <Show when={expanded()}>
        <DiffGridRow
          row={{
            ...props.row.foldedRows[0],
            isHunkAnchor: isChangedRowStatus(props.row.foldedRows[0]?.status),
          }}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          foldToggle={{ expanded: true, onToggle: toggle }}
        />
        <For each={props.row.foldedRows.slice(1)}>
          {(foldedRow, foldedIndex) => (
            <DiffGridRow
              row={foldedRow}
              rowIndex={props.rowIndex + foldedIndex() + 1}
              fileLabel={props.fileLabel}
            />
          )}
        </For>
      </Show>
    </>
  );
}

function FoldSide(props: { count: number; label: string; sideLabel: string }) {
  const lineText = () => `${props.count} line${props.count === 1 ? "" : "s"}`;
  const text = () =>
    props.label ? `... ${lineText()} in ${props.label}` : `... ${lineText()}`;
  return (
    <div class="diff-side fold-side" data-side-label={props.sideLabel}>
      <div class="line-no">..</div>
      <div class="fold-label">{text()}</div>
    </div>
  );
}

function InlineFoldSection(props: {
  row: FoldRow;
  rowIndex: number;
  fileLabel: string;
}) {
  const [expanded, setExpanded] = createSignal(false);
  const toggle = () => setExpanded((value) => !value);

  return (
    <>
      <Show when={!expanded()}>
        <button
          type="button"
          class="diff-row inline-diff-row inline-fold-bar"
          data-row-index={props.rowIndex}
          onClick={toggle}
          title="Expand folded rows"
        >
          <div class="line-no">..</div>
          <div class="line-no">..</div>
          <div class="fold-label inline-fold-label">{foldLabel(props.row)}</div>
        </button>
      </Show>
      <Show when={expanded()}>
        <InlineDiffRows
          row={{
            ...props.row.foldedRows[0],
            isHunkAnchor: isChangedRowStatus(props.row.foldedRows[0]?.status),
          }}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          foldToggle={{ expanded: true, onToggle: toggle }}
        />
        <For each={props.row.foldedRows.slice(1)}>
          {(foldedRow, foldedIndex) => (
            <InlineDiffRows
              row={foldedRow}
              rowIndex={props.rowIndex + foldedIndex() + 1}
              fileLabel={props.fileLabel}
            />
          )}
        </For>
      </Show>
    </>
  );
}

function foldLabel(row: FoldRow) {
  const lineText = `${row.count} line${row.count === 1 ? "" : "s"}`;
  return row.label ? `... ${lineText} in ${row.label}` : `... ${lineText}`;
}

function DiffGridRow(props: {
  row: DiffRow & { isHunkAnchor?: boolean };
  rowIndex: number;
  fileLabel: string;
  foldToggle?: { expanded: boolean; onToggle: () => void };
}) {
  const row = () => props.row;
  const changed = () => isChangedRowStatus(row().status);
  const whitespaceOnly = () => changed() && isWhitespaceOnlyChange(row());

  return (
    <div
      class="diff-row"
      classList={{
        [row().status]: true,
        "hunk-anchor": Boolean(row().isHunkAnchor),
        "whitespace-only-change": whitespaceOnly(),
        "fold-toggle-row": Boolean(props.foldToggle),
        "fold-expanded": Boolean(props.foldToggle?.expanded),
      }}
      data-row-index={props.rowIndex}
      title={props.foldToggle ? "Collapse folded rows" : undefined}
      onClick={props.foldToggle?.onToggle}
    >
      <DiffSide
        row={row()}
        side="left"
        fileLabel={props.fileLabel}
        foldToggle={props.foldToggle}
      />
      <DiffSide
        row={row()}
        side="right"
        fileLabel={props.fileLabel}
        foldToggle={props.foldToggle}
      />
    </div>
  );
}

function InlineDiffRows(props: {
  row: DiffRow & { isHunkAnchor?: boolean };
  rowIndex: number;
  fileLabel: string;
  foldToggle?: { expanded: boolean; onToggle: () => void };
}) {
  const row = () => props.row;
  const rightText = () => row().right_text ?? "";
  const leftText = () => row().left_text ?? "";
  const sharedText = () => rightText() || leftText();
  const sharedTokens = () =>
    (row().right_tokens?.length ? row().right_tokens : row().left_tokens) ?? [];
  const sharedSyntax = () =>
    (row().right_syntax?.length ? row().right_syntax : row().left_syntax) ?? [];

  return (
    <>
      <Show when={row().status === "equal"}>
        <InlineDiffRow
          status="equal"
          marker=" "
          leftNo={row().left_no}
          rightNo={row().right_no}
          text={sharedText()}
          tokens={sharedTokens()}
          syntax={sharedSyntax()}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          sourceRow={row()}
          foldToggle={props.foldToggle}
        />
      </Show>
      <Show when={row().status === "delete"}>
        <InlineDiffRow
          status="delete"
          marker="-"
          leftNo={row().left_no}
          rightNo={null}
          text={leftText()}
          tokens={row().left_tokens ?? []}
          syntax={row().left_syntax ?? []}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          sourceRow={row()}
          foldToggle={props.foldToggle}
        />
      </Show>
      <Show when={row().status === "insert"}>
        <InlineDiffRow
          status="insert"
          marker="+"
          leftNo={null}
          rightNo={row().right_no}
          text={rightText()}
          tokens={row().right_tokens ?? []}
          syntax={row().right_syntax ?? []}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          sourceRow={row()}
          foldToggle={props.foldToggle}
        />
      </Show>
      <Show when={row().status === "replace"}>
        <InlineDiffRow
          status="delete"
          marker="-"
          leftNo={row().left_no}
          rightNo={null}
          text={leftText()}
          tokens={row().left_tokens ?? []}
          syntax={row().left_syntax ?? []}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          sourceRow={row()}
          foldToggle={props.foldToggle}
        />
        <InlineDiffRow
          status="insert"
          marker="+"
          leftNo={null}
          rightNo={row().right_no}
          text={rightText()}
          tokens={row().right_tokens ?? []}
          syntax={row().right_syntax ?? []}
          rowIndex={props.rowIndex}
          fileLabel={props.fileLabel}
          sourceRow={{ ...row(), isHunkAnchor: false }}
        />
      </Show>
    </>
  );
}

function InlineDiffRow(props: {
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
  foldToggle?: { expanded: boolean; onToggle: () => void };
}) {
  const changed = () => props.status !== "equal";
  const whitespaceOnly = () =>
    changed() && isWhitespaceOnlyChange(props.sourceRow);

  return (
    <div
      class="diff-row inline-diff-row"
      classList={{
        [props.status]: true,
        "hunk-anchor": Boolean(props.sourceRow.isHunkAnchor),
        "whitespace-only-change": whitespaceOnly(),
        "fold-toggle-row": Boolean(props.foldToggle),
        "fold-expanded": Boolean(props.foldToggle?.expanded),
      }}
      data-row-index={props.rowIndex}
      title={props.foldToggle ? "Collapse folded rows" : undefined}
      onClick={props.foldToggle?.onToggle}
    >
      <LineNumber lineNo={props.leftNo} side="left" fileLabel={props.fileLabel}>
        <Show when={props.foldToggle}>
          <FoldToggleButton foldToggle={props.foldToggle} />
        </Show>
      </LineNumber>
      <LineNumber
        lineNo={props.rightNo}
        side="right"
        fileLabel={props.fileLabel}
      />
      <InlineLineCode
        marker={props.marker}
        text={props.text}
        tokens={props.tokens}
        syntax={props.syntax}
      />
    </div>
  );
}

function DiffSide(props: {
  row: DiffRow;
  side: Side;
  fileLabel: string;
  foldToggle?: { expanded: boolean; onToggle: () => void };
}) {
  const lineNo = () =>
    props.side === "left" ? props.row.left_no : props.row.right_no;
  const text = () =>
    (props.side === "left" ? props.row.left_text : props.row.right_text) ?? "";
  const tokens = () =>
    (props.side === "left" ? props.row.left_tokens : props.row.right_tokens) ??
    [];
  const syntax = () =>
    (props.side === "left" ? props.row.left_syntax : props.row.right_syntax) ??
    [];
  const empty = () => lineNo() === null && text() === "";

  return (
    <div
      class={`diff-side side-${props.side}`}
      classList={{ "empty-side": empty() }}
    >
      <LineNumber
        lineNo={lineNo()}
        side={props.side}
        fileLabel={props.fileLabel}
      >
        <Show when={props.foldToggle}>
          <FoldToggleButton foldToggle={props.foldToggle} />
        </Show>
      </LineNumber>
      <LineCode text={text()} tokens={tokens()} syntax={syntax()} />
    </div>
  );
}

function FoldToggleButton(props: {
  foldToggle?: { expanded: boolean; onToggle: () => void };
}) {
  return (
    <button
      type="button"
      class="inline-fold-toggle"
      aria-label={props.foldToggle?.expanded ? "Collapse fold" : "Expand fold"}
      onClick={(event) => {
        event.stopPropagation();
        props.foldToggle?.onToggle();
      }}
    >
      {props.foldToggle?.expanded ? "▾" : "▸"}
    </button>
  );
}

function LineNumber(props: {
  lineNo: number | null;
  side: Side;
  fileLabel: string;
  children?: JSX.Element;
}) {
  const linePinAttrs = () => {
    if (props.lineNo === null) {
      return {};
    }
    return {
      "data-line-pin-file": props.fileLabel,
      "data-line-pin-side": props.side,
      "data-line-pin-line": String(props.lineNo),
      title: "Pin line",
    };
  };

  return (
    <div class="line-no" {...linePinAttrs()}>
      {props.children}
      {props.lineNo ?? ""}
    </div>
  );
}

function LineCode(props: {
  text: string;
  tokens: InlineToken[];
  syntax: SyntaxSpan[];
}) {
  return (
    <code class="line-code">
      <DecoratedText
        text={props.text}
        tokens={props.tokens}
        syntax={props.syntax}
      />
    </code>
  );
}

function InlineLineCode(props: {
  marker: InlineMarker;
  text: string;
  tokens: InlineToken[];
  syntax: SyntaxSpan[];
}) {
  return (
    <code class="line-code inline-line-code">
      <span class="inline-marker" aria-hidden="true">
        {props.marker}
      </span>
      <DecoratedText
        text={props.text}
        tokens={props.tokens}
        syntax={props.syntax}
      />
    </code>
  );
}

function DecoratedText(props: {
  text: string;
  tokens: InlineToken[];
  syntax: SyntaxSpan[];
}) {
  const tokenNodes = () => tokenParts(props.tokens);
  return (
    <>
      {tokenNodes().length > 0 ? (
        <For each={tokenNodes()}>
          {(part) => (
            <span
              classList={{
                "token-changed": part.changed,
                whitespace: part.changed && part.isWhitespace,
                "whitespace-leading":
                  part.changed && part.isWhitespace && part.leading,
              }}
              title={
                part.changed && part.isWhitespace
                  ? "Whitespace changed"
                  : undefined
              }
            >
              {part.text}
            </span>
          )}
        </For>
      ) : (
        <SyntaxText text={props.text} syntax={props.syntax} />
      )}
    </>
  );
}

function SyntaxText(props: { text: string; syntax: SyntaxSpan[] }) {
  const parts = () => syntaxParts(props.text, props.syntax);
  return (
    <>
      {parts().length > 0 ? (
        <For each={parts()}>
          {(part) =>
            part.classes.length > 0 ? (
              <span class={`ts-token ${part.classes.join(" ")}`}>
                {part.text}
              </span>
            ) : (
              part.text
            )
          }
        </For>
      ) : (
        props.text
      )}
    </>
  );
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
