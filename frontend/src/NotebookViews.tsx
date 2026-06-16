import { For, Show, createSignal } from "solid-js";
import { useQueryClient } from "@tanstack/solid-query";
import type {
  DiffRequest,
  FileEntry,
  NotebookCellEntry,
  NotebookSection,
} from "./api";
import { fetchNotebookSection } from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import { isNotebookSummary } from "./model";

export function NotebookFile(props: {
  file: FileEntry;
  request: DiffRequest | null;
  diffViewMode: DiffViewMode;
}) {
  const notebookSummary = () =>
    isNotebookSummary(props.file.summary) ? props.file.summary : null;
  const summary = () => props.file.summary;
  const changedCells = () => notebookSummary()?.changed_cells ?? 0;
  const cells = () => props.file.cells ?? [];

  return (
    <div class="notebook-file">
      <div class="notebook-summary">
        <span class="badge badge-neutral">
          {summary()?.left_exists === true ? "left exists" : "left missing"}
        </span>
        <span class="badge badge-neutral">
          {summary()?.right_exists === true ? "right exists" : "right missing"}
        </span>
        <span class="badge badge-neutral">
          {changedCells()} changed cell{changedCells() === 1 ? "" : "s"}
        </span>
        <Show when={notebookSummary()?.notebook_metadata_changed}>
          <span class="badge badge-neutral">notebook metadata changed</span>
        </Show>
      </div>

      <Show when={notebookSummary()?.notebook_metadata_changed}>
        <NotebookDetails
          file={props.file}
          request={props.request}
          title={notebookSectionSummary("Notebook metadata diff", {
            renderMode: props.file.notebook_metadata_render_mode ?? null,
            truncatedRows: props.file.notebook_metadata_truncated_rows ?? 0,
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
                request={props.request}
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
  request: DiffRequest | null;
  cell: NotebookCellEntry;
  diffViewMode: DiffViewMode;
}) {
  const cell = () => props.cell;
  const leftIndex = () => cell().left_index ?? "—";
  const rightIndex = () => cell().right_index ?? "—";

  return (
    <article class="notebook-cell-card">
      <header class="notebook-cell-header">
        <div>
          <h3>
            {cell().kind.toUpperCase()} {cell().cell_type} cell
          </h3>
          <p>
            Cell ID: {cell().cell_id ?? "missing"} · left #{leftIndex()} · right
            #{rightIndex()}
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
        truncatedRows={cell().source_truncated_rows ?? 0}
        diffViewMode={props.diffViewMode}
      />

      <Show when={cell().metadata_changed}>
        <NotebookDetails
          file={props.file}
          request={props.request}
          title={notebookSectionSummary("Cell metadata diff", {
            renderMode: cell().metadata_render_mode,
            truncatedRows: cell().metadata_truncated_rows ?? 0,
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
          request={props.request}
          title={notebookSectionSummary("Cell outputs diff", {
            renderMode: cell().outputs_render_mode,
            truncatedRows: cell().outputs_truncated_rows ?? 0,
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
  request: DiffRequest | null;
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
    const request = props.request;
    if (request === null || section() !== null || loading()) {
      return;
    }
    setOpen(true);
    setLoading(true);
    setError("");
    try {
      const payload = await queryClient.fetchQuery({
        queryKey: notebookSectionQueryKey(
          request,
          props.file,
          props.section,
          props.cellKey ?? null,
        ),
        queryFn: ({ signal }) =>
          fetchNotebookSection(
            request,
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
      <Show when={error()}>
        <p class="file-placeholder error-text">{error()}</p>
      </Show>
      <Show when={section()}>
        {(payload) => (
          <NotebookSectionView
            rows={payload().rows}
            foldHints={payload().fold_hints}
            leftLabel={props.leftLabel}
            rightLabel={props.rightLabel}
            renderMode={payload().render_mode}
            truncatedRows={payload().truncated_rows}
            diffViewMode={props.diffViewMode}
          />
        )}
      </Show>
    </details>
  );
}

function NotebookSectionView(props: {
  heading?: string;
  rows: FileEntry["rows"];
  foldHints: FileEntry["fold_hints"];
  leftLabel: string;
  rightLabel: string;
  renderMode?: "plain" | null;
  truncatedRows?: number | null;
  diffViewMode: DiffViewMode;
}) {
  const file = (): FileEntry => ({
    file_kind: { type: "git", status: "modified" },
    left_path: null,
    right_path: null,
    left_label: props.leftLabel,
    right_label: props.rightLabel,
    rows: props.rows ?? [],
    fold_hints: props.foldHints ?? [],
    default_expanded: true,
  });

  return (
    <section class="notebook-section">
      <Show when={props.heading}>
        <p class="notebook-section-heading">{props.heading}</p>
      </Show>
      <DiffGrid file={file()} viewMode={props.diffViewMode} />
      <Show when={props.renderMode === "plain" || (props.truncatedRows ?? 0)}>
        <p class="notebook-section-note">
          {props.renderMode === "plain" ? "plain render" : ""}
          {props.renderMode === "plain" && (props.truncatedRows ?? 0)
            ? " · "
            : ""}
          {(props.truncatedRows ?? 0) ? `truncated ${props.truncatedRows}` : ""}
        </p>
      </Show>
    </section>
  );
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
  request: DiffRequest,
  entry: FileEntry,
  section: string,
  cellKey: string | null,
) {
  return [
    "notebook-section",
    request.repo_id,
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
    entry.left_path,
    entry.right_path,
    section,
    cellKey,
  ] as const;
}
