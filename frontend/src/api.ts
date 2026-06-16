import { z } from "zod";

export const DiffModeSchema = z.enum([
  "files",
  "staged",
  "head",
  "refs",
  "branch-review",
  "preset",
]);
export type DiffMode = z.infer<typeof DiffModeSchema>;

export const DiffEngineSchema = z.enum(["dirdiff", "git", "difftastic"]);
export type DiffEngine = z.infer<typeof DiffEngineSchema>;

export type RepoId = number;

const RepoMarkSchema = z.strictObject({
  id: z.number().int(),
  path: z.string(),
  name: z.string(),
  marked_at: z.string(),
});
export type RepoMark = z.infer<typeof RepoMarkSchema>;

const RefChoicesSchema = z.strictObject({
  builtins: z.array(z.string()),
  locals: z.array(z.string()),
  remotes: z.array(z.string()),
  remote_names: z.array(z.string()),
});
export type RefChoices = z.infer<typeof RefChoicesSchema>;

const RepoRefsSchema = z.strictObject({
  default_base_branch: z.string().nullable(),
  preferred_review_branch: z.string().nullable(),
  ref_choices: RefChoicesSchema,
});
export type RepoRefs = z.infer<typeof RepoRefsSchema>;

export type DiffParams = {
  repo_id: RepoId;
  engine: DiffEngine;
  mode: DiffMode;
  left: string;
  right: string;
  base_branch: string | null;
  review_branch: string | null;
  show_untracked: boolean;
};

const SummarySchema = z.strictObject({
  changed_files: z.number().int(),
  added_files: z.number().int(),
  removed_files: z.number().int(),
  updated_files: z.number().int(),
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  skipped_files: z.number().int(),
  changed_cells: z.number().int().nullable().optional(),
  added_cells: z.number().int().nullable().optional(),
  removed_cells: z.number().int().nullable().optional(),
  modified_cells: z.number().int().nullable().optional(),
});
export type Summary = z.infer<typeof SummarySchema>;

export type RepoPayload = {
  display_name: string;
  mode: "repo";
  left_label: string;
  right_label: string;
  summary: Summary;
};

export type RepoManifestPayload = RepoPayload & {
  files: FileEntry[];
};

export type LazyInfoFile = {
  file_kind: FileKind;
  left_path: string | null;
  right_path: string | null;
  display_name: string;
  summary: FileSummary;
};

export type LazyInfoPayload = {
  files: LazyInfoFile[];
};

const FileSummarySchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  left_exists: z.boolean(),
  right_exists: z.boolean(),
});
export type FileSummary = z.infer<typeof FileSummarySchema>;

const NotebookSummarySchema = FileSummarySchema.extend({
  changed_cells: z.number().int(),
  added_cells: z.number().int(),
  removed_cells: z.number().int(),
  modified_cells: z.number().int(),
  notebook_metadata_changed: z.boolean(),
});
export type NotebookSummary = z.infer<typeof NotebookSummarySchema>;

export const RowStatusSchema = z.enum([
  "equal",
  "replace",
  "insert",
  "delete",
  "fold",
  "elided",
]);
export type RowStatus = z.infer<typeof RowStatusSchema>;

const InlineTokenSchema = z.strictObject({
  text: z.string(),
  is_ws: z.boolean(),
  status: z.enum(["unchanged", "replace", "insert", "delete"]),
});
export type InlineToken = z.infer<typeof InlineTokenSchema>;

const SyntaxSpanSchema = z.strictObject({
  start: z.number().int(),
  end: z.number().int(),
  classes: z.array(z.string()),
});
export type SyntaxSpan = z.infer<typeof SyntaxSpanSchema>;

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

const DiffRowCommonSchema = {
  left_no: z.number().int().nullable(),
  right_no: z.number().int().nullable(),
  left_text: z.string().nullable(),
  right_text: z.string().nullable(),
  left_tokens: z.array(InlineTokenSchema),
  right_tokens: z.array(InlineTokenSchema),
  left_syntax: z.array(SyntaxSpanSchema),
  right_syntax: z.array(SyntaxSpanSchema),
  count: z.number().int().nullable().optional(),
  label: z.string().nullable().optional(),
};

