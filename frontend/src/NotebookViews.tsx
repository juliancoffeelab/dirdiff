import { For, Show, createSignal } from "solid-js";
import { useQueryClient } from "@tanstack/solid-query";
import type {
  DiffParams,
  DiffRow,
  FileEntry,
  FoldHint,
  NotebookCellEntry,
  NotebookSection,
} from "./api";
import { fetchNotebookSection } from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import { notebookCells, notebookSummary } from "./model";

export function NotebookFile(props: {
  file: FileEntry;
  diffParams: DiffParams;
  diffViewMode: DiffViewMode;
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

      <Show when={summary().notebook_metadata_changed}>
        <NotebookDetails
          file={props.file}
          diffParams={props.diffParams}
          title={notebookSectionSummary("Notebook metadata diff", {
            renderMode: notebookMetadataRenderMode(props.file),
            truncatedRows: notebookMetadataTruncatedRows(props.file),
          })}
          section="notebook-metadata"
          leftLabel="Left notebook metadata"
          rightLabel="Right notebook metadata"
          diffViewMode={props.diffViewMode}
        />
      </Show>

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
                file={props.file}
                diffParams={props.diffParams}
                cell={cell}
                diffViewMode={props.diffViewMode}
              />
            )}
          </For>
        </Show>
      </div>
    </div>
  );
}

function NotebookCell(props: {
  file: FileEntry;
  diffParams: DiffParams;
  cell: NotebookCellEntry;
  diffViewMode: DiffViewMode;
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
        heading="Cell source"
        rows={cell().source_rows}
        foldHints={cell().source_fold_hints}
        leftLabel="Left source"
        rightLabel="Right source"
        renderMode={cell().source_render_mode}
        truncatedRows={truncatedRowsValue(cell().source_truncated_rows)}
        diffViewMode={props.diffViewMode}
      />

      <Show when={cell().metadata_changed}>
        <NotebookDetails
          file={props.file}
          diffParams={props.diffParams}
          title={notebookSectionSummary("Cell metadata diff", {
            renderMode: cell().metadata_render_mode,
            truncatedRows: truncatedRowsValue(cell().metadata_truncated_rows),
          })}
          section="cell-metadata"
          cellKey={cell().cell_key}
          leftLabel="Left metadata"
          rightLabel="Right metadata"
          diffViewMode={props.diffViewMode}
        />
      </Show>

      <Show when={cell().outputs_changed}>
        <NotebookDetails
          file={props.file}
          diffParams={props.diffParams}
          title={notebookSectionSummary("Cell outputs diff", {
            renderMode: cell().outputs_render_mode,
            truncatedRows: truncatedRowsValue(cell().outputs_truncated_rows),
          })}
          section="cell-outputs"
          cellKey={cell().cell_key}
          leftLabel="Left outputs"
          rightLabel="Right outputs"
          diffViewMode={props.diffViewMode}
        />
      </Show>
    </article>
  );
}

function NotebookDetails(props: {
  file: FileEntry;
  diffParams: DiffParams;
  title: string;
  section: string;
  cellKey?: string;
  leftLabel: string;
  rightLabel: string;
  diffViewMode: DiffViewMode;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = createSignal(false);
  const [loading, setLoading] = createSignal(false);
  const [error, setError] = createSignal("");
  const [section, setSection] = createSignal<NotebookSection | null>(null);

  const load = async () => {
    const diffParams = props.diffParams;
    if (section() !== null || loading()) {
      return;
    }
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const payload = await queryClient.fetchQuery({
        queryKey: notebookSectionQueryKey(
          diffParams,
          props.file,
          props.section,
          notebookSectionCellKey(props.cellKey),
        ),
        queryFn: ({ signal }) =>
          fetchNotebookSection(
            diffParams,
            props.file,
            {
              section: props.section,
              cellKey: props.cellKey,
            },
            signal,
          ),
        staleTime: 0,
      });
      setSection(payload);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Failed to load notebook section.",
      );
    } finally {
      setLoading(false);
    }
  };

  const loadedSection = () => {
    const current = section();
    if (current === null) {
      throw new Error("Notebook section is not loaded.");
    }
    return current;
  };

  return (
    <details
      class="notebook-details"
      open={open()}
      onToggle={(event) => {
        const nextOpen = event.currentTarget.open;
        setOpen(nextOpen);
        if (nextOpen) {
          void load();
        }
      }}
    >
      <summary>{props.title}</summary>
      <Show when={loading()}>
        <p class="notebook-details-message">Loading...</p>
      </Show>
      <Show when={error() !== ""}>
        <p class="file-placeholder error-text">{error()}</p>
      </Show>
      <Show when={section() !== null}>
        <NotebookSectionView
          rows={loadedSection().rows}
          foldHints={loadedSection().fold_hints}
          leftLabel={props.leftLabel}
          rightLabel={props.rightLabel}
          renderMode={loadedSection().render_mode}
          truncatedRows={loadedSection().truncated_rows}
          diffViewMode={props.diffViewMode}
        />
      </Show>
    </details>
  );
}

