export type DiffMode =
  | "files"
  | "staged"
  | "against-head"
  | "refs"
  | "branch-review";

export type RefChoices = {
  builtins: string[];
  locals: string[];
  remotes: string[];
  remote_names: string[];
};

export type Defaults = {
  mode: DiffMode;
  left: string;
  right: string;
  base_branch: string | null;
  review_branch: string | null;
  ref_choices: RefChoices;
  repo_available: boolean;
};

export type DiffRequest = {
  mode: DiffMode;
  left: string;
  right: string;
  base_branch: string | null;
  review_branch: string | null;
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
};

export type RepoPayload = {
  display_name: string;
  mode: "repo";
  left_label: string;
  right_label: string;
  summary: Summary;
};

export type FileSummary = {
  changed_lines: number;
  modified_lines: number;
  added_lines: number;
  removed_lines: number;
  left_exists: boolean;
  right_exists: boolean;
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
  changed: boolean;
  is_ws: boolean;
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

export type FileEntry = {
  display_name?: string;
  mode?: "git";
  left_label?: string;
  right_label?: string;
  summary?: FileSummary;
  change_type: "modify" | "add" | "delete" | "rename" | "copy" | null;
  left_path: string | null;
  right_path: string | null;
  rows?: DiffRow[];
  fold_hints?: FoldHint[];
  lazy?: boolean;
  lazy_reason?: string | null;
  render_kind?: "notebook";
};

export type DiffStreamInitEvent = {
  type: "init";
  payload: RepoPayload;
};

export type DiffStreamFileEvent = {
  type: "file";
  entry: FileEntry;
  summary: Summary;
};

export type DiffStreamDoneEvent = {
  type: "done";
  summary: Summary;
};

export type DiffStreamErrorEvent = {
  type: "stream-error";
  error: string;
};

export type DiffStreamEvent =
  | DiffStreamInitEvent
  | DiffStreamFileEvent
  | DiffStreamDoneEvent
  | DiffStreamErrorEvent;

export async function fetchDefaults(): Promise<Defaults> {
  const response = await fetch("/api/defaults");
  if (!response.ok) {
    throw new Error(`Failed to load defaults: ${response.status}`);
  }
  return (await response.json()) as Defaults;
}

export async function fetchFileDiff(
  request: DiffRequest,
  entry: FileEntry,
): Promise<FileEntry> {
  const params = new URLSearchParams();
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
  if (entry.left_path) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path) {
    params.set("right_path", entry.right_path);
  }
  if (entry.display_name) {
    params.set("display_name", entry.display_name);
  }
  params.set("change_type", entry.change_type || "modify");

  const response = await fetch(`/api/file-diff?${params.toString()}`);
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || "Failed to load file diff.");
  }
  return payload as FileEntry;
}

export function openDiffStream(
  request: DiffRequest,
  onEvent: (event: DiffStreamEvent) => void,
  onTransportError: (error: Event) => void,
): EventSource {
  const params = new URLSearchParams();
  params.set("mode", request.mode === "against-head" ? "files" : request.mode);
  params.set("left", request.left);
  params.set("right", request.right);
  if (request.base_branch) {
    params.set("base_branch", request.base_branch);
  }
  if (request.review_branch) {
    params.set("review_branch", request.review_branch);
  }

  const source = new EventSource(`/api/diff-stream?${params.toString()}`);
  source.addEventListener("init", (event) => {
    onEvent({
      type: "init",
      payload: JSON.parse(event.data) as RepoPayload,
    });
  });
  source.addEventListener("file", (event) => {
    const payload = JSON.parse(event.data) as {
      entry: FileEntry;
      summary: Summary;
    };
    onEvent({
      type: "file",
      entry: payload.entry,
      summary: payload.summary,
    });
  });
  source.addEventListener("done", (event) => {
    const payload = JSON.parse(event.data) as { summary: Summary };
    onEvent({ type: "done", summary: payload.summary });
  });
  source.addEventListener("stream-error", (event) => {
    const payload = JSON.parse(event.data) as { error: string };
    onEvent({ type: "stream-error", error: payload.error });
  });
  source.onerror = onTransportError;
  return source;
}