const DiffRowSchema: z.ZodType<DiffRow> = z.lazy(() =>
  z.discriminatedUnion("status", [
    z.strictObject({
      status: z.literal("equal"),
      ...DiffRowCommonSchema,
      foldedRows: z.array(DiffRowSchema).optional(),
    }),
    z.strictObject({
      status: z.literal("replace"),
      ...DiffRowCommonSchema,
      foldedRows: z.array(DiffRowSchema).optional(),
    }),
    z.strictObject({
      status: z.literal("insert"),
      ...DiffRowCommonSchema,
      foldedRows: z.array(DiffRowSchema).optional(),
    }),
    z.strictObject({
      status: z.literal("delete"),
      ...DiffRowCommonSchema,
      foldedRows: z.array(DiffRowSchema).optional(),
    }),
    z.strictObject({
      status: z.literal("fold"),
      ...DiffRowCommonSchema,
      count: z.number().int(),
      foldedRows: z.array(DiffRowSchema),
    }),
    z.strictObject({
      status: z.literal("elided"),
      ...DiffRowCommonSchema,
      count: z.number().int(),
      label: z.string(),
      foldedRows: z.array(DiffRowSchema).optional(),
    }),
  ]),
);

const FoldHintSchema = z.strictObject({
  start_row: z.number().int(),
  end_row: z.number().int(),
  label: z.string(),
});
export type FoldHint = z.infer<typeof FoldHintSchema>;

export type GitChangeType = "modify" | "add" | "delete" | "rename" | "copy";

const GitFileKindSchema = z.strictObject({
  type: z.literal("git"),
  status: z.enum(["modified", "added", "deleted", "renamed", "copied"]),
});
const UntrackedFileKindSchema = z.strictObject({
  type: z.literal("untracked"),
});
const FileKindSchema = z.discriminatedUnion("type", [
  GitFileKindSchema,
  UntrackedFileKindSchema,
]);
export type FileKind = z.infer<typeof FileKindSchema>;

const LazyReasonSchema = z.enum([
  "too_big",
  "generated",
  "deleted",
  "untracked",
  "pure_renamed",
]);
export type LazyReason = z.infer<typeof LazyReasonSchema>;

const EngineWarningSchema = z.strictObject({
  type: z.literal("difftastic_graph_limit"),
  message: z.string(),
});
export type EngineWarning = z.infer<typeof EngineWarningSchema>;

const FileEntrySchema = z.strictObject({
  display_name: z.string().optional(),
  mode: z.literal("git").optional(),
  left_label: z.string().optional(),
  right_label: z.string().optional(),
  summary: z.union([NotebookSummarySchema, FileSummarySchema]).optional(),
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  changed_lines: z.number().int().nullable().optional(),
  added_lines: z.number().int().nullable().optional(),
  removed_lines: z.number().int().nullable().optional(),
  rows: z.array(DiffRowSchema).optional(),
  fold_hints: z.array(FoldHintSchema).optional(),
  engine_warning: EngineWarningSchema.nullable().optional(),
  lazy: LazyReasonSchema.nullable().optional(),
  default_expanded: z.boolean().optional(),
  render_kind: z.literal("notebook").optional(),
  render_mode: z.literal("plain").nullable().optional(),
  truncated_rows: z.number().int().nullable().optional(),
  notebook_metadata_rows: z.array(DiffRowSchema).optional(),
  notebook_metadata_changed_lines: z.number().int().optional(),
  notebook_metadata_hunk_count: z.number().int().optional(),
  notebook_metadata_lazy: z.boolean().optional(),
  notebook_metadata_render_mode: z.literal("plain").nullable().optional(),
  notebook_metadata_truncated_rows: z.number().int().optional(),
  cells: z.array(z.lazy(() => NotebookCellEntrySchema)).optional(),
});
export type FileEntry = z.infer<typeof FileEntrySchema>;

const RepoFileEntrySchema = z.strictObject({
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  lazy: LazyReasonSchema.nullable(),
});

