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

export const DiffEngineSchema = z.enum([
  "dirdiff",
  "git",
  "difftastic",
  "gumtree",
]);
export type DiffEngine = z.infer<typeof DiffEngineSchema>;

export const PresetTypeSchema = z.enum(["diff", "fold", "gumtree"]);
export type PresetType = z.infer<typeof PresetTypeSchema>;

export type RepoId = number;

const RepoMarkSchema = z.strictObject({
  id: z.number().int(),
  path: z.string(),
  name: z.string(),
  marked_at: z.string(),
});
export type RepoMark = z.infer<typeof RepoMarkSchema>;

const UserProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});
export type UserProfile = z.infer<typeof UserProfileSchema>;

const PreferencesSchema = z.strictObject({
  id: z.number().int().positive(),
  aggressive_folds: z.boolean(),
});
export type Preferences = z.infer<typeof PreferencesSchema>;

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

const PresetGroupSchema = z.strictObject({
  name: z.string(),
  display_name: z.string(),
});
export type PresetGroup = z.infer<typeof PresetGroupSchema>;

const PresetCatalogSchema = z.strictObject({
  default_preset: z.string(),
  groups: z.array(PresetGroupSchema),
});
export type PresetCatalog = z.infer<typeof PresetCatalogSchema>;

const PresetCatalogsSchema = z.strictObject({
  diff: PresetCatalogSchema,
  fold: PresetCatalogSchema,
  gumtree: PresetCatalogSchema,
});
export type PresetCatalogs = z.infer<typeof PresetCatalogsSchema>;

type DiffParamsBase = {
  repo_id: RepoId;
  engine: DiffEngine;
};

export type HeadDiffParams = DiffParamsBase & {
  mode: "head";
  left: "head";
  right: "worktree";
  show_untracked: true;
};

export type RefsDiffParams = DiffParamsBase & {
  mode: "refs";
  left: string;
  right: string;
};

export type BranchReviewDiffParams = DiffParamsBase & {
  mode: "branch-review";
  base_branch: string;
  review_branch: string;
};

export type PresetDiffParams = DiffParamsBase & {
  mode: "preset";
  preset_type: PresetType;
  preset: string;
};

export type DiffParams =
  | HeadDiffParams
  | RefsDiffParams
  | BranchReviewDiffParams
  | PresetDiffParams;

const SummarySchema = z.strictObject({
  changed_files: z.number().int(),
  added_files: z.number().int(),
  removed_files: z.number().int(),
  updated_files: z.number().int(),
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

// ManifestEntry is a handle for follow-up file APIs such as /api/file-diff and
// /api/lazy-info. It is not rendered directly; on /api/file-diff failure the
// client may copy this handle into an explicit error placeholder FileEntry.
export type ManifestEntry = {
  file_kind: FileKind;
  left_path: string | null;
  right_path: string | null;
  lazy: LazyReason | null;
};

export type RepoManifestPayload = RepoPayload & {
  files: ManifestEntry[];
};

export type LazyInfoFile = {
  file_kind: FileKind;
  left_path: string | null;
  right_path: string | null;
  display_name: string;
  changed_lines: number | null;
  added_lines: number | null;
  removed_lines: number | null;
  lazy: LazyReason | null;
};

export type LazyInfoPayload = {
  files: LazyInfoFile[];
};

const FileSummarySchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  moved_lines: z.number().int(),
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
  "move",
  "fold",
  "elided",
]);
export type RowStatus = z.infer<typeof RowStatusSchema>;

