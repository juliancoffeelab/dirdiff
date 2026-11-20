import {
  ErrorBoundary,
  Show,
  createEffect,
  createSignal,
  createUniqueId,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";
import {
  type DiffRow,
  type FoldHint,
  type ReviewFilePair,
} from "../../../../api/api";
import type { DiffViewMode } from "../../../App";
import { addFoldRows, isFoldRow } from "./folds";
import type { LinePins, LinePinTarget, PreparedLine } from "../../../linePins";
import { assert, expect } from "../../../../utils";
import { useNavigation } from "../../../navigation";
import { presentError, useToasts } from "../../../../comp/Toasts";
import {
  LineMarkerKeySchema,
  useReview,
  type ReviewMarkerKind,
  type ReviewTextGridBinding,
} from "../../../review/Review";
import { renderInlineRowsDom, renderSplitRowsDom, type Side } from "./rowDom";

export function TextDiffGrid(props: {
  reviewFile: ReviewFilePair;

  fileIndex: number;

  displayName: string;

  bayKey: string;

  contentLabel: string;

  leftLabel: string;

  rightLabel: string;

  rows: DiffRow[];

  foldHints: FoldHint[];

  interleaveInlineRows: boolean;

  viewMode: DiffViewMode;

  aggressiveFolds: boolean;

  linePins: LinePins;
}) {
  return (
    <div
      class="diff-grid"
      data-review-bay={props.bayKey}
      classList={{
        "diff-grid-inline": props.viewMode === "inline",
      }}
    >
      {props.viewMode === "inline" ? (
        <InlineHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
          contentLabel={props.contentLabel}
        />
      ) : (
        <SplitHeader
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
        />
      )}
      {/* An empty text bay (an added or removed empty File, an empty cell)
          has no rows to show, so this strip says so where they would be. The
          row host below still mounts: line-pin preparation looks it up. */}
      <Show when={props.rows.length === 0}>
        <div class="diff-empty-strip">(empty)</div>
      </Show>
      <ImperativeDiffLines
        reviewFile={props.reviewFile}
        fileIndex={props.fileIndex}
        displayName={props.displayName}
        bayKey={props.bayKey}
        rows={props.rows}
        foldHints={props.foldHints}
        interleaveInlineRows={props.interleaveInlineRows}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        viewMode={props.viewMode}
        aggressiveFolds={props.aggressiveFolds}
        linePins={props.linePins}
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

function InlineHeader(props: {
  leftLabel: string;
  rightLabel: string;
  contentLabel: string;
}) {
  return (
    <div class="diff-header-row inline-header-row">
      <div class="diff-pane-header inline-line-header" title={props.leftLabel}>
        old
      </div>
      <div class="diff-pane-header inline-line-header" title={props.rightLabel}>
        new
      </div>
      <div class="diff-pane-header">{props.contentLabel}</div>
    </div>
  );
}

type PreparableDiffLines = HTMLDivElement & {
  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

function ImperativeDiffLines(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  displayName: string;
  bayKey: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  interleaveInlineRows: boolean;
  leftLabel: string;
  rightLabel: string;
  viewMode: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
}) {
  let root!: PreparableDiffLines;
  const review = useReview();
  const [rowRevision, setRowRevision] = createSignal(0);
  let reviewMarkersFailed = false;
  const reviewBinding: ReviewTextGridBinding = {
    snapshot_id: review.snapshotId,
    file: props.reviewFile,
    bay: { bay_key: props.bayKey },
  };
  const navigation = useNavigation();
  const toast = useToasts();
  // A newer move click cancels an unfinished jump. Grid disposal also cancels
  // preparation so it cannot scroll after its source DOM has gone away.
  let moveNavigationAbort: AbortController | undefined;
  onCleanup(() => moveNavigationAbort?.abort());
  const expandedFolds = new Set<number>();
  let previousDisplayName: string | undefined;
  let previousRows: DiffRow[] | undefined;
  let previousFoldHints: FoldHint[] | undefined;
  let previousLeftLabel: string | undefined;
  let previousRightLabel: string | undefined;
  let previousViewMode: DiffViewMode | undefined;
  let previousAggressiveFolds: boolean | undefined;

  function reviewMarkerKind(trigger: HTMLButtonElement): ReviewMarkerKind {
    const markerKind = trigger.dataset.reviewMarkerKind;
    assert(
      markerKind === "new" ||
        markerKind === "draft" ||
        markerKind === "open" ||
        markerKind === "resolved" ||
        markerKind === "deleted",
      "Comment trigger has an invalid marker kind.",
    );
    return markerKind;
  }

  function ReviewMarkerRefresh(): JSX.Element {
    let appliedRowRevision = -1;
    // Keep marker buttons synchronized with review facts and imperative row
    // replacements for this child's mounted lifetime. The effect reads the
    // review revision, changed-key set, and rowRevision signal; Solid disposes
    // the subscription on unmount, while onCleanup removes marker behavior from
    // DOM that may outlive a failed refresh.
    createEffect(() => {
      review.markerRevision();
      const rows = rowRevision();
      try {
        if (rows !== appliedRowRevision) {
          // New or replaced rows carry no decoration yet: refresh them all.
          appliedRowRevision = rows;
          refreshReviewMarkers();
          return;
        }
        const changed = review.changedMarkerKeys();
        if (changed === null) {
          refreshReviewMarkers();
        } else if (changed.size > 0) {
          refreshChangedReviewMarkers(changed);
        }
      } catch (error) {
        reviewMarkersFailed = true;
        disableReviewMarkers();
        throw error;
      }
    });
    onCleanup(disableReviewMarkers);
    return <></>;
  }

  function renderedRow(target: LinePinTarget): HTMLElement | null {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path &&
        target.bay.bay_key === props.bayKey,
      "TextDiffGrid received a line target from another bay.",
    );
    const matchingLines = root.querySelectorAll<HTMLElement>(
      `.line-no[data-line-pin-side="${target.side}"][data-line-pin-line="${target.line}"]`,
    );
    assert(
      matchingLines.length <= 1,
      "TextDiffGrid contains duplicate line-pin coordinates.",
    );
    const lineNumber = matchingLines[0];
    if (lineNumber === undefined) {
      return null;
    }
    const row = lineNumber.closest<HTMLElement>(".diff-row");
    assert(
      row !== null && root.contains(row),
      "Pinnable line has no TextDiffGrid row.",
    );
    return row;
  }

  function handleLineActivation(event: MouseEvent): void {
    const clicked = event.target;
    if (!(clicked instanceof HTMLElement)) {
      return;
    }
    const move = clicked.closest<HTMLElement>(".token-moved");
    if (move !== null && root.contains(move)) {
      // A drag or multi-click selects source text instead of navigating.
      if (event.detail > 1 || window.getSelection()?.isCollapsed === false) {
        return;
      }
      event.stopPropagation();
      const side = move.dataset.moveSide;
      assert(side === "left" || side === "right", "Move has an invalid side.");
      const coordinates = expect(move.dataset.moveKey, "Move has no endpoints.")
        .split(":")
        .map(Number);
      assert(
        coordinates.length === 8 && coordinates.every(Number.isInteger),
        "Move has invalid endpoints.",
      );
      const line = expect(
        coordinates[side === "left" ? 4 : 0],
        "Move has no counterpart start.",
      );
      assert(line > 0, "Move counterpart line must be positive.");
      moveNavigationAbort?.abort();
      moveNavigationAbort = new AbortController();
      void navigation
        .navigate({
          kind: "line",
          fileIndex: props.fileIndex,
          target: {
            file: props.reviewFile,
            bay: { bay_key: props.bayKey },
            side: side === "left" ? "right" : "left",
            line: String(line),
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
          toast.showError("Move navigation failed", error),
        );
      return;
    }
    const lineNumber = clicked.closest<HTMLElement>(
      ".line-no[data-line-pin-line]",
    );
    if (lineNumber === null || !root.contains(lineNumber)) {
      return;
    }
    event.stopPropagation();
    const commentTrigger = clicked.closest(".line-comment-trigger");
    if (commentTrigger !== null) {
      assert(
        commentTrigger instanceof HTMLButtonElement &&
          lineNumber.contains(commentTrigger),
        "Comment trigger must be a button inside its line number.",
      );
    }
    const side = lineNumber.dataset.linePinSide;
    const line = lineNumber.dataset.linePinLine;
    assert(
      side === "left" || side === "right",
      "Pinnable line has an invalid side identity.",
    );
    assert(
      line !== undefined && /^[1-9]\d*$/u.test(line),
      "Pinnable line has an invalid backend line identity.",
    );
    if (commentTrigger !== null) {
      const commentRowPart = lineNumber.parentElement;
      const commentCodeCell =
        commentRowPart?.querySelector<HTMLElement>(":scope > .line-code") ??
        null;
      assert(
        commentRowPart instanceof HTMLElement &&
          (commentRowPart.classList.contains("diff-side") ||
            commentRowPart.classList.contains("inline-diff-row")) &&
          root.contains(commentRowPart) &&
          commentCodeCell !== null,
        "Comment trigger must belong to a rendered diff code cell.",
      );
      review.activateTextCommentInput(
        reviewBinding,
        side,
        Number(line),
        { codeCell: commentCodeCell, trigger: commentTrigger },
        reviewMarkerKind(commentTrigger),
        event.shiftKey,
      );
      return;
    }
    const target: LinePinTarget = {
      file: props.reviewFile,
      bay: { bay_key: props.bayKey },
      side,
      line,
    };
    const row = expect(
      renderedRow(target),
      "Activated line disappeared from its TextDiffGrid.",
    );
    const changeSetRoot = expect(
      root.closest<HTMLElement>("[data-change-set-root]"),
      "TextDiffGrid requires its ChangeSet root.",
    );
    const paintedRows =
      changeSetRoot.querySelectorAll<HTMLElement>(".pinned-line");
    assert(
      paintedRows.length <= 1,
      "ChangeSet contains multiple painted line pins.",
    );
    paintedRows[0]?.classList.remove("pinned-line");
    if (props.linePins.toggleUrlState(target) === "pinned") {
      row.classList.add("pinned-line");
    }
  }

  function restoreOldPin(): void {
    const parsed = props.linePins.parseUrl();
    // we dont validatate the pin, it must be checked elsewhere
    if (parsed.state === "valid") {
      const row = renderedRow(parsed.target);
      // we ignore missing rows too, again, must be checked elsewhere
      if (row === null) {
        return;
      }
      // row is not a fold-edge
      if (!row.classList.contains("fold-toggle-row")) {
        row.classList.add("pinned-line");
      }
    }
  }

  async function prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path &&
        target.bay.bay_key === props.bayKey,
      "TextDiffGrid preparation received the wrong target.",
    );
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const existing = renderedRow(target);
    if (existing !== null) {
      return { state: "ready", row: existing };
    }
    const lineNumber = Number(target.line);
    const matchingRowIndexes = props.rows.flatMap((row, rowIndex) => {
      const candidate = target.side === "left" ? row.left_no : row.right_no;
      return candidate === lineNumber ? [rowIndex] : [];
    });
    if (matchingRowIndexes.length === 0) {
      return { state: "missing" };
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
      ...addFoldRows(props.rows, props.foldHints, props.aggressiveFolds),
    ];
    while (remaining.length > 0) {
      const candidate = remaining.shift();
      if (candidate === undefined || !isFoldRow(candidate)) {
        continue;
      }
      if (
        targetRowIndex >= candidate.startRow &&
        targetRowIndex < candidate.startRow + candidate.count
      ) {
        if (!expandedFolds.has(candidate.startRow)) {
          expandedFolds.add(candidate.startRow);
          expanded = true;
        }
      }
      remaining.unshift(...candidate.foldedRows);
    }
    assert(
      expanded,
      "A known unrendered line must belong to a collapsed fold.",
    );
    render();
    // `render()` reopens the fold through a signal write, so the rows it
    // reveals are mounted by Solid's flush rather than by the call. The yield
    // lets that flush run before the reopened row is looked up.
    await Promise.resolve();
    if (abortSignal.aborted || !root.isConnected) {
      return { state: "stopped" };
    }
    const row = renderedRow(target);
    return row === null ? { state: "missing" } : { state: "ready", row };
  }

  function applyMarkerState(lineNumber: HTMLElement): void {
    function decorateTriggerKind(
      trigger: HTMLButtonElement,
      markerKind: ReviewMarkerKind,
    ): void {
      trigger.dataset.reviewMarkerKind = markerKind;
      trigger.classList.toggle(
        "line-comment-trigger-commented",
        markerKind === "open" ||
          markerKind === "resolved" ||
          markerKind === "deleted",
      );
      trigger.classList.toggle(
        "line-comment-trigger-draft",
        markerKind === "draft",
      );
      trigger.classList.toggle(
        "line-comment-trigger-open",
        markerKind === "open",
      );
      trigger.classList.toggle(
        "line-comment-trigger-resolved",
        markerKind === "resolved",
      );
      trigger.classList.toggle(
        "line-comment-trigger-deleted",
        markerKind === "deleted",
      );
    }

    function createTrigger(
      markerKind: ReviewMarkerKind,
      side: Side,
      line: string,
    ): HTMLButtonElement {
      const trigger = document.createElement("button");
      trigger.type = "button";
      trigger.className = "line-comment-trigger";
      decorateTriggerKind(trigger, markerKind);
      const icon = document.createElement("span");
      icon.className = "line-comment-trigger-icon";
      icon.ariaHidden = "true";
      const label = document.createElement("span");
      label.className = "line-comment-trigger-label";
      trigger.append(icon, label);
      trigger.ariaLabel = `Review action on ${side === "left" ? "old" : "new"} line ${line}`;
      return trigger;
    }

    const host = lineNumber.querySelector(":scope > .line-comment-triggers");
    const side = lineNumber.dataset.linePinSide;
    const line = lineNumber.dataset.linePinLine;
    assert(
      host instanceof HTMLSpanElement &&
        (side === "left" || side === "right") &&
        line !== undefined &&
        /^[1-9]\d*$/u.test(line),
      "Rendered Comment marker must expose its exact line identity.",
    );
    const storedState = review.markerState(reviewBinding, side, Number(line));
    storedState.markers.forEach((marker, index) => {
      const current = host.children.item(index);
      let trigger: HTMLButtonElement;
      if (current instanceof HTMLButtonElement) {
        // A kind change re-decorates the existing button instead of replacing
        // it: the button may anchor the active Comment input, and replacing it
        // disconnects that anchor, which closes the input on the next
        // anchored-UI sweep (e.g. any FileCard header unmount while the file
        // lane is still loading).
        trigger = current;
        if (reviewMarkerKind(trigger) !== marker.kind) {
          decorateTriggerKind(trigger, marker.kind);
        }
      } else {
        trigger = createTrigger(marker.kind, side, line);
        if (current === null) {
          host.append(trigger);
        } else {
          current.replaceWith(trigger);
        }
      }
      const label = trigger.lastElementChild;
      assert(
        label instanceof HTMLSpanElement,
        "Rendered Comment trigger requires its visible label.",
      );
      let actionLabel: string;
      if (marker.kind === "draft") {
        actionLabel = "Draft";
      } else if (marker.kind === "new") {
        actionLabel = "Add comment";
      } else {
        actionLabel = `${marker.count} ${marker.kind === "resolved" ? "Resolved" : marker.kind === "deleted" ? "Deleted" : "Open"} Thread${marker.count === 1 ? "" : "s"}`;
      }
      const labelText =
        marker.kind === "open" ||
        marker.kind === "resolved" ||
        marker.kind === "deleted"
          ? String(marker.count)
          : actionLabel;
      if (label.textContent !== labelText) {
        label.textContent = labelText;
      }
      if (trigger.title !== actionLabel) {
        trigger.title = actionLabel;
      }
      const ariaLabel = `${actionLabel} on ${side === "left" ? "old" : "new"} line ${line}`;
      if (trigger.ariaLabel !== ariaLabel) {
        trigger.ariaLabel = ariaLabel;
      }
      const warning = "warning" in marker && marker.warning;
      if (
        trigger.classList.contains("line-comment-trigger-warning") !== warning
      ) {
        trigger.classList.toggle("line-comment-trigger-warning", warning);
      }
      if (trigger.disabled !== storedState.disabled) {
        trigger.disabled = storedState.disabled;
      }
    });
    while (host.children.length > storedState.markers.length) {
      host.lastElementChild?.remove();
    }
  }

  function refreshReviewMarkers(): void {
    for (const lineNumber of root.querySelectorAll<HTMLElement>(
      ".line-no[data-line-pin-line]",
    )) {
      applyMarkerState(lineNumber);
    }
  }

  function refreshChangedReviewMarkers(keys: ReadonlySet<string>): void {
    for (const key of keys) {
      // The key's shape is declared once beside its encoder, so this reader
      // cannot drift from it: a changed encoding fails here as a parse error
      // rather than as a stale hand-written arity check.
      const [leftPath, rightPath, bayKey, side, line] =
        LineMarkerKeySchema.parse(JSON.parse(key));
      if (
        leftPath !== reviewBinding.file.left_path ||
        rightPath !== reviewBinding.file.right_path ||
        bayKey !== reviewBinding.bay.bay_key
      ) {
        continue;
      }
      const matchingLines = root.querySelectorAll<HTMLElement>(
        `.line-no[data-line-pin-side="${side}"][data-line-pin-line="${line}"]`,
      );
      assert(
        matchingLines.length <= 1,
        "TextDiffGrid contains duplicate line-pin coordinates.",
      );
      const lineNumber = matchingLines[0];
      if (lineNumber === undefined) {
        continue;
      }
      applyMarkerState(lineNumber);
    }
  }

  function disableReviewMarkers(): void {
    for (const trigger of root.querySelectorAll<HTMLButtonElement>(
      ".line-comment-trigger",
    )) {
      trigger.classList.remove(
        "line-comment-trigger-commented",
        "line-comment-trigger-draft",
        "line-comment-trigger-open",
        "line-comment-trigger-resolved",
        "line-comment-trigger-deleted",
        "line-comment-trigger-warning",
      );
      trigger.disabled = true;
    }
  }

  // This grid's SVG filter combines touching moved-text rectangles into one
  // outline, retaining holes occupied by different move relationships or unassociated text.
  const moveOutlineId = `move-outline-${createUniqueId().replaceAll(":", "")}`;
  function paintMoveOutlines(): void {
    root.querySelector(".move-outlines")?.remove();
    const moved = root.querySelectorAll<HTMLElement>(".token-moved");
    if (moved.length === 0) {
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
    for (const token of moved) {
      const move = expect(
        token.closest<HTMLElement>("[data-move-key]"),
        "Move enclosures require the same endpoints as their hover details.",
      );
      const key = `${move.dataset.moveSide}:${move.dataset.moveKey}`;
      let group = groups.get(key);
      if (group === undefined) {
        group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        group.dataset.moveSide = expect(
          move.dataset.moveSide,
          "Move side is required.",
        );
        group.dataset.moveKey = expect(
          move.dataset.moveKey,
          "Move endpoints are required.",
        );
        group.setAttribute("filter", `url(#${moveOutlineId})`);
        group.style.setProperty(
          "--move-outline-filter",
          `url(#${moveOutlineId})`,
        );
        group.classList.toggle(
          "move-highlighted",
          move.classList.contains("move-highlighted"),
        );
        groups.set(key, group);
        svg.append(group);
      }
      const code = expect(
        token.closest<HTMLElement>(".line-code"),
        "Move tokens require a source code cell.",
      );
      const cell = code.getBoundingClientRect();
      const lineHeight = Number.parseFloat(getComputedStyle(code).lineHeight);
      for (const bounds of token.getClientRects()) {
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

  function afterRowsChanged(): void {
    paintMoveOutlines();
    setRowRevision((current) => current + 1);
    if (reviewMarkersFailed) {
      disableReviewMarkers();
    }
    const parsed = props.linePins.parseUrl();
    if (
      parsed.state === "valid" &&
      parsed.target.file.left_path === props.reviewFile.left_path &&
      parsed.target.file.right_path === props.reviewFile.right_path &&
      parsed.target.bay.bay_key === props.bayKey
    ) {
      restoreOldPin();
    }
  }

  const render = () => {
    const inputChanged = [
      props.displayName !== previousDisplayName,
      props.rows !== previousRows,
      props.foldHints !== previousFoldHints,
      props.leftLabel !== previousLeftLabel,
      props.rightLabel !== previousRightLabel,
      props.viewMode !== previousViewMode,
      props.aggressiveFolds !== previousAggressiveFolds,
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
    }

    const rows = addFoldRows(
      props.rows,
      props.foldHints,
      props.aggressiveFolds,
    );
    const fragment =
      props.viewMode === "inline"
        ? renderInlineRowsDom(
            rows,
            props.interleaveInlineRows,
            expandedFolds,
            props.fileIndex,
            props.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          )
        : renderSplitRowsDom(
            rows,
            props.leftLabel,
            props.rightLabel,
            expandedFolds,
            props.fileIndex,
            props.bayKey,
            0,
            review.closeAnchoredUi,
            afterRowsChanged,
          );
    review.closeAnchoredUi(root);
    root.replaceChildren(fragment);
    afterRowsChanged();
  };

  // Rebuild the exclusively owned row subtree when the renderer inputs read by
  // render() change. The effect lives with this component, owns no asynchronous
  // work, and needs no cleanup because each run replaces the prior DOM after
  // closing anchored review UI; component disposal removes the root itself.
  createEffect(render);
  onMount(() => {
    root.prepareLine_impl = prepareLine_impl;
    root.addEventListener("click", handleLineActivation);
    function highlightMove(event: PointerEvent) {
      const [entered, exited] = [event.target, event.relatedTarget].map(
        (target) => {
          const span =
            target instanceof Element
              ? target.closest<HTMLElement>(".token-moved")
              : null;
          return span !== null && root.contains(span) ? span : null;
        },
      );
      if (
        entered?.dataset.moveKey === exited?.dataset.moveKey &&
        entered?.dataset.moveSide === exited?.dataset.moveSide
      ) {
        return;
      }
      const hovered = event.type === "pointerout" ? exited : entered;
      const selected = new Set<string>();
      if (hovered !== null) {
        const key = expect(
          hovered.dataset.moveKey,
          "Move hover requires endpoints.",
        );
        const side = expect(
          hovered.dataset.moveSide,
          "Move hover requires a source side.",
        );
        const offset = side === "left" ? 0 : 4;
        const [startLine, startColumn, endLine, endColumn] = key
          .split(":")
          .map(Number)
          .slice(offset, offset + 4);
        // Origins can lie outside the parent's origin. Containment is evaluated
        // only on the side being hovered, then paired identities cross sides.
        for (const span of root.querySelectorAll<HTMLElement>(
          `.token-moved[data-move-side="${side}"]`,
        )) {
          const candidate = expect(
            span.dataset.moveKey,
            "Move hover requires endpoints.",
          );
          const [firstLine, firstColumn, lastLine, lastColumn] = candidate
            .split(":")
            .map(Number)
            .slice(offset, offset + 4);
          if (
            (firstLine > startLine ||
              (firstLine === startLine && firstColumn >= startColumn)) &&
            (lastLine < endLine ||
              (lastLine === endLine && lastColumn <= endColumn))
          ) {
            selected.add(candidate);
          }
        }
      }
      // Coverage never changes: hover emphasizes existing text and outlines for
      // the selected move and descendants, including their opposite-side pieces.
      for (const element of root.querySelectorAll<HTMLElement | SVGGElement>(
        ".token-moved, .move-outlines g",
      )) {
        element.classList.toggle(
          "move-highlighted",
          selected.has(
            expect(element.dataset.moveKey, "Move paint requires endpoints."),
          ),
        );
      }
    }
    // The observer lives with the mounted grid and repaints after wrapping or
    // font/layout changes. SVG is absolutely positioned and cannot resize it.
    const moveResize = new ResizeObserver(() => {
      paintMoveOutlines();
    });
    moveResize.observe(root);
    paintMoveOutlines();
    // Delegation covers fold replacements without per-token listeners. Pair
    // identity stays in mounted DOM; disposal removes both pointer listeners.
    root.addEventListener("pointerover", highlightMove);
    root.addEventListener("pointerout", highlightMove);
    onCleanup(() => {
      review.closeAnchoredUi(root);
      root.removeEventListener("click", handleLineActivation);
      root.removeEventListener("pointerover", highlightMove);
      root.removeEventListener("pointerout", highlightMove);
      moveResize.disconnect();
      Reflect.deleteProperty(root, "prepareLine_impl");
    });
  });

  return (
    <>
      <div ref={root} class="diff-lines" />
      <ErrorBoundary
        fallback={(error) => (
          <div class="review-marker-error" role="alert">
            Review markers unavailable: {presentError(error).message}
          </div>
        )}
      >
        <ReviewMarkerRefresh />
      </ErrorBoundary>
    </>
  );
}