const LazyInfoFileSchema = z.strictObject({
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  display_name: z.string(),
  summary: FileSummarySchema,
});

const TextFileDiffResponseSchema = z.strictObject({
  display_name: z.string(),
  mode: z.literal("git"),
  left_label: z.string(),
  right_label: z.string(),
  summary: FileSummarySchema,
  rows: z.array(DiffRowSchema),
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  lazy: LazyReasonSchema.nullable(),
  default_expanded: z.boolean(),
  render_mode: z.literal("plain").nullable(),
  truncated_rows: z.number().int().nullable(),
  fold_hints: z.array(FoldHintSchema),
  engine_warning: EngineWarningSchema.nullable(),
});

const NotebookCellEntrySchema = z.strictObject({
  kind: z.enum(["added", "removed", "modified"]),
  cell_type: z.string(),
  cell_id: z.string().nullable(),
  cell_key: z.string(),
  left_index: z.number().int().nullable(),
  right_index: z.number().int().nullable(),
  left_id: z.string().nullable(),
  right_id: z.string().nullable(),
  source_changed: z.boolean(),
  metadata_changed: z.boolean(),
  outputs_changed: z.boolean(),
  source_rows: z.array(DiffRowSchema),
  source_changed_lines: z.number().int(),
  source_modified_lines: z.number().int(),
  source_added_lines: z.number().int(),
  source_removed_lines: z.number().int(),
  source_fold_hints: z.array(FoldHintSchema),
  metadata_rows: z.array(DiffRowSchema),
  outputs_rows: z.array(DiffRowSchema),
  metadata_changed_lines: z.number().int(),
  metadata_modified_lines: z.number().int(),
  metadata_added_lines: z.number().int(),
  metadata_removed_lines: z.number().int(),
  metadata_hunk_count: z.number().int(),
  metadata_lazy: z.boolean(),
  outputs_changed_lines: z.number().int(),
  outputs_modified_lines: z.number().int(),
  outputs_added_lines: z.number().int(),
  outputs_removed_lines: z.number().int(),
  outputs_hunk_count: z.number().int(),
  outputs_lazy: z.boolean(),
  source_render_mode: z.literal("plain").nullable(),
  source_truncated_rows: z.number().int().nullable(),
  metadata_render_mode: z.literal("plain").nullable(),
  metadata_truncated_rows: z.number().int().nullable(),
  outputs_render_mode: z.literal("plain").nullable(),
  outputs_truncated_rows: z.number().int().nullable(),
});
export type NotebookCellEntry = z.infer<typeof NotebookCellEntrySchema>;

const NotebookFileDiffResponseSchema = z.strictObject({
  display_name: z.string(),
  mode: z.literal("git"),
  render_kind: z.literal("notebook"),
  left_label: z.string(),
  right_label: z.string(),
  summary: NotebookSummarySchema,
  notebook_metadata_rows: z.array(DiffRowSchema),
  notebook_metadata_changed_lines: z.number().int(),
  notebook_metadata_hunk_count: z.number().int(),
  notebook_metadata_lazy: z.boolean(),
  cells: z.array(NotebookCellEntrySchema),
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  default_expanded: z.boolean(),
});

const FileDiffResponseSchema = z.union([
  NotebookFileDiffResponseSchema,
  TextFileDiffResponseSchema,
]);

const RepoManifestPayloadSchema = z.strictObject({
  display_name: z.string(),
  mode: z.literal("repo"),
  left_label: z.string(),
  right_label: z.string(),
  summary: SummarySchema,
  files: z.array(RepoFileEntrySchema),
});

const LazyInfoPayloadSchema = z.strictObject({
  files: z.array(LazyInfoFileSchema),
});

const NotebookSectionSchema = z.strictObject({
  section: z.string(),
  cell_key: z.string().nullable(),
  left_index: z.number().int().nullable(),
  right_index: z.number().int().nullable(),
  left_label: z.string(),
  right_label: z.string(),
  rows: z.array(DiffRowSchema),
  render_mode: z.literal("plain").nullable(),
  truncated_rows: z.number().int(),
  fold_hints: z.array(FoldHintSchema),
});
export type NotebookSection = z.infer<typeof NotebookSectionSchema>;