const InlineTokenSchema = z.strictObject({
  text: z.string(),
  is_ws: z.boolean(),
  status: z.enum(["unchanged", "replace", "insert", "delete", "move"]),
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
      status: z.literal("move"),
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
  kind: z.enum([
    "function_like",
    "class_like",
    "container",
    "section",
    "top_level",
  ]),
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
const LazyErrorSchema = z.strictObject({
  type: z.literal("error"),
  original: LazyReasonSchema.nullable(),
});
const LazyStateSchema = z.union([LazyReasonSchema, LazyErrorSchema]);
export type LazyState = z.infer<typeof LazyStateSchema>;

const EngineWarningSchema = z.strictObject({
  type: z.enum([
    "difftastic_graph_limit",
    "difftastic_empty_rows",
    "gumtree_invalid_json",
  ]),
  message: z.string(),
});
export type EngineWarning = z.infer<typeof EngineWarningSchema>;

// FileEntry is the renderable file-card/tree entry. It must come from
// /api/file-diff, from /api/lazy-info for lazy placeholders, or from
// /api/manifest only as a client-side error placeholder after /api/file-diff
// fails for that exact manifest entry.
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
  moved_lines: z.number().int().nullable().optional(),
  rows: z.array(DiffRowSchema).optional(),
  fold_hints: z.array(FoldHintSchema).optional(),
  engine_warning: EngineWarningSchema.nullable().optional(),
  lazy: LazyStateSchema.nullable().optional(),
  lazy_reason: LazyReasonSchema.optional(),
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

// /api/manifest supplies ManifestEntry handles for follow-up fetches. It does
// not contain enough data for normal renderable FileEntry values; the client
// may only copy it into an explicit error placeholder after /api/file-diff
// fails.
const ManifestEntrySchema = z.strictObject({
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  lazy: LazyReasonSchema.nullable(),
});

// /api/lazy-info must contain every field needed for a lazy placeholder
// FileEntry, including the lazy reason used by the file tree/card state.
const LazyInfoFileSchema = z.strictObject({
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  display_name: z.string(),
  changed_lines: z.number().int().nullable(),
  added_lines: z.number().int().nullable(),
  removed_lines: z.number().int().nullable(),
  lazy: LazyReasonSchema.nullable(),
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
  source_moved_lines: z.number().int(),
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

// /api/file-diff is the only source for fully hydrated renderable FileEntry
// values.
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
  files: z.array(ManifestEntrySchema),
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

const HttpExceptionResponseSchema = z.strictObject({
  detail: z.string(),
});

export const REQUEST_TIMEOUT_MS = 8_000;
const SLOW_FILE_DIFF_TIMEOUT_MS = 20_000;

type FetchJsonInit = RequestInit & {
  timeoutMs?: number;
};

type RequestErrorReason = "timeout" | "other";

class RequestError extends Error {
  readonly error_reason: RequestErrorReason;

  constructor(
    errorReason: RequestErrorReason,
    message: string,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "RequestError";
    this.error_reason = errorReason;
  }
}

async function parseErrorResponse(response: Response): Promise<never> {
  const bodyText = await response.text();
  if (bodyText.length > 0) {
    try {
      const payload = JSON.parse(bodyText);
      const parsedError = ErrorResponseSchema.safeParse(payload);
      if (parsedError.success) {
        throw new RequestError("other", parsedError.data.error);
      }
      const parsedDetail = HttpExceptionResponseSchema.safeParse(payload);
      if (parsedDetail.success) {
        throw new RequestError("other", parsedDetail.data.detail);
      }
    } catch (error) {
      if (!(error instanceof SyntaxError)) {
        throw error;
      }
    }
    throw new RequestError("other", bodyText);
  }
  throw new RequestError(
    "other",
    `Request failed with status ${response.status} ${response.statusText}.`,
  );
}

function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

function requestErrorLabel(input: string): string {
  const queryStart = input.indexOf("?");
  if (queryStart >= 0) {
    return input.slice(0, queryStart);
  }
  return input;
}

function createRequestSignal(
  upstreamSignal: AbortSignal | null | undefined,
  timeoutMs: number,
): {
  signal: AbortSignal;
  cancelTimeout: () => void;
  timedOut: () => boolean;
} {
  const controller = new AbortController();
  let didTimeout = false;
  const abortFromUpstream = () => {
    controller.abort(upstreamSignal?.reason);
  };
  if (upstreamSignal != null) {
    if (upstreamSignal.aborted) {
      controller.abort(upstreamSignal.reason);
    } else {
      upstreamSignal.addEventListener("abort", abortFromUpstream, {
        once: true,
      });
    }
  }
  const timeoutId = window.setTimeout(() => {
    didTimeout = true;
    controller.abort();
  }, timeoutMs);
  return {
    signal: controller.signal,
    cancelTimeout: () => {
      window.clearTimeout(timeoutId);
      if (upstreamSignal != null) {
        upstreamSignal.removeEventListener("abort", abortFromUpstream);
      }
    },
    timedOut: () => didTimeout,
  };
}

async function fetchJsonResponse(
  input: string,
  init?: FetchJsonInit,
): Promise<Response> {
  let timeoutMs = REQUEST_TIMEOUT_MS;
  let requestInit: RequestInit | undefined;
  if (init !== undefined) {
    const { timeoutMs: requestedTimeoutMs, ...rest } = init;
    if (requestedTimeoutMs !== undefined) {
      timeoutMs = requestedTimeoutMs;
    }
    requestInit = rest;
  }
  const upstreamSignal =
    requestInit === undefined ? undefined : requestInit.signal;
  const { signal, cancelTimeout, timedOut } = createRequestSignal(
    upstreamSignal,
    timeoutMs,
  );
  try {
    const initWithSignal =
      requestInit === undefined ? { signal } : { ...requestInit, signal };
    return await fetch(input, initWithSignal);
  } catch (error) {
    const label = requestErrorLabel(input);
    if (timedOut()) {
      throw new RequestError(
        "timeout",
        `Request timed out before response: ${label}`,
      );
    }
    if (isAbortError(error)) {
      throw error;
    }
    throw new RequestError(
      "other",
      `Request failed before response: ${label}`,
      {
        cause: error,
      },
    );
  } finally {
    cancelTimeout();
  }
}

export async function fetchRepoRefs(repoId: RepoId): Promise<RepoRefs> {
  const params = new URLSearchParams({ repo_id: String(repoId) });
  const response = await fetchJsonResponse(
    `/api/repo-refs?${params.toString()}`,
  );
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return RepoRefsSchema.parse(await response.json());
}

export async function fetchPresets(): Promise<PresetCatalogs> {
  const response = await fetchJsonResponse("/api/presets");
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return PresetCatalogsSchema.parse(await response.json());
}

export async function fetchRepos(): Promise<RepoMark[]> {
  const response = await fetchJsonResponse("/api/repos");
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return z.array(RepoMarkSchema).parse(await response.json());
}

export async function createUserProfile(
  username: string,
): Promise<UserProfile> {
  const response = await fetchJsonResponse("/api/user-profile", {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ username }),
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return UserProfileSchema.parse(await response.json());
}

export async function updateUserProfile(
  profileId: number,
  username: string,
): Promise<UserProfile> {
  const response = await fetchJsonResponse(`/api/user-profile/${profileId}`, {
    method: "PATCH",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ username }),
  });
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return UserProfileSchema.parse(await response.json());
}

export async function fetchPreferences(): Promise<Preferences> {
  const response = await fetchJsonResponse("/api/preferences");
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return PreferencesSchema.parse(await response.json());
}

export async function updatePreferences(
  preferencesId: number,
  aggressiveFolds: boolean,
): Promise<Preferences> {
  const response = await fetchJsonResponse(
    `/api/preferences/${preferencesId}`,
    {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
      },
      body: JSON.stringify({ aggressive_folds: aggressiveFolds }),
    },
  );
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return PreferencesSchema.parse(await response.json());
}

export function diffParamsQueryParams(diffParams: DiffParams): URLSearchParams {
  const params = new URLSearchParams();
  params.set("repo_id", String(diffParams.repo_id));
  params.set("engine", diffParams.engine);
  params.set("mode", diffParams.mode);
  if (diffParams.mode === "preset") {
    params.set("preset_type", diffParams.preset_type);
    params.set("preset", diffParams.preset);
    return params;
  }
  if (diffParams.mode === "branch-review") {
    params.set("base_branch", diffParams.base_branch);
    params.set("review_branch", diffParams.review_branch);
    return params;
  }
  params.set("left", diffParams.left);
  params.set("right", diffParams.right);
  if (diffParams.mode === "head") {
    params.set("show_untracked", "true");
  }
  return params;
}

export async function fetchManifest(
  diffParams: DiffParams,
  signal?: AbortSignal,
): Promise<RepoManifestPayload> {
  const params = diffParamsQueryParams(diffParams);
  const response = await fetchJsonResponse(
    `/api/manifest?${params.toString()}`,
    {
      signal,
    },
  );
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
  const response = await fetchJsonResponse(
    `/api/lazy-info?${params.toString()}`,
    {
      signal,
    },
  );
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  // /api/lazy-info must contain enough data for lazy placeholder FileEntry
  // construction; callers must not fill gaps from /api/manifest.
  return LazyInfoPayloadSchema.parse(await response.json());
}

export async function fetchFileDiff(
  diffParams: DiffParams,
  entry: ManifestEntry,
  signal?: AbortSignal,
  timeoutMs?: number,
): Promise<FileEntry> {
  const params = diffParamsQueryParams(diffParams);
  if (entry.left_path !== null && entry.left_path.length > 0) {
    params.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null && entry.right_path.length > 0) {
    params.set("right_path", entry.right_path);
  }
  params.set("change_type", changeTypeForFileKind(entry.file_kind));
  params.set("file_kind", entry.file_kind.type);

  let requestTimeoutMs = REQUEST_TIMEOUT_MS;
  if (usesSlowFileDiffTimeout(diffParams.engine)) {
    requestTimeoutMs = SLOW_FILE_DIFF_TIMEOUT_MS;
  }
  if (timeoutMs !== undefined) {
    requestTimeoutMs = timeoutMs;
  }

  const response = await fetchJsonResponse(
    `/api/file-diff?${params.toString()}`,
    {
      signal,
      timeoutMs: requestTimeoutMs,
    },
  );
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  // /api/file-diff is the source of hydrated renderable FileEntry values.
  return FileDiffResponseSchema.parse(await response.json());
}

function usesSlowFileDiffTimeout(engine: DiffEngine): boolean {
  switch (engine) {
    case "difftastic":
    case "gumtree":
      return true;
    case "dirdiff":
    case "git":
      return false;
    default:
      return engine satisfies never;
  }
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

  const response = await fetchJsonResponse(
    `/api/notebook-section?${params.toString()}`,
    {
      signal,
    },
  );
  if (!response.ok) {
    return parseErrorResponse(response);
  }
  return NotebookSectionSchema.parse(await response.json());
}
