import {
  Show,
  createEffect,
  createSignal,
  onCleanup,
  onMount,
  untrack,
} from "solid-js";
import {
  type DiffRow,
  type FoldHint,
  type ReviewFilePair,
} from "../../../../api/api";
import type { DiffViewMode } from "../../../App";
import type { LinePins } from "../../../linePins";
import { useNavigation } from "../../../navigation";
import { presentError, useToasts } from "../../../../comp/Toasts";
import { useReview } from "../../../review/Review";
import { createTextGrid, type TextGrid } from "./textGrid";

export function TextDiffGrid(props: {
  reviewFile: ReviewFilePair;
  fileIndex: number;
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
  // Receives the grid once its rows are mounted and null before they go.
  onGrid: (grid: TextGrid | null) => void;
}) {
  let frame!: HTMLDivElement;
  let root!: HTMLDivElement;
  const review = useReview();
  const navigation = useNavigation();
  const toast = useToasts();
  const [markerFailure, setMarkerFailure] = createSignal<{
    error: unknown;
  } | null>(null);

  // The grid lives exactly as long as its row host. Both effects are created
  // after it, so neither runs against a missing grid.
  onMount(() => {
    const grid = createTextGrid({
      frame,
      root,
      // Fixed for this grid: a FileCard keeps its manifest index, and each bay
      // mounts its own grid.
      reviewFile: props.reviewFile,
      fileIndex: props.fileIndex,
      bayKey: props.bayKey,
      linePins: props.linePins,
      review,
      navigate: navigation.navigate,
      changeSetRoot: navigation.root,
      showError: toast.showError,
      onMarkerFailure: (error) => setMarkerFailure({ error }),
    });
    // Only these inputs rebuild the rows. Painting reads review and URL
    // state, which must not subscribe the rebuild.
    createEffect(() => {
      const input = {
        rows: props.rows,
        foldHints: props.foldHints,
        interleaveInlineRows: props.interleaveInlineRows,
        leftLabel: props.leftLabel,
        rightLabel: props.rightLabel,
        view: props.viewMode,
        aggressiveFolds: props.aggressiveFolds,
      };
      untrack(() => grid.render(input));
    });
    // Every marker read, including the revision that subscribes this effect,
    // happens inside the grid's marker boundary.
    createEffect(() => {
      grid.paintMarkers();
    });
    props.onGrid(grid);
    onCleanup(() => {
      props.onGrid(null);
      grid.dispose();
    });
  });

  return (
    <div
      ref={frame}
      class="diff-grid"
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
          has no rows to show, so this strip says so where they would be. */}
      <Show when={props.rows.length === 0}>
        <div class="diff-empty-strip">(empty)</div>
      </Show>
      <div ref={root} class="diff-lines" />
      <Show when={markerFailure()} keyed>
        {(failure) => (
          <div class="review-marker-error" role="alert">
            Review markers unavailable: {presentError(failure.error).message}
          </div>
        )}
      </Show>
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