const ErrorResponseSchema = z.strictObject({
  error: z.string(),
});

async function parseErrorResponse(response: Response): Promise<never> {
  const payload = await response.json();
  throw new Error(ErrorResponseSchema.parse(payload).error);
}

export async function fetchRepoRefs(repoId: RepoId): Promise<RepoRefs> {
  const params = new URLSearchParams({ repo_id: String(repoId) });
  const response = await fetch(`/api/repo-refs?${params.toString()}`);
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return RepoRefsSchema.parse(await response.json());
}

export async function fetchRepos(): Promise<RepoMark[]> {
  const response = await fetch("/api/repos");
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return z.array(RepoMarkSchema).parse(await response.json());
}

function diffParamsQueryParams(diffParams: DiffParams): URLSearchParams {
  const params = new URLSearchParams();
  params.set("repo_id", String(diffParams.repo_id));
  params.set("engine", diffParams.engine);
  params.set("mode", diffParams.mode);
  if (diffParams.left.length > 0) {
    params.set("left", diffParams.left);
  }
  if (diffParams.right.length > 0) {
    params.set("right", diffParams.right);
  }
  if (diffParams.base_branch !== null && diffParams.base_branch.length > 0) {
    params.set("base_branch", diffParams.base_branch);
  }
  if (
    diffParams.review_branch !== null &&
    diffParams.review_branch.length > 0
  ) {
    params.set("review_branch", diffParams.review_branch);
  }
  if (diffParams.show_untracked) {
    params.set("show_untracked", "true");
  }
  return params;
}

export async function fetchManifest(
  diffParams: DiffParams,
  signal?: AbortSignal,
): Promise<RepoManifestPayload> {
  const params = diffParamsQueryParams(diffParams);
  const response = await fetch(`/api/manifest?${params.toString()}`, {
    signal,
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return RepoManifestPayloadSchema.parse(await response.json());
}

export async function fetchLazyInfo(
  diffParams: DiffParams,
  signal?: AbortSignal,
): Promise<LazyInfoPayload> {
  const params = diffParamsQueryParams(diffParams);
  const response = await fetch(`/api/lazy-info?${params.toString()}`, {
    signal,
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return LazyInfoPayloadSchema.parse(await response.json());
}

export async function fetchFileDiff(
  diffParams: DiffParams,
  entry: FileEntry,
  signal?: AbortSignal,
): Promise<FileEntry> {
  const params = diffParamsQueryParams(diffParams);
  if (entry.left_path !== null && entry.left_path.length > 0) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null && entry.right_path.length > 0) {
    params.set("right_path", entry.right_path);
  }
  if (entry.display_name !== undefined && entry.display_name.length > 0) {
    params.set("display_name", entry.display_name);
  }
  params.set("change_type", changeTypeForFileKind(entry.file_kind));
  params.set("file_kind", entry.file_kind.type);

  const response = await fetch(`/api/file-diff?${params.toString()}`, {
    signal,
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return FileDiffResponseSchema.parse(await response.json());
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
      return "modify";
    default:
      return unsupportedGitStatus(fileKind.status);
  }
}

function unsupportedGitStatus(status: never): never {
  throw new Error(`Unsupported git file status: ${String(status)}.`);
}

export async function fetchNotebookSection(
  diffParams: DiffParams,
  entry: FileEntry,
  options: { section: string; cellKey?: string | null },
  signal?: AbortSignal,
): Promise<NotebookSection> {
  const params = diffParamsQueryParams(diffParams);
  if (entry.left_path !== null && entry.left_path.length > 0) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null && entry.right_path.length > 0) {
    params.set("right_path", entry.right_path);
  }
  params.set("section", options.section);
  if (options.cellKey !== null && options.cellKey !== undefined) {
    params.set("cell_key", options.cellKey);
  }

  const response = await fetch(`/api/notebook-section?${params.toString()}`, {
    signal,
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return NotebookSectionSchema.parse(await response.json());
}
