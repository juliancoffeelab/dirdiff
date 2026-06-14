export type DiffMode =
  | "files"
  | "staged"
  | "against-head"
  | "refs"
  | "branch-review"
  | "preset";
export type DiffEngine = "dirdiff" | "git" | "difftastic";

export type RefChoices = {
  builtins: string[];
  locals: string[];
  remotes: string[];
  remote_names: string[];
};

export type Defaults = {
  engine: DiffEngine;
  mode: DiffMode;
  left: string;
  right: string;
  base_branch: string | null;
  review_branch: string | null;
  ref_choices: RefChoices;
  repo_available: boolean;
};

export type DiffRequest = {
  engine: DiffEngine;
  mode: DiffMode;
  left: string;
  right: string;
  base_branch: string | null;
  review_branch: string | null;
  show_untracked: boolean;
};

export type Summary = {
  changed_files: number;
  added_files: number;
  removed_files: number;
  updated_files: number;
  changed_lines: number;
  modified_lines: number;
  added_lines: number;
  removed_lines: number;
  skipped_files: number;
  changed_cells?: number;
  added_cells?: number;
  removed_cells?: number;
  modified_cells?: number;
};

export type RepoPayload = {
  display_name: string;
  mode: "repo";
  left_label: string;
  right_label: string;
  summary: Summary;
};

export type RepoDiffPayload = RepoPayload & {
  files: FileEntry[];
};

export type FileSummary = {
  changed_lines: number;
  modified_lines: number;
  added_lines: number;
  removed_lines: number;
  left_exists: boolean;
  right_exists: boolean;
};

export type NotebookSummary = FileSummary & {
  changed_cells: number;
  added_cells: number;
  removed_cells: number;
  modified_cells: number;
  notebook_metadata_changed: boolean;
};

export type RowStatus =
  | "equal"
  | "replace"
  | "insert"
  | "delete"
  | "fold"
  | "elided";

export type InlineToken = {
  text: string;
  is_ws: boolean;
  status: "unchanged" | "replace" | "insert" | "delete";
};

export type SyntaxSpan = {
  start: number;
  end: number;
  classes: string[];
};

export type DiffRow = {
  status: RowStatus;
  left_no: number | null;
  right_no: number | null;
  left_text: string | null;
  right_text: string | null;
  left_tokens: InlineToken[];
  right_tokens: InlineToken[];
  left_syntax: SyntaxSpan[];
  right_syntax: SyntaxSpan[];
  count?: number | null;
  foldedRows?: DiffRow[];
  label?: string | null;
};

export type FoldHint = {
  start_row: number;
  end_row: number;
  label: string;
};

export type GitChangeType = "modify" | "add" | "delete" | "rename" | "copy";

export type FileKind =
  | {
      type: "git";
      status: "modified" | "added" | "deleted" | "renamed" | "copied";
    }
  | {
      type: "untracked";
    };

export type LazyReason =
  | "too_big"
  | "generated"
  | "deleted"
  | "untracked"
  | "pure_renamed";

export type EngineWarning = {
  type: "difftastic_graph_limit";
  message: string;
};

export type FileEntry = {
  display_name?: string;
  mode?: "git";
  left_label?: string;
  right_label?: string;
  summary?: FileSummary | NotebookSummary;
  file_kind: FileKind;
  left_path: string | null;
  right_path: string | null;
  changed_lines?: number | null;
  added_lines?: number | null;
  removed_lines?: number | null;
  rows?: DiffRow[];
  fold_hints?: FoldHint[];
  engine_warning?: EngineWarning;
  lazy?: LazyReason | null;
  default_expanded?: boolean;
  render_kind?: "notebook";
  notebook_metadata_rows?: DiffRow[];
  notebook_metadata_changed_lines?: number;
  notebook_metadata_hunk_count?: number;
  notebook_metadata_lazy?: boolean;
  notebook_metadata_render_mode?: "plain" | null;
  notebook_metadata_truncated_rows?: number;
  cells?: NotebookCellEntry[];
};

