import { For, Show, createSignal } from "solid-js";
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

export function DiffGrid(props: { file: FileEntry }) {
  const rows = () =>
    markHunkAnchors(addFoldRows(props.file.rows ?? [], props.file.fold_hints));
  const fileLabel = () =>
    props.file.display_name ||
    props.file.right_path ||
    props.file.left_path ||
    "(unknown file)";

  return (
    <div class="diff-grid">
      <div class="diff-header-row">
        <div class="diff-pane-header diff-side-header">
          {props.file.left_label}
        </div>
        <div class="diff-pane-header diff-side-header">
          {props.file.right_label}
        </div>
      </div>
      <div class="diff-lines">
        <RenderedRows
          rows={rows()}
          fileLabel={fileLabel()}
          leftLabel={props.file.left_label || "left"}
          rightLabel={props.file.right_label || "right"}
        />
      </div>
    </div>
  );
}

type HunkRenderRow = RenderRow & {
  isHunkAnchor?: boolean;
};

function RenderedRows(props: {
  rows: HunkRenderRow[];
  fileLabel: string;
  leftLabel: string;
  rightLabel: string;
}) {
  return (
    <For each={props.rows}>
      {(row, index) => (
        <Show
          when={isFoldRow(row)}
          fallback={
            <DiffGridRow
              row={row as DiffRow}
              rowIndex={index()}
              fileLabel={props.fileLabel}
            />
          }
        >
          <FoldSection
            row={row as FoldRow}
            rowIndex={index()}
            fileLabel={props.fileLabel}
            leftLabel={props.leftLabel}
            rightLabel={props.rightLabel}
          />
        </Show>
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
  const linePinAttrs = () => {
    const line = lineNo();
    if (line === null) {
      return {};
    }
    return {
      "data-line-pin-file": props.fileLabel,
      "data-line-pin-side": props.side,
      "data-line-pin-line": String(line),
      title: "Pin line",
    };
  };

  return (
    <div
      class={`diff-side side-${props.side}`}
      classList={{ "empty-side": empty() }}
    >
      <div class="line-no" {...linePinAttrs()}>
        <Show when={props.foldToggle}>
          <button
            type="button"
            class="inline-fold-toggle"
            aria-label={
              props.foldToggle?.expanded ? "Collapse fold" : "Expand fold"
            }
            onClick={(event) => {
              event.stopPropagation();
              props.foldToggle?.onToggle();
            }}
          >
            {props.foldToggle?.expanded ? "▾" : "▸"}
          </button>
        </Show>
        {lineNo() ?? ""}
      </div>
      <code class="line-code">
        <DecoratedText text={text()} tokens={tokens()} syntax={syntax()} />
      </code>
    </div>
  );
}

function DecoratedText(props: {
  text: string;
  tokens: InlineToken[];
  syntax: SyntaxSpan[];
}) {
  const tokenNodes = () => tokenParts(props.tokens);
  return (
    <Show
      when={tokenNodes().length > 0}
      fallback={<SyntaxText text={props.text} syntax={props.syntax} />}
    >
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
    </Show>
  );
}

function SyntaxText(props: { text: string; syntax: SyntaxSpan[] }) {
  const parts = () => syntaxParts(props.text, props.syntax);
  return (
    <Show when={parts().length > 0} fallback={props.text}>
      <For each={parts()}>
        {(part) => (
          <Show when={part.classes.length > 0} fallback={part.text}>
            <span class={`ts-token ${part.classes.join(" ")}`}>
              {part.text}
            </span>
          </Show>
        )}
      </For>
    </Show>
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
