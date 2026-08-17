/**
 * Renders one validated notebook FileDiff inside a FullFile body.
 *
 * The module exports NotebookFile as the notebook-specific renderer boundary. It
 * renders notebook summary badges and cell framing and invokes DiffGrid for source regions.
 * Callers provide one complete immutable NotebookFileDiff and current renderer
 * inputs. It must not fetch cell details, store ChangeSet state, virtualize the
 * outer file, or merge notebook regions into ordinary text-file identity.
 */
import { For, Show, type JSX } from "solid-js";
import type {
  NotebookCell,
  NotebookFileDiff,
  ReviewFilePair,
} from "../api/api";
import { assert } from "../utils";
import type { DiffViewMode } from "./App";
import { DiffGrid } from "./diffGrid/DiffGrid";
import type { LinePins } from "./linePins";

/**
 * Defines every complete input required by the notebook renderer.
 *
 * File identity and row data come from the canonical file query. Presentation
 * inputs are reactive client state and must not be copied into notebook state.
 */
type NotebookFileProps = {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  backend_data: NotebookFileDiff;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
};

/**
 * Defines the complete inputs for one notebook-cell presentation.
 *
 * The owning notebook supplies its manifest-order file index and immutable cell.
 * The component does not fetch metadata/output details or invent region identity.
 */
type NotebookCellProps = {
  reviewFile: ReviewFilePair;
  fileIndex: number;
  fileDisplayName: string;
  cell: NotebookCell;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  linePins: LinePins;
};

/**
 * Renders notebook summary and every changed cell in backend order.
 *
 * Callers must provide the notebook-discriminated FileDiff. Empty cells retain
 * the established explicit message rather than being interpreted as an error.
 * Every rendered cell must supply one non-empty region key unique within the
 * file so exact line-pin coordinates cannot alias another cell or ordinary text.
 */
export function NotebookFile(props: NotebookFileProps): JSX.Element {
  const cellKeys = new Set<string>();
  for (const cell of props.backend_data.cells) {
    assert(
      cell.cell_key.length > 0,
      "Notebook cells require a non-empty line-pin region key.",
    );
    assert(
      !cellKeys.has(cell.cell_key),
      `Notebook contains duplicate cell key ${cell.cell_key}.`,
    );
    cellKeys.add(cell.cell_key);
  }

  return (
    <div class="notebook-file">
      <div class="notebook-summary">
        <span class="badge badge-neutral">
          {props.backend_data.summary.left_exists
            ? "left exists"
            : "left missing"}
        </span>
        <span class="badge badge-neutral">
          {props.backend_data.summary.right_exists
            ? "right exists"
            : "right missing"}
        </span>
        <span class="badge badge-neutral">
          {props.backend_data.summary.changed_cells} changed cell
          {props.backend_data.summary.changed_cells === 1 ? "" : "s"}
        </span>
        <Show when={props.backend_data.summary.notebook_metadata_changed}>
          <span class="badge badge-neutral">notebook metadata changed</span>
        </Show>
      </div>

      <div class="notebook-cells">
        <Show
          when={props.backend_data.cells.length > 0}
          fallback={
            <p class="file-placeholder">
              No changed cells detected for the selected notebook sides.
            </p>
          }
        >
          <For each={props.backend_data.cells}>
            {(cell) => (
              <NotebookCellView
                reviewFile={props.reviewFile}
                fileIndex={props.fileIndex}
                fileDisplayName={props.backend_data.display_name}
                cell={cell}
                view={props.view}
                aggressiveFolds={props.aggressiveFolds}
                linePins={props.linePins}
              />
            )}
          </For>
        </Show>
      </div>
    </div>
  );
}

/**
 * Renders one structural notebook cell and its source diff.
 *
 * The backend cell is complete and required. Metadata and output changes are
 * represented by badges while their richer raw/rendered bodies remain outside
 * this renderer's current backend contract.
 */
function NotebookCellView(props: NotebookCellProps): JSX.Element {
  /**
   * Maps the required backend cell-change variant to its established badge.
   *
   * Modified is the only remaining validated variant after added and removed;
   * the helper affects presentation only and does not reinterpret cell data.
   */
  const badgeClass = () => {
    if (props.cell.kind === "added") {
      return "badge-added";
    }
    if (props.cell.kind === "removed") {
      return "badge-removed";
    }
    return "badge-modified";
  };

  return (
    <article class="notebook-cell-card">
      <header class="notebook-cell-header">
        <div>
          <h3>
            {props.cell.kind.toUpperCase()} {props.cell.cell_type} cell
          </h3>
          <p>
            Cell ID: {props.cell.cell_id ?? "missing"} · left #
            {props.cell.left_index ?? "—"} · right #
            {props.cell.right_index ?? "—"}
          </p>
        </div>
        <div class="badge-row">
          <span class={`badge ${badgeClass()}`}>{props.cell.kind}</span>
          <Show when={props.cell.metadata_changed}>
            <span class="badge badge-neutral">metadata changed</span>
          </Show>
          <Show when={props.cell.outputs_changed}>
            <span class="badge badge-neutral">outputs changed</span>
          </Show>
          <Show when={!props.cell.source_changed}>
            <span class="badge badge-neutral">source unchanged</span>
          </Show>
        </div>
      </header>

      <section class="notebook-section">
        <p class="notebook-section-heading">Cell source</p>
        <DiffGrid
          reviewFile={props.reviewFile}
          fileIndex={props.fileIndex}
          displayName={props.fileDisplayName}
          region={props.cell.cell_key}
          leftLabel="Left source"
          rightLabel="Right source"
          rows={props.cell.source_rows}
          foldHints={props.cell.source_fold_hints}
          viewMode={props.view}
          aggressiveFolds={props.aggressiveFolds}
          combineInsertOnlyReplaceRows={false}
          linePins={props.linePins}
        />
      </section>
    </article>
  );
}
