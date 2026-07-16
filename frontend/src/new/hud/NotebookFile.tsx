/**
 * Renders one validated notebook FileDiff inside a FullFile body.
 *
 * The module exports NotebookFile as the notebook-specific renderer boundary. It
 * owns notebook summary badges, cell framing, and source-region DiffGrid calls.
 * Callers provide one complete immutable NotebookFileDiff and current renderer
 * inputs. It must not fetch cell details, own ChangeSet state, virtualize the
 * outer file, or collapse notebook regions into ordinary text-file identity.
 */
import { For, Show, type JSX } from "solid-js";
import type { NotebookCell, NotebookFileDiff } from "../api/api";
import type { DiffViewMode } from "./App";
import { DiffGrid } from "./DiffGrid";

/**
 * Defines every complete input required by the notebook renderer.
 *
 * File identity and row data come from the canonical file query. Presentation
 * inputs are reactive client state and must not be copied into notebook state.
 */
type NotebookFileProps = {
  fileIndex: number;
  file: NotebookFileDiff;
  view: DiffViewMode;
  aggressiveFolds: boolean;
};

/**
 * Defines the complete inputs for one notebook-cell presentation.
 *
 * The owning notebook supplies its manifest-order file index and immutable cell.
 * The component does not fetch metadata/output details or invent region identity.
 */
type NotebookCellProps = {
  fileIndex: number;
  cell: NotebookCell;
  view: DiffViewMode;
  aggressiveFolds: boolean;
};

/**
 * Renders notebook summary and every changed cell in backend order.
 *
 * Callers must provide the notebook-discriminated FileDiff. Empty cells retain
 * the established explicit message rather than being interpreted as an error.
 */
export function NotebookFile(props: NotebookFileProps): JSX.Element {
  return (
    <div class="notebook-file">
      <div class="notebook-summary">
        <span class="badge badge-neutral">
          {props.file.summary.left_exists ? "left exists" : "left missing"}
        </span>
        <span class="badge badge-neutral">
          {props.file.summary.right_exists ? "right exists" : "right missing"}
        </span>
        <span class="badge badge-neutral">
          {props.file.summary.changed_cells} changed cell
          {props.file.summary.changed_cells === 1 ? "" : "s"}
        </span>
        <Show when={props.file.summary.notebook_metadata_changed}>
          <span class="badge badge-neutral">notebook metadata changed</span>
        </Show>
      </div>

      <div class="notebook-cells">
        <Show
          when={props.file.cells.length > 0}
          fallback={
            <p class="file-placeholder">
              No changed cells detected for the selected notebook sides.
            </p>
          }
        >
          <For each={props.file.cells}>
            {(cell) => (
              <NotebookCellView
                fileIndex={props.fileIndex}
                cell={cell}
                view={props.view}
                aggressiveFolds={props.aggressiveFolds}
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
          fileIndex={props.fileIndex}
          displayName="Cell source"
          leftLabel="Left source"
          rightLabel="Right source"
          rows={props.cell.source_rows}
          foldHints={props.cell.source_fold_hints}
          viewMode={props.view}
          aggressiveFolds={props.aggressiveFolds}
          collapseInsertOnlyReplaceRows={false}
        />
      </section>
    </article>
  );
}
