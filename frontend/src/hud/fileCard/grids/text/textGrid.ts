import type { DiffRow, FoldHint, ReviewFilePair } from "../../../../api/api";
import type { DiffViewMode } from "../../../App";
import type { LinePins } from "../../../linePins";
import type { Navigation } from "../../../navigation";
import {
  LineMarkerKeySchema,
  paintCommentTriggers,
  type ReviewBinding,
  type ReviewTextGridBinding,
} from "../../../review/Review";
import { assert, expect } from "../../../../utils";
import { addFoldRows, isFoldRow } from "./folds";
import {
  finishForcedChunkLayout,
  forceChunkLayout,
  lineCellAt,
  lineCellFromEvent,
  lineCellsIn,
  moveAt,
  movesIn,
  paintLinePin,
  renderInlineRowsDom,
  warmChunksWhenIdle,
  renderSplitRowsDom,
  type LineCell,
  type Side,
} from "./rowDom";

export type TextGridInput = {
  rows: DiffRow[];
  foldHints: FoldHint[];
  interleaveInlineRows: boolean;
  leftLabel: string;
  rightLabel: string;
  view: DiffViewMode;
  aggressiveFolds: boolean;
};

export type TextGrid = {
  // Replaces every row and closes every fold.
  render(input: TextGridInput): void;
  // Repaints the lines the review's current marker revision changed. A
  // failure, including in reading that revision, is reported through
  // `onMarkerFailure` and never thrown.
  paintMarkers(): void;
  // Opens the folds holding the line. Null: the bay has no such line.
  revealLine(side: Side, line: number): LineCell | null;
  // Lays every chunk out for real, then measures the whole grid.
  realHeight(): number;
  // Gives freshly laid-out chunks one rendered frame to record their sizes.
  settleLayout(): Promise<void>;
  dispose(): void;
};

// Each grid's filter needs a document-unique id.
let nextMoveOutlineId = 0;