export type NotebookCellEntry = {
  kind: "added" | "removed" | "modified";
  cell_type: string;
  cell_id: string | null;
  cell_key: string;
  left_index: number | null;
  right_index: number | null;
  left_id: string | null;
  right_id: string | null;
  source_changed: boolean;
  metadata_changed: boolean;
  outputs_changed: boolean;
  source_rows: DiffRow[];
  source_changed_lines: number;
  source_modified_lines: number;
  source_added_lines: number;
  source_removed_lines: number;
  source_fold_hints: FoldHint[];
  metadata_rows: DiffRow[];
  outputs_rows: DiffRow[];
  metadata_changed_lines: number;
  metadata_modified_lines: number;
  metadata_added_lines: number;
  metadata_removed_lines: number;
  metadata_hunk_count: number;
  metadata_lazy: boolean;
  outputs_changed_lines: number;
  outputs_modified_lines: number;
  outputs_added_lines: number;
  outputs_removed_lines: number;
  outputs_hunk_count: number;
  outputs_lazy: boolean;
  source_render_mode: "plain" | null;
  source_truncated_rows: number | null;
  metadata_render_mode: "plain" | null;
  metadata_truncated_rows: number | null;
  outputs_render_mode: "plain" | null;
  outputs_truncated_rows: number | null;
};

export type NotebookSection = {
  section: string;
  cell_key: string | null;
  left_index: number | null;
  right_index: number | null;
  left_label: string;
  right_label: string;
  rows: DiffRow[];
  render_mode: "plain" | null;
  truncated_rows: number;
  fold_hints: FoldHint[];
};

export async function fetchDefaults(): Promise<Defaults> {
  const response = await fetch("/api/defaults");
  if (!response.ok) {
    throw new Error(`Failed to load defaults: ${response.status}`);
  }
  return (await response.json()) as Defaults;
}

function diffRequestParams(request: DiffRequest): URLSearchParams {
  const params = new URLSearchParams();
  params.set("engine", request.engine);
  params.set("mode", request.mode === "against-head" ? "files" : request.mode);
  if (request.left) {
    params.set("left", request.left);
  }
  if (request.right) {
    params.set("right", request.right);
  }
  if (request.base_branch) {
    params.set("base_branch", request.base_branch);
  }
  if (request.review_branch) {
    params.set("review_branch", request.review_branch);
  }
  if (request.show_untracked) {
    params.set("show_untracked", "true");
  }
  return params;
}

export async function fetchDiff(
  request: DiffRequest,
  signal?: AbortSignal,
): Promise<RepoDiffPayload> {
  const params = diffRequestParams(request);
  const response = await fetch(`/api/diff?${params.toString()}`, { signal });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Failed to load diff.");
  }
  return payload as RepoDiffPayload;
}

export async function fetchFileDiff(
  request: DiffRequest,
  entry: FileEntry,
): Promise<FileEntry> {
  const params = diffRequestParams(request);
  if (entry.left_path) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path) {
    params.set("right_path", entry.right_path);
  }
  if (entry.display_name) {
    params.set("display_name", entry.display_name);
  }
  params.set("change_type", changeTypeForFileKind(entry.file_kind));
  params.set("file_kind", entry.file_kind.type);

  const response = await fetch(`/api/file-diff?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Failed to load file diff.");
  }
  return payload as FileEntry;
}

function changeTypeForFileKind(fileKind: FileKind): GitChangeType {
  if (fileKind.type === "untracked") {
    return "add";
  }
  switch (fileKind.status) {
    case "added":
      return "add";
    case "deleted":
      return "delete";
    case "renamed":
      return "rename";
    case "copied":
      return "copy";
    case "modified":
    default:
      return "modify";
  }
}

export async function fetchNotebookSection(
  request: DiffRequest,
  entry: FileEntry,
  options: { section: string; cellKey?: string | null },
): Promise<NotebookSection> {
  const params = diffRequestParams(request);
  if (entry.left_path) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path) {
    params.set("right_path", entry.right_path);
  }
  params.set("section", options.section);
  if (options.cellKey) {
    params.set("cell_key", options.cellKey);
  }

  const response = await fetch(`/api/notebook-section?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Failed to load notebook section.");
  }
  return payload as NotebookSection;
}