function NotebookSectionView(props: {
  heading?: string;
  rows: DiffRow[];
  foldHints: FoldHint[];
  leftLabel: string;
  rightLabel: string;
  renderMode?: "plain" | null;
  truncatedRows?: number | null;
  diffViewMode: DiffViewMode;
}) {
  const file = (): FileEntry => ({
    display_name: notebookSectionDisplayName(props),
    file_kind: { type: "git", status: "modified" },
    left_path: null,
    right_path: null,
    left_label: props.leftLabel,
    right_label: props.rightLabel,
    rows: props.rows,
    fold_hints: props.foldHints,
    default_expanded: true,
  });

  return (
    <section class="notebook-section">
      <Show when={props.heading !== undefined && props.heading !== ""}>
        <p class="notebook-section-heading">{props.heading}</p>
      </Show>
      <DiffGrid file={file()} viewMode={props.diffViewMode} />
      <Show
        when={
          props.renderMode === "plain" ||
          (props.truncatedRows !== null &&
            props.truncatedRows !== undefined &&
            props.truncatedRows > 0)
        }
      >
        <p class="notebook-section-note">
          {props.renderMode === "plain" ? "plain render" : ""}
          {props.renderMode === "plain" && truncatedRows(props) > 0
            ? " · "
            : ""}
          {truncatedRows(props) > 0 ? `truncated ${truncatedRows(props)}` : ""}
        </p>
      </Show>
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

function notebookMetadataRenderMode(file: FileEntry): "plain" | null {
  if (file.notebook_metadata_render_mode === undefined) {
    return null;
  }
  return file.notebook_metadata_render_mode;
}

function notebookMetadataTruncatedRows(file: FileEntry): number {
  if (file.notebook_metadata_truncated_rows === undefined) {
    return 0;
  }
  return file.notebook_metadata_truncated_rows;
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

function truncatedRowsValue(rows: number | null): number {
  if (rows === null) {
    return 0;
  }
  return rows;
}

function notebookSectionCellKey(cellKey: string | undefined): string | null {
  if (cellKey === undefined) {
    return null;
  }
  return cellKey;
}

function truncatedRows(props: { truncatedRows?: number | null }): number {
  if (props.truncatedRows === null || props.truncatedRows === undefined) {
    return 0;
  }
  return props.truncatedRows;
}

function notebookSectionSummary(
  label: string,
  details: { renderMode?: "plain" | null; truncatedRows?: number | null },
): string {
  const parts = [label];
  if (details.renderMode === "plain") {
    parts.push("plain render");
  }
  if (details.truncatedRows !== null && details.truncatedRows !== undefined) {
    parts.push(`truncated ${details.truncatedRows}`);
  }
  return parts.join(" · ");
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

function notebookSectionQueryKey(
  diffParams: DiffParams,
  entry: FileEntry,
  section: string,
  cellKey: string | null,
) {
  const diffIdentityParts =
    diffParams.mode === "preset"
      ? [diffParams.mode, diffParams.preset]
      : diffParams.mode === "branch-review"
        ? [diffParams.mode, diffParams.base_branch, diffParams.review_branch]
        : [
            diffParams.mode,
            diffParams.left,
            diffParams.right,
            diffParams.mode === "head",
          ];
  return [
    "notebook-section",
    diffParams.repo_id,
    diffParams.engine,
    ...diffIdentityParts,
    entry.left_path,
    entry.right_path,
    section,
    cellKey,
  ] as const;
}