export function createTextGrid(context: {
  // The whole grid, header included, as measured by `realHeight`.
  frame: HTMLElement;
  // The row host this grid replaces the children of.
  root: HTMLElement;
  reviewFile: ReviewFilePair;
  fileIndex: number;
  bayKey: string;
  linePins: LinePins;
  review: ReviewBinding;
  navigate: Navigation["navigate"];
  changeSetRoot: Navigation["root"];
  showError(title: string, error: unknown): void;
  // Called once, with the first marker paint failure. Rows stay usable and
  // every trigger already painted stays inert for the grid's lifetime.
  onMarkerFailure(error: unknown): void;
}): TextGrid {
  const { frame, root, review } = context;
  const binding: ReviewTextGridBinding = {
    snapshot_id: review.snapshotId,
    file: context.reviewFile,
    bay: { bay_key: context.bayKey },
  };
  let input: TextGridInput | null = null;
  const expandedFolds = new Set<number>();
  let markersFailed = false;
  // A newer move click cancels an unfinished jump. Disposal also cancels it so
  // it cannot scroll after its source DOM has gone away.
  let moveNavigationAbort: AbortController | undefined;

  function lineLabel(cell: LineCell): string {
    return `${cell.side === "left" ? "old" : "new"} line ${cell.line}`;
  }

  // `all` repaints every line; otherwise only the keys the review's current
  // marker revision changed.
  function paintMarkers(all: boolean): void {
    if (markersFailed) {
      return;
    }
    try {
      const changed = all ? null : review.changedMarkerKeys();
      if (changed === null) {
        for (const cell of lineCellsIn(root)) {
          paintCommentTriggers(
            cell.triggerHost,
            review.markerState(binding, cell.side, cell.line),
            lineLabel(cell),
          );
        }
        return;
      }
      for (const key of changed) {
        // The key's shape is declared once beside its encoder, so this reader
        // cannot drift from it: a changed encoding fails here as a parse error
        // rather than as a stale hand-written arity check.
        const [leftPath, rightPath, bayKey, side, line] =
          LineMarkerKeySchema.parse(JSON.parse(key));
        if (
          leftPath !== binding.file.left_path ||
          rightPath !== binding.file.right_path ||
          bayKey !== binding.bay.bay_key
        ) {
          continue;
        }
        const cell = lineCellAt(root, side, line);
        if (cell !== null) {
          paintCommentTriggers(
            cell.triggerHost,
            review.markerState(binding, side, line),
            lineLabel(cell),
          );
        }
      }
    } catch (error) {
      markersFailed = true;
      // The failure may come from reading the lines themselves, so the
      // triggers are disabled without reading them again.
      paintCommentTriggers(root, null);
      context.onMarkerFailure(error);
    }
  }

  // This grid's SVG filter combines touching moved-text rectangles into one
  // outline, retaining holes occupied by different move relationships or
  // unassociated text.
  const moveOutlineId = `move-outline-${nextMoveOutlineId++}`;
  function paintMoveOutlines(): void {
    root.querySelector(".move-outlines")?.remove();
    const moves = movesIn(root);
    if (moves.length === 0) {
      return;
    }
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.classList.add("move-outlines");
    svg.setAttribute("aria-hidden", "true");
    // Static filter markup: dilation minus the source produces only the outer
    // edge of each group's union, so touching lines have no internal borders.
    svg.innerHTML = `<defs><filter id="${moveOutlineId}" x="-5%" y="-5%" width="110%" height="110%"><feMorphology in="SourceAlpha" operator="dilate" radius="1" result="expanded"/><feComposite in="expanded" in2="SourceAlpha" operator="out" result="edge"/><feFlood flood-color="#a56abd"/><feComposite in2="edge" operator="in"/></filter></defs>`;
    const origin = root.getBoundingClientRect();
    const groups = new Map<string, SVGGElement>();
    for (const move of moves) {
      const groupKey = `${move.side}:${move.key}`;
      let group = groups.get(groupKey);
      if (group === undefined) {
        group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        group.dataset.moveSide = move.side;
        group.dataset.moveKey = move.key;
        group.setAttribute("filter", `url(#${moveOutlineId})`);
        group.style.setProperty(
          "--move-outline-filter",
          `url(#${moveOutlineId})`,
        );
        group.classList.toggle(
          "move-highlighted",
          move.element.classList.contains("move-highlighted"),
        );
        groups.set(groupKey, group);
        svg.append(group);
      }
      const cell = move.codeCell.getBoundingClientRect();
      const lineHeight = Number.parseFloat(
        getComputedStyle(move.codeCell).lineHeight,
      );
      for (const bounds of move.element.getClientRects()) {
        const rect = document.createElementNS(
          "http://www.w3.org/2000/svg",
          "rect",
        );
        rect.setAttribute("x", String(bounds.left - origin.left));
        rect.setAttribute(
          "y",
          String(
            (cell.height < lineHeight * 2 ? cell.top : bounds.top - 2) -
              origin.top,
          ),
        );
        rect.setAttribute("width", String(bounds.width));
        rect.setAttribute(
          "height",
          String(cell.height < lineHeight * 2 ? cell.height : lineHeight),
        );
        group.append(rect);
      }
    }
    root.append(svg);
  }

  // Runs after every row replacement, whole-grid or one fold's. New rows carry
  // no review decoration, outline or pin paint yet.
  function afterRowsChanged(): void {
    paintMoveOutlines();
    paintMarkers(true);
    const parsed = context.linePins.parseUrl();
    // An invalid or foreign pin is answered by line-pin restoration, not here.
    if (
      parsed.state === "valid" &&
      parsed.target.file.left_path === context.reviewFile.left_path &&
      parsed.target.file.right_path === context.reviewFile.right_path &&
      parsed.target.bay.bay_key === context.bayKey
    ) {
      const cell = lineCellAt(
        root,
        parsed.target.side,
        Number(parsed.target.line),
      );
      if (cell !== null) {
        paintLinePin(context.changeSetRoot(), cell, true);
      }
    }
    warmChunksWhenIdle(root);
  }

  function renderRows(): void {
    const current = expect(input, "A text grid renders only after its input.");
    const rows = addFoldRows(
      current.rows,
      current.foldHints,
      current.aggressiveFolds,
    );
    const fragment =
      current.view === "inline"
        ? renderInlineRowsDom(
            rows,
            current.interleaveInlineRows,
            expandedFolds,
            context.fileIndex,
            context.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          )
        : renderSplitRowsDom(
            rows,
            current.leftLabel,
            current.rightLabel,
            expandedFolds,
            context.fileIndex,
            context.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          );
    review.closeAnchoredUi(root);
    root.replaceChildren(fragment);
    afterRowsChanged();
  }

  function revealLine(side: Side, line: number): LineCell | null {
    const current = expect(input, "A text grid reveals only after its input.");
    const existing = lineCellAt(root, side, line);
    if (existing !== null) {
      return existing;
    }
    const matchingRowIndexes = current.rows.flatMap((row, rowIndex) => {
      const candidate = side === "left" ? row.left_no : row.right_no;
      return candidate === line ? [rowIndex] : [];
    });
    if (matchingRowIndexes.length === 0) {
      return null;
    }
    assert(
      matchingRowIndexes.length === 1,
      "Backend rows contain duplicate line-pin coordinates.",
    );
    const targetRowIndex = expect(
      matchingRowIndexes[0],
      "Matched line row index disappeared.",
    );
    let expanded = false;
    const remaining = [
      ...addFoldRows(current.rows, current.foldHints, current.aggressiveFolds),
    ];
    while (remaining.length > 0) {
      const candidate = remaining.shift();
      if (candidate === undefined || !isFoldRow(candidate)) {
        continue;
      }
      if (
        targetRowIndex >= candidate.startRow &&
        targetRowIndex < candidate.startRow + candidate.count &&
        !expandedFolds.has(candidate.startRow)
      ) {
        expandedFolds.add(candidate.startRow);
        expanded = true;
      }
      remaining.unshift(...candidate.foldedRows);
    }
    assert(
      expanded,
      "A known unrendered line must belong to a collapsed fold.",
    );
    renderRows();
    return lineCellAt(root, side, line);
  }

  function handleLineActivation(event: MouseEvent): void {
    const clicked = event.target;
    if (!(clicked instanceof Element)) {
      return;
    }
    const move = moveAt(clicked);
    if (move !== null && root.contains(move.element)) {
      // A drag or multi-click selects source text instead of navigating.
      if (event.detail > 1 || window.getSelection()?.isCollapsed === false) {
        return;
      }
      event.stopPropagation();
      const counterpart = move.side === "left" ? move.right : move.left;
      assert(
        counterpart.startLine > 0,
        "Move counterpart line must be positive.",
      );
      moveNavigationAbort?.abort();
      moveNavigationAbort = new AbortController();
      void context
        .navigate({
          kind: "line",
          fileIndex: context.fileIndex,
          target: {
            file: context.reviewFile,
            bay: { bay_key: context.bayKey },
            side: move.side === "left" ? "right" : "left",
            line: String(counterpart.startLine),
          },
          abortSignal: moveNavigationAbort.signal,
        })
        .then((result) => {
          assert(
            result.state !== "missing",
            "Move counterpart is absent from its source bay.",
          );
        })
        .catch((error: unknown) =>
          context.showError("Move navigation failed", error),
        );
      return;
    }
    const cell = lineCellFromEvent(clicked);
    if (cell === null || !root.contains(cell.row)) {
      return;
    }
    event.stopPropagation();
    const trigger = Array.from(cell.triggerHost.children).find((child) =>
      child.contains(clicked),
    );
    if (trigger !== undefined) {
      assert(
        trigger instanceof HTMLButtonElement,
        "Comment trigger must be a button inside its line number.",
      );
      review.activateTextCommentInput(
        binding,
        cell.side,
        cell.line,
        { codeCell: cell.codeCell, trigger },
        event.shiftKey,
      );
      return;
    }
    paintLinePin(
      context.changeSetRoot(),
      cell,
      context.linePins.toggleUrlState({
        file: context.reviewFile,
        bay: { bay_key: context.bayKey },
        side: cell.side,
        line: String(cell.line),
      }) === "pinned",
    );
  }

  function highlightMove(event: PointerEvent): void {
    const [entered, exited] = [event.target, event.relatedTarget].map(
      (target) => {
        const move = target instanceof Element ? moveAt(target) : null;
        return move !== null && root.contains(move.element) ? move : null;
      },
    );
    if (entered?.key === exited?.key && entered?.side === exited?.side) {
      return;
    }
    const hovered = event.type === "pointerout" ? exited : entered;
    const moves = movesIn(root);
    const selected = new Set<string>();
    if (hovered !== null) {
      const span = hovered.side === "left" ? hovered.left : hovered.right;
      // Origins can lie outside the parent's origin. Containment is evaluated
      // only on the side being hovered, then paired identities cross sides.
      for (const candidate of moves) {
        if (candidate.side !== hovered.side) {
          continue;
        }
        const candidateSpan =
          hovered.side === "left" ? candidate.left : candidate.right;
        if (
          (candidateSpan.startLine > span.startLine ||
            (candidateSpan.startLine === span.startLine &&
              candidateSpan.startColumn >= span.startColumn)) &&
          (candidateSpan.endLine < span.endLine ||
            (candidateSpan.endLine === span.endLine &&
              candidateSpan.endColumn <= span.endColumn))
        ) {
          selected.add(candidate.key);
        }
      }
    }
    // Coverage never changes: hover emphasizes existing text and outlines for
    // the selected move and descendants, including their opposite-side pieces.
    for (const move of moves) {
      move.element.classList.toggle("move-highlighted", selected.has(move.key));
    }
    for (const group of root.querySelectorAll<SVGGElement>(
      ".move-outlines g",
    )) {
      group.classList.toggle(
        "move-highlighted",
        selected.has(
          expect(group.dataset.moveKey, "A move outline names its move."),
        ),
      );
    }
  }

  root.addEventListener("click", handleLineActivation);
  // Delegation covers fold replacements without per-token listeners. Pair
  // identity stays in mounted DOM; disposal removes both pointer listeners.
  root.addEventListener("pointerover", highlightMove);
  root.addEventListener("pointerout", highlightMove);
  // Repaints outlines after wrapping or font and layout changes. The SVG is
  // absolutely positioned and cannot resize the grid itself.
  const moveResize = new ResizeObserver(paintMoveOutlines);
  moveResize.observe(root);

  return {
    render(next) {
      input = next;
      expandedFolds.clear();
      renderRows();
    },
    paintMarkers: () => paintMarkers(false),
    revealLine,
    realHeight() {
      forceChunkLayout(root);
      const height = frame.getBoundingClientRect().height;
      assert(
        Number.isFinite(height) && height > 0,
        "A text grid must have a finite positive height.",
      );
      return height;
    },
    async settleLayout() {
      const fresh = forceChunkLayout(root);
      if (fresh.length === 0) {
        return;
      }
      await new Promise<void>((resolve) => {
        requestAnimationFrame(() => resolve());
      });
      if (!root.isConnected) {
        return;
      }
      finishForcedChunkLayout(fresh);
    },
    dispose() {
      moveNavigationAbort?.abort();
      review.closeAnchoredUi(root);
      root.removeEventListener("click", handleLineActivation);
      root.removeEventListener("pointerover", highlightMove);
      root.removeEventListener("pointerout", highlightMove);
      moveResize.disconnect();
    },
  };
}
