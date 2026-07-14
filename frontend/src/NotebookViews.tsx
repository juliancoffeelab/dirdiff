import { For, Show } from "solid-js";
import type {
  DiffRow,
  FileEntry,
  FoldHint,
  NotebookSummary,
  NotebookCellEntry,
} from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import { fileDisplayName } from "./fileUtils";
import type { RenderedFileEntry } from "./fileUtils";

function notebookSummary(entry: FileEntry): NotebookSummary {
  const summary = entry.summary;
  if (summary === undefined || !("changed_cells" in summary)) {
    throw new Error(`${fileDisplayName(entry)} is missing notebook summary.`);
  }
  return summary;
}

function notebookCells(entry: FileEntry) {
  if (entry.cells === undefined) {
    throw new Error(`${fileDisplayName(entry)} is missing notebook cells.`);
  }
  return entry.cells;
}

export function NotebookFile(props: {
  fileIndex: number;
  file: RenderedFileEntry;
  diffViewMode: DiffViewMode;
  aggressiveFolds: boolean;
}) {
  const summary = () => notebookSummary(props.file);
  const cells = () => notebookCells(props.file);

  return (
    <div class="notebook-file">
      <div class="notebook-summary">
        <span class="badge badge-neutral">
          {summary().left_exists ? "left exists" : "left missing"}
        </span>
        <span class="badge badge-neutral">
          {summary().right_exists ? "right exists" : "right missing"}
        </span>
        <span class="badge badge-neutral">
          {summary().changed_cells} changed cell
          {summary().changed_cells === 1 ? "" : "s"}
        </span>
        <Show when={summary().notebook_metadata_changed}>
          <span class="badge badge-neutral">notebook metadata changed</span>
        </Show>
      </div>

      <div class="notebook-cells">
        <Show
          when={cells().length > 0}
          fallback={
            <p class="file-placeholder">
              No changed cells detected for the selected notebook sides.
            </p>
          }
        >
          <For each={cells()}>
            {(cell) => (
              <NotebookCell
                fileIndex={props.fileIndex}
                cell={cell}
                diffViewMode={props.diffViewMode}
                aggressiveFolds={props.aggressiveFolds}
              />
            )}
          </For>
        </Show>
      </div>
    </div>
  );
}

function NotebookCell(props: {
  fileIndex: number;
  cell: NotebookCellEntry;
  diffViewMode: DiffViewMode;
  aggressiveFolds: boolean;
}) {
  const cell = () => props.cell;
  const leftIndex = () => notebookCellIndex(cell().left_index);
  const rightIndex = () => notebookCellIndex(cell().right_index);

  return (
    <article class="notebook-cell-card">
      <header class="notebook-cell-header">
        <div>
          <h3>
            {cell().kind.toUpperCase()} {cell().cell_type} cell
          </h3>
          <p>
            Cell ID: {notebookCellId(cell())} · left #{leftIndex()} · right #
            {rightIndex()}
          </p>
        </div>
        <div class="badge-row">
          <span class={`badge ${notebookCellKindBadgeClass(cell().kind)}`}>
            {cell().kind}
          </span>
          <Show when={cell().metadata_changed}>
            <span class="badge badge-neutral">metadata changed</span>
          </Show>
          <Show when={cell().outputs_changed}>
            <span class="badge badge-neutral">outputs changed</span>
          </Show>
          <Show when={!cell().source_changed}>
            <span class="badge badge-neutral">source unchanged</span>
          </Show>
        </div>
      </header>

      <NotebookSectionView
        fileIndex={props.fileIndex}
        heading="Cell source"
        rows={cell().source_rows}
        foldHints={cell().source_fold_hints}
        leftLabel="Left source"
        rightLabel="Right source"
        diffViewMode={props.diffViewMode}
        aggressiveFolds={props.aggressiveFolds}
      />
    </article>
  );
}

function NotebookSectionView(props: {
  fileIndex: number;
  heading?: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  leftLabel: string;
  rightLabel: string;
  diffViewMode: DiffViewMode;
  aggressiveFolds: boolean;
}) {
  return (
    <section class="notebook-section">
      <Show when={props.heading !== undefined && props.heading !== ""}>
        <p class="notebook-section-heading">{props.heading}</p>
      </Show>
      <DiffGrid
        fileIndex={props.fileIndex}
        displayName={notebookSectionDisplayName(props)}
        leftLabel={props.leftLabel}
        rightLabel={props.rightLabel}
        rows={props.rows}
        foldHints={props.foldHints}
        viewMode={props.diffViewMode}
        aggressiveFolds={props.aggressiveFolds}
      />
    </section>
  );
}

function notebookSectionDisplayName(props: {
  heading?: string;
  leftLabel: string;
  rightLabel: string;
}): string {
  if (props.heading !== undefined && props.heading.length > 0) {
    return props.heading;
  }
  return `${props.leftLabel} vs ${props.rightLabel}`;
}

function notebookCellIndex(index: number | null): string {
  if (index === null) {
    return "—";
  }
  return String(index);
}

function notebookCellId(cell: NotebookCellEntry): string {
  if (cell.cell_id === null) {
    return "missing";
  }
  return cell.cell_id;
}

function notebookCellKindBadgeClass(kind: NotebookCellEntry["kind"]): string {
  if (kind === "added") {
    return "badge-added";
  }
  if (kind === "removed") {
    return "badge-removed";
  }
  return "badge-modified";
}
