/**
 * Defines the complete typed boundary between the browser UI and Python API.
 *
 * The module exports backend value types and the single `api` facade containing
 * canonical TanStack query and mutation definitions. It privately owns runtime
 * response validation, HTTP request construction, timeout handling, query keys,
 * and request functions. It must not own UI state, component behavior, query
 * observers, Toast presentation, or ChangeSet file-fetch sequencing.
 */
import { mutationOptions, queryOptions } from "@tanstack/solid-query";
import { z } from "zod";

const DiffEngineSchema = z.enum(["dirdiff", "git", "difftastic", "gumtree"]);

/**
 * Selects the backend diff implementation used for every requested file.
 *
 * Callers must pass one supported engine as part of complete DiffParams. The
 * value controls backend rendering and must not represent inline/split view.
 */
export type DiffEngine = z.infer<typeof DiffEngineSchema>;

const PresetTypeSchema = z.enum(["diff", "fold", "gumtree", "scroll"]);

/**
 * Identifies one backend preset catalog and its corresponding preset project.
 *
 * The value is both the Preset Tab kind and the `project_id` used by preset
 * DiffParams. It must not identify a repository-backed project.
 */
export type PresetType = z.infer<typeof PresetTypeSchema>;

/**
 * Identifies one repository known to the Python backend.
 *
 * Callers receive IDs from validated backend responses or canonical URL state.
 * The numeric alias must not be used for profile IDs or preset projects.
 */
export type ProjectId = number;

const RepoMarkSchema = z.strictObject({
  id: z.number().int().positive(),
  path: z.string(),
  name: z.string(),
  marked_at: z.string(),
});

/**
 * Describes one repository available to repository-backed Tabs.
 *
 * All fields come from the repositories query. UI state stores only `id` and
 * derives the remaining display data from this backend-owned value.
 */
export type RepoMark = z.infer<typeof RepoMarkSchema>;

const UserProfileSchema = z.strictObject({
  id: z.number().int().positive(),
  username: z.string().min(1),
});

/**
 * Describes the selected user identity returned by profile mutations.
 *
 * The complete value may be persisted explicitly in localStorage. It contains
 * identity only and must not absorb backend-owned preferences.
 */
export type UserProfile = z.infer<typeof UserProfileSchema>;

const PreferencesSchema = z.strictObject({
  user_profile_id: z.number().int().positive(),
  aggressive_folds: z.boolean(),
});

/**
 * Describes backend-owned preferences for one user profile.
 *
 * Callers may cache this complete value under its profile query key. The value
 * must not be copied into workspace state or persisted separately by the UI.
 */
export type Preferences = z.infer<typeof PreferencesSchema>;

const LocalBranchSelectionSchema = z.strictObject({
  source: z.literal("local"),
  branch: z.string(),
});

const RemoteBranchSelectionSchema = z.strictObject({
  source: z.literal("remote"),
  remote: z.string(),
  branch: z.string(),
});

const BranchSelectionSchema = z.discriminatedUnion("source", [
  LocalBranchSelectionSchema,
  RemoteBranchSelectionSchema,
]);

/**
 * Identifies one structured local or remote branch selection.
 *
 * Remote selections require both the remote and branch. Local selections do
 * not admit a meaningless remote field. Free-form refs use strings instead.
 */
export type BranchSelection = z.infer<typeof BranchSelectionSchema>;

const DefaultBaseSelectionSchema = z.union([
  BranchSelectionSchema,
  z.strictObject({
    kind: z.literal("error"),
    error: z.literal("heuristic_fail"),
  }),
]);

/**
 * Represents the backend's branch-review base recommendation.
 *
 * A valid recommendation is a complete BranchSelection. The tagged error is
 * explicit backend data and must not be silently replaced with another branch.
 */
export type DefaultBaseSelection = z.infer<typeof DefaultBaseSelectionSchema>;

const RemoteBranchRefSchema = z.strictObject({
  structured: z.strictObject({
    remote: z.string(),
    branch: z.string(),
  }),
  gitref: z.string(),
});

const RefChoicesSchema = z.strictObject({
  builtins: z.array(z.string()),
  local_branches: z.array(z.string()),
  remotes: z.array(z.string()),
  remote_branches: z.array(RemoteBranchRefSchema),
});

/**
 * Describes all ref autocomplete categories for one repository snapshot.
 *
 * Built-ins and local branches are direct refs. Remote branches retain both a
 * free-form gitref and their structured remote/branch identity for the distinct
 * Refs and Branch Review workflows.
 */
export type RefChoices = z.infer<typeof RefChoicesSchema>;

const RepoRefsSchema = z.strictObject({
  ref_choices: RefChoicesSchema,
});

/**
 * Contains the canonical ref choices returned for one repository.
 *
 * Consumers share this single backend entity and filter it locally. It must not
 * contain live autocomplete input or a selected ref.
 */
export type RepoRefs = z.infer<typeof RepoRefsSchema>;

const RepoDefaultsSchema = z.strictObject({
  default_base_selection: DefaultBaseSelectionSchema,
  preferred_review_selection: BranchSelectionSchema,
});

/**
 * Contains the backend's two branch-review defaults for one repository.
 *
 * These values remain realtime query inputs for untouched controls and must not
 * overwrite user-edited autocomplete input.
 */
export type RepoDefaults = z.infer<typeof RepoDefaultsSchema>;

const RepoMainBranchSchema = z.strictObject({
  project_id: z.number().int().positive(),
  selection: BranchSelectionSchema,
});

/**
 * Confirms the repository main-branch selection stored by the backend.
 *
 * Callers may rely on the returned project and complete selection after the
 * mutation succeeds. The value is not a local optimistic placeholder.
 */
export type RepoMainBranch = z.infer<typeof RepoMainBranchSchema>;

const PreparedPullRequestBranchSchema = z.strictObject({
  remote: z.string(),
  branch: z.string(),
});

const PreparedPullRequestSchema = z.strictObject({
  project_id: z.number().int().positive(),
  pull_request_url: z.string(),
  base_branch: PreparedPullRequestBranchSchema,
  review_branch: PreparedPullRequestBranchSchema,
});

/**
 * Contains the authoritative repository and branches resolved from a PR URL.
 *
 * Successful preparation supplies every value needed to reconstruct a canonical
 * Branch Review workspace. Callers must not combine it with a previous repo.
 */
export type PreparedPullRequest = z.infer<typeof PreparedPullRequestSchema>;

const PresetGroupSchema = z.strictObject({
  id: z.string(),
  display_name: z.string(),
});

/**
 * Describes one selectable preset within a catalog.
 *
 * `id` is sent as `preset_subset`; `display_name` is presentation data. The
 * group contains no manifest or rendered-file content.
 */
export type PresetGroup = z.infer<typeof PresetGroupSchema>;

const PresetCatalogSchema = z.strictObject({
  default_preset: z.string(),
  groups: z.array(PresetGroupSchema),
});

/**
 * Describes one preset kind's default and selectable groups.
 *
 * The catalog is directory metadata only. Consumers must request a ChangeSet
 * separately after the user selects one group.
 */
export type PresetCatalog = z.infer<typeof PresetCatalogSchema>;

const PresetCatalogsSchema = z.strictObject({
  diff: PresetCatalogSchema,
  fold: PresetCatalogSchema,
  gumtree: PresetCatalogSchema,
  scroll: PresetCatalogSchema,
});

/**
 * Contains all four preset catalogs returned by the bounded catalog endpoint.
 *
 * Consumers select a catalog by PresetType and must not copy catalogs into
 * component state merely to expose them to Preset controls.
 */
export type PresetCatalogs = z.infer<typeof PresetCatalogsSchema>;

/**
 * Contains fields shared by every repository-backed diff request.
 *
 * The project and engine are always required. Preset requests use their own
 * complete parameter shape because their project identity is a PresetType.
 */
type RepoBackedDiffParams = {
  project_id: ProjectId;
  engine: DiffEngine;
};

/**
 * Requests the current HEAD against the complete worktree, including untracked
 * files.
 *
 * Callers construct the complete fixed mode fields; none are optional or
 * inferred by the transport layer.
 */
export type HeadDiffParams = RepoBackedDiffParams & {
  mode: "head";
  left: "head";
  right: "worktree";
  show_untracked: true;
};

/**
 * Requests a selected diff between two Git refs in one repository.
 *
 * Both refs are complete selected values returned by Refs Controls, not live
 * input or autocomplete suggestions.
 */
export type RefsDiffParams = RepoBackedDiffParams & {
  mode: "refs";
  left: string;
  right: string;
};

/**
 * Requests a structured base/review branch ChangeSet in one repository.
 *
 * Both selections are complete local or remote values. The transport must not
 * reconstruct missing remotes or substitute repository defaults.
 */
export type BranchReviewDiffParams = RepoBackedDiffParams & {
  mode: "branch-review";
  base_selection: BranchSelection;
  review_selection: BranchSelection;
};

/**
 * Requests one selected preset fixture using the chosen diff engine.
 *
 * The preset kind is the backend project ID and `preset_subset` is the selected
 * catalog group. Repository selection is deliberately absent.
 */
export type PresetDiffParams = {
  project_id: PresetType;
  engine: DiffEngine;
  mode: "preset";
  preset_subset: string;
};

/**
 * Represents the complete immutable input to every ChangeSet snapshot query.
 *
 * Each variant contains all backend-required fields. Live control input and
 * partially selected workflows must never be represented as DiffParams.
 */
export type DiffParams =
  | HeadDiffParams
  | RefsDiffParams
  | BranchReviewDiffParams
  | PresetDiffParams;

const ManifestSummarySchema = z.strictObject({
  changed_files: z.number().int(),
  added_files: z.number().int(),
  removed_files: z.number().int(),
  updated_files: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  skipped_files: z.number().int(),
  changed_cells: z.number().int().nullable(),
  added_cells: z.number().int().nullable(),
  removed_cells: z.number().int().nullable(),
  modified_cells: z.number().int().nullable(),
});

/**
 * Contains immutable aggregate statistics for one manifest snapshot.
 *
 * Notebook cell totals are always present but may be null. The summary must not
 * be progressively reconstructed from individual file responses.
 */
export type ManifestSummary = z.infer<typeof ManifestSummarySchema>;

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

/**
 * Identifies how Git classifies one manifest or rendered-file entry.
 *
 * Git entries require their exact status; untracked entries carry no invented
 * status. This type is backend metadata rather than file presentation state.
 */
export type FileKind = z.infer<typeof FileKindSchema>;

const LazyReasonSchema = z.enum([
  "too_big",
  "generated",
  "deleted",
  "untracked",
  "pure_renamed",
]);

/**
 * Explains why the backend intentionally delays one manifest file.
 *
 * A null lazy field means ordinary sequential loading. Errors are query state
 * and must not be encoded as another LazyReason.
 */
export type LazyReason = z.infer<typeof LazyReasonSchema>;

const ManifestEntrySchema = z.strictObject({
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  lazy: LazyReasonSchema.nullable(),
});

/**
 * Provides the complete thin handle for one manifest file.
 *
 * Paths, kind and lazy reason are sufficient for later file endpoints. The
 * handle deliberately contains no rendered rows, file summary, or copied state.
 */
export type ManifestEntry = z.infer<typeof ManifestEntrySchema>;

/**
 * Represents one file leaf in the recursive manifest tree.
 *
 * `name` is tree presentation data and `entry` is the exact backend handle used
 * for later requests. A file node cannot contain child nodes.
 */
export type ManifestFile = {
  type: "file";
  name: string;
  entry: ManifestEntry;
};

/**
 * Represents one directory in the recursive manifest tree.
 *
 * `path` is the stable expansion key and `entries` preserve backend order. A
 * directory carries no file request handle.
 */
export type ManifestDirectory = {
  type: "directory";
  name: string;
  path: string;
  entries: ManifestNode[];
};

/**
 * Represents one ordered node in a manifest tree.
 *
 * Consumers discriminate by `type` and must preserve recursive entry order when
 * deriving FileCards or FileTree rows.
 */
export type ManifestNode = ManifestFile | ManifestDirectory;

const ManifestNodeSchema: z.ZodType<ManifestNode> = z.lazy(() =>
  z.discriminatedUnion("type", [
    z.strictObject({
      type: z.literal("file"),
      name: z.string(),
      entry: ManifestEntrySchema,
    }),
    z.strictObject({
      type: z.literal("directory"),
      name: z.string(),
      path: z.string(),
      entries: z.array(ManifestNodeSchema),
    }),
  ]),
);

const ManifestSchema = z.strictObject({
  cache_id: z.string(),
  display_name: z.string(),
  mode: z.literal("repo"),
  left_label: z.string(),
  right_label: z.string(),
  summary: ManifestSummarySchema,
  tree: z.array(ManifestNodeSchema),
});

/**
 * Describes one ordered immutable ChangeSet snapshot returned by the manifest
 * endpoint.
 *
 * The cache ID isolates all subsequent file queries. The tree remains thin and
 * must not be mutated with progressive file results.
 */
export type Manifest = z.infer<typeof ManifestSchema>;

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

/**
 * Contains the complete lightweight presentation data for one delayed file.
 *
 * Every field comes from `/api/lazy-info`; callers must not fill missing values
 * from the manifest or a failed file request.
 */
export type LazyInfoFile = z.infer<typeof LazyInfoFileSchema>;

const LazyInfoSchema = z.strictObject({
  files: z.array(LazyInfoFileSchema),
});

/**
 * Contains lightweight metadata for the intentionally delayed manifest files.
 *
 * The response is backend query data and must not become a second mutable file
 * store inside ChangeSet.
 */
export type LazyInfo = z.infer<typeof LazyInfoSchema>;

const TextFileSummarySchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  moved_lines: z.number().int(),
  left_exists: z.boolean(),
  right_exists: z.boolean(),
});

/**
 * Contains complete statistics for one ordinary rendered text file.
 *
 * These values belong to the file response and its header. They must not be
 * substituted with manifest aggregates or partially loaded counters.
 */
export type TextFileSummary = z.infer<typeof TextFileSummarySchema>;

const NotebookFileSummarySchema = TextFileSummarySchema.extend({
  changed_cells: z.number().int(),
  added_cells: z.number().int(),
  removed_cells: z.number().int(),
  modified_cells: z.number().int(),
  notebook_metadata_changed: z.boolean(),
});

/**
 * Contains complete file and cell statistics for one rendered notebook.
 *
 * The notebook fields are required for notebook presentation and must not be
 * interpreted as optional text-file extensions.
 */
export type NotebookFileSummary = z.infer<typeof NotebookFileSummarySchema>;

const RowStatusSchema = z.enum([
  "equal",
  "replace",
  "insert",
  "delete",
  "move",
]);

/**
 * Classifies one aligned diff row according to the backend renderer.
 *
 * The value drives presentation only and must not be recomputed from text or
 * line-number presence in the browser.
 */
export type RowStatus = z.infer<typeof RowStatusSchema>;

const InlineTokenSchema = z.strictObject({
  text: z.string(),
  is_ws: z.boolean(),
  status: z.enum(["unchanged", "replace", "insert", "delete", "move"]),
});

/**
 * Describes one backend-produced inline token and its change classification.
 *
 * Consumers render the exact text and whitespace flag; they must not retokenize
 * rows or infer another status.
 */
export type InlineToken = z.infer<typeof InlineTokenSchema>;

const SyntaxSpanSchema = z.strictObject({
  start: z.number().int(),
  end: z.number().int(),
  classes: z.array(z.string()),
});

/**
 * Describes one syntax-highlighting span within a row's source text.
 *
 * Offsets and classes are authoritative backend output. The type carries no DOM
 * node or rendered measurement.
 */
export type SyntaxSpan = z.infer<typeof SyntaxSpanSchema>;

const DiffRowSchema = z.strictObject({
  status: RowStatusSchema,
  left_no: z.number().int().nullable(),
  right_no: z.number().int().nullable(),
  left_text: z.string().nullable(),
  right_text: z.string().nullable(),
  left_tokens: z.array(InlineTokenSchema),
  right_tokens: z.array(InlineTokenSchema),
  left_syntax: z.array(SyntaxSpanSchema),
  right_syntax: z.array(SyntaxSpanSchema),
  hunk_index: z.number().int().nonnegative().nullable(),
});

/**
 * Contains one complete backend-aligned row for text or notebook source.
 *
 * Nullable side fields represent genuinely absent lines. `hunk_index` is the
 * backend-provided navigation identity and must not be reconstructed from rows.
 */
export type DiffRow = z.infer<typeof DiffRowSchema>;

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

/**
 * Describes one backend fold suggestion over unchanged rows.
 *
 * The range must never contain a real hunk target. It is an input to frontend
 * fold presentation, not mutable expansion state.
 */
export type FoldHint = z.infer<typeof FoldHintSchema>;

const EngineWarningSchema = z.strictObject({
  type: z.enum([
    "difftastic_graph_limit",
    "difftastic_empty_rows",
    "gumtree_invalid_json",
  ]),
  message: z.string(),
});

/**
 * Describes a non-fatal renderer warning attached to one file response.
 *
 * Callers display the complete backend message while continuing to render valid
 * file data. It must not be treated as a request error or hidden Toast.
 */
export type EngineWarning = z.infer<typeof EngineWarningSchema>;

const TextFileDiffSchema = z.strictObject({
  display_name: z.string(),
  mode: z.literal("git"),
  left_label: z.string(),
  right_label: z.string(),
  summary: TextFileSummarySchema,
  rows: z.array(DiffRowSchema),
  hunk_count: z.number().int().nonnegative(),
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  lazy: LazyReasonSchema.nullable(),
  default_expanded: z.boolean(),
  fold_hints: z.array(FoldHintSchema),
  engine_warning: EngineWarningSchema.nullable(),
});

/**
 * Contains the complete renderable response for one ordinary text file.
 *
 * The stable backend response deliberately has no text `render_kind`. Callers
 * distinguish notebooks by their existing notebook discriminator.
 */
export type TextFileDiff = z.infer<typeof TextFileDiffSchema>;

const NotebookCellSchema = z.strictObject({
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
  source_hunk_count: z.number().int().nonnegative(),
  source_changed_lines: z.number().int(),
  source_modified_lines: z.number().int(),
  source_added_lines: z.number().int(),
  source_removed_lines: z.number().int(),
  source_moved_lines: z.number().int(),
  source_fold_hints: z.array(FoldHintSchema),
  metadata_changed_lines: z.number().int(),
  metadata_modified_lines: z.number().int(),
  metadata_added_lines: z.number().int(),
  metadata_removed_lines: z.number().int(),
  outputs_changed_lines: z.number().int(),
  outputs_modified_lines: z.number().int(),
  outputs_added_lines: z.number().int(),
  outputs_removed_lines: z.number().int(),
});

/**
 * Contains one notebook cell's complete structural and diff metadata.
 *
 * The stable `cell_key` identifies the current region bridge. Source rows,
 * metadata and output statistics remain backend data rather than UI state.
 */
export type NotebookCell = z.infer<typeof NotebookCellSchema>;

const NotebookFileDiffSchema = z.strictObject({
  display_name: z.string(),
  mode: z.literal("git"),
  render_kind: z.literal("notebook"),
  left_label: z.string(),
  right_label: z.string(),
  summary: NotebookFileSummarySchema,
  hunk_count: z.number().int().nonnegative(),
  notebook_metadata_changed_lines: z.number().int(),
  cells: z.array(NotebookCellSchema),
  file_kind: FileKindSchema,
  left_path: z.string().nullable(),
  right_path: z.string().nullable(),
  default_expanded: z.boolean(),
});

/**
 * Contains the complete renderable response for one notebook file.
 *
 * The notebook discriminator is the stable backend variant marker. Every cell
 * and summary field required by notebook rendering is present.
 */
export type NotebookFileDiff = z.infer<typeof NotebookFileDiffSchema>;

const FileDiffSchema = z.union([NotebookFileDiffSchema, TextFileDiffSchema]);

/**
 * Represents either stable backend shape returned by `/api/file-diff`.
 *
 * Consumers narrow through the notebook discriminator's presence. They must not
 * require a new text discriminator or accept optional-field placeholder shapes.
 */
export type FileDiff = z.infer<typeof FileDiffSchema>;

const ErrorResponseSchema = z.strictObject({
  error: z.string(),
});

const HttpExceptionResponseSchema = z.strictObject({
  detail: z.string(),
});

const REQUEST_TIMEOUT_MS = 8_000;
const SLOW_DIFF_TIMEOUT_MS = 20_000;
const PULL_REQUEST_TIMEOUT_MS = 60_000;

/**
 * Describes every required input to one private HTTP request.
 *
 * `signal` is genuinely nullable because TanStack queries provide cancellation
 * while mutation commands do not. The remaining fields are always explicit so
 * transport behavior is never selected through omitted arguments.
 */
type HttpRequest = {
  input: string;
  init: RequestInit;
  signal: AbortSignal | null;
  timeoutMs: number;
};

/**
 * Exposes the combined request signal and its owned timeout lifecycle.
 *
 * Callers must invoke `dispose` exactly once after the request settles. The
 * timeout classifier reports only whether this owner initiated cancellation.
 */
type RequestSignal = {
  signal: AbortSignal;
  dispose(): void;
  timedOut(): boolean;
};

/**
 * Classifies transport errors for Toast expiration policy.
 *
 * Timeout failures may expire; every other request failure remains persistent.
 */
type RequestErrorReason = "timeout" | "other";

/**
 * Represents a browser or backend HTTP failure with its Toast lifetime reason.
 *
 * Callers receive the complete visible message and may inspect `error_reason`.
 * The class must not convert intentional AbortErrors into request failures.
 */
class RequestError extends Error {
  readonly error_reason: RequestErrorReason;

  /**
   * Constructs one classified request failure from complete transport context.
   *
   * `cause` is null when no underlying exception exists and otherwise preserves
   * the original thrown value. Callers must provide every argument explicitly.
   */
  constructor(
    errorReason: RequestErrorReason,
    message: string,
    cause: unknown | null,
  ) {
    super(message, cause === null ? undefined : { cause });
    this.name = "RequestError";
    this.error_reason = errorReason;
  }
}

/**
 * Combines a caller cancellation signal with one transport-owned timeout.
 *
 * The caller supplies either a real upstream signal or explicit null and a
 * positive timeout. The returned owner forwards cancellation and removes its
 * listener and timer when disposed.
 */
function combinedSignal(
  upstreamSignal: AbortSignal | null,
  timeoutMs: number,
): RequestSignal {
  const controller = new AbortController();
  let didTimeout = false;

  /**
   * Forwards cancellation from the caller into the transport-owned controller.
   *
   * This callback is installed only when an upstream signal exists and forwards
   * that signal's exact reason. It owns no independent cancellation or cleanup;
   * the enclosing signal owner installs and removes it.
   */
  function abortFromUpstream(): void {
    if (upstreamSignal === null) {
      return;
    }
    controller.abort(upstreamSignal.reason);
  }

  if (upstreamSignal !== null) {
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
    dispose() {
      window.clearTimeout(timeoutId);
      if (upstreamSignal !== null) {
        upstreamSignal.removeEventListener("abort", abortFromUpstream);
      }
    },
    timedOut() {
      return didTimeout;
    },
  };
}

/**
 * Converts one non-successful HTTP response into a complete RequestError.
 *
 * The response body is consumed exactly once. Known JSON error envelopes expose
 * their message; unknown or plain-text bodies remain dramatically visible.
 */
async function throwResponseError(response: Response): Promise<never> {
  const bodyText = await response.text();
  if (bodyText.length === 0) {
    throw new RequestError(
      "other",
      `Request failed with status ${response.status} ${response.statusText}.`,
      null,
    );
  }

  try {
    const payload: unknown = JSON.parse(bodyText);
    const parsedError = ErrorResponseSchema.safeParse(payload);
    if (parsedError.success) {
      throw new RequestError("other", parsedError.data.error, null);
    }
    const parsedDetail = HttpExceptionResponseSchema.safeParse(payload);
    if (parsedDetail.success) {
      throw new RequestError("other", parsedDetail.data.detail, null);
    }
  } catch (error) {
    if (!(error instanceof SyntaxError)) {
      throw error;
    }
  }

  throw new RequestError("other", bodyText, null);
}

/**
 * Performs one HTTP request with explicit cancellation and timeout ownership.
 *
 * Callers provide the complete HttpRequest. Successful and unsuccessful HTTP
 * responses are returned unchanged; pre-response failures become classified
 * RequestErrors while intentional upstream cancellation remains AbortError.
 */
async function requestResponse(request: HttpRequest): Promise<Response> {
  const ownedSignal = combinedSignal(request.signal, request.timeoutMs);
  try {
    return await fetch(request.input, {
      ...request.init,
      signal: ownedSignal.signal,
    });
  } catch (error) {
    // Error copy names only the endpoint and never exposes request query values.
    const queryStart = request.input.indexOf("?");
    const label =
      queryStart === -1 ? request.input : request.input.slice(0, queryStart);
    if (ownedSignal.timedOut()) {
      throw new RequestError(
        "timeout",
        `Request timed out before response: ${label}`,
        null,
      );
    }
    // Intentional browser cancellation remains AbortError for query ownership.
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new RequestError(
      "other",
      `Request failed before response: ${label}`,
      error,
    );
  } finally {
    ownedSignal.dispose();
  }
}

/**
 * Performs one JSON request and validates its complete successful response.
 *
 * Callers provide an explicit request and the authoritative Zod schema. Invalid
 * success data throws its validation error; unsuccessful responses throw their
 * complete backend or HTTP error without substituting defaults.
 */
async function requestJson<T>(
  request: HttpRequest,
  schema: z.ZodType<T>,
): Promise<T> {
  const response = await requestResponse(request);
  if (!response.ok) {
    return throwResponseError(response);
  }
  return schema.parse(await response.json());
}

/**
 * Performs one command whose successful response intentionally has no body.
 *
 * The caller provides the complete request. HTTP failures remain visible and a
 * success resolves only after the backend confirms the command.
 */
async function requestEmpty(request: HttpRequest): Promise<void> {
  const response = await requestResponse(request);
  if (!response.ok) {
    return throwResponseError(response);
  }
}

/**
 * Requests the complete repository list used by repository selectors.
 *
 * The caller owns cancellation and receives only schema-validated repository
 * marks. This function neither selects a repository nor caches the response.
 */
function requestRepos(signal: AbortSignal): Promise<RepoMark[]> {
  return requestJson(
    {
      input: "/api/repos",
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    z.array(RepoMarkSchema),
  );
}

/**
 * Requests all refs, local branches, and remotes for one repository.
 *
 * Callers provide a real backend project ID and the query cancellation signal.
 * Selection, autocomplete filtering, and cache freshness remain UI concerns.
 */
function requestRepoRefs(
  projectId: ProjectId,
  signal: AbortSignal,
): Promise<RepoRefs> {
  const params = new URLSearchParams({ project_id: String(projectId) });
  return requestJson(
    {
      input: `/api/repo-refs?${params.toString()}`,
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoRefsSchema,
  );
}

/**
 * Requests the backend-selected branch-review defaults for one repository.
 *
 * The project must already exist and the caller owns cancellation. The result
 * is validated as a complete entity and is not merged with local input state.
 */
function requestRepoDefaults(
  projectId: ProjectId,
  signal: AbortSignal,
): Promise<RepoDefaults> {
  const params = new URLSearchParams({ project_id: String(projectId) });
  return requestJson(
    {
      input: `/api/repo-defaults?${params.toString()}`,
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoDefaultsSchema,
  );
}

/**
 * Removes exactly one saved repository mark by its backend project ID.
 *
 * Completion means the backend accepted the command. Cache invalidation and
 * any replacement repository selection are responsibilities of the caller.
 */
function requestRemoveRepo(projectId: ProjectId): Promise<void> {
  return requestEmpty({
    input: `/api/repos/${projectId}`,
    init: { method: "DELETE" },
    signal: null,
    timeoutMs: REQUEST_TIMEOUT_MS,
  });
}

/**
 * Saves one repository's complete main-branch selection.
 *
 * Callers provide both the repository identity and an explicit local or remote
 * selection. The validated backend entity is returned without updating caches.
 */
function requestSaveMainBranch(input: {
  projectId: ProjectId;
  selection: BranchSelection;
}): Promise<RepoMainBranch> {
  return requestJson(
    {
      input: `/api/repos/${input.projectId}/main-branch`,
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ selection: input.selection }),
      },
      signal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoMainBranchSchema,
  );
}

/**
 * Requests the complete bounded preset catalog collection.
 *
 * The caller supplies query cancellation and receives every preset kind in one
 * validated response. This function performs no tab or subset selection.
 */
function requestPresets(signal: AbortSignal): Promise<PresetCatalogs> {
  return requestJson(
    {
      input: "/api/presets",
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PresetCatalogsSchema,
  );
}

/**
 * Registers a user profile with the exact submitted username.
 *
 * The caller is responsible for validating interaction-level input and for
 * selecting or persisting the returned profile after backend confirmation.
 */
function requestRegisterProfile(username: string): Promise<UserProfile> {
  return requestJson(
    {
      input: "/api/user-profile",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username }),
      },
      signal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    UserProfileSchema,
  );
}

/**
 * Renames one existing profile selected by its exact backend ID.
 *
 * Callers provide the complete rename command. The validated updated profile
 * is returned, while cache and local-storage updates remain caller-owned.
 */
function requestRenameProfile(input: {
  profileId: number;
  username: string;
}): Promise<UserProfile> {
  return requestJson(
    {
      input: `/api/user-profile/${input.profileId}`,
      init: {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: input.username }),
      },
      signal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    UserProfileSchema,
  );
}

/**
 * Requests backend preferences for one selected profile.
 *
 * A concrete profile ID and cancellation signal are required. This function
 * does not supply defaults when the backend response is absent or malformed.
 */
function requestPreferences(
  profileId: number,
  signal: AbortSignal,
): Promise<Preferences> {
  return requestJson(
    {
      input: `/api/user-profile/${profileId}/preferences`,
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
}

/**
 * Saves the complete editable preference entity for one profile.
 *
 * Callers provide an explicit profile ID and aggressive-folds value. The
 * validated backend result is returned without mutating UI-owned state.
 */
function requestSavePreferences(input: {
  profileId: number;
  aggressiveFolds: boolean;
}): Promise<Preferences> {
  return requestJson(
    {
      input: `/api/user-profile/${input.profileId}/preferences`,
      init: {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ aggressive_folds: input.aggressiveFolds }),
      },
      signal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
}

/**
 * Resolves one pull-request URL into authoritative repository and ref state.
 *
 * The complete URL is sent to the backend. Callers must replace workspace
 * selection from the validated result rather than preserving conflicting data.
 */
function requestPreparePullRequest(url: string): Promise<PreparedPullRequest> {
  return requestJson(
    {
      input: "/api/pull-request/prepare",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ url }),
      },
      signal: null,
      timeoutMs: PULL_REQUEST_TIMEOUT_MS,
    },
    PreparedPullRequestSchema,
  );
}

/**
 * Encodes complete DiffParams for the snapshot-producing manifest endpoint.
 *
 * Every discriminated variant is translated into the stable backend contract.
 * This function performs encoding only and never changes or defaults selection.
 */
function manifestSearchParams(params: DiffParams): URLSearchParams {
  const search = new URLSearchParams({
    engine: params.engine,
    mode: params.mode,
    project_id: String(params.project_id),
  });
  switch (params.mode) {
    case "preset":
      search.set("preset_subset", params.preset_subset);
      return search;
    case "branch-review":
      search.set("base_source", params.base_selection.source);
      search.set("base_branch", params.base_selection.branch);
      if (params.base_selection.source === "remote") {
        search.set("base_remote", params.base_selection.remote);
      }
      search.set("review_source", params.review_selection.source);
      search.set("review_branch", params.review_selection.branch);
      if (params.review_selection.source === "remote") {
        search.set("review_remote", params.review_selection.remote);
      }
      return search;
    case "head":
      search.set("left", params.left);
      search.set("right", params.right);
      search.set("show_untracked", "true");
      return search;
    case "refs":
      search.set("left", params.left);
      search.set("right", params.right);
      return search;
  }
}

/**
 * Encodes the immutable snapshot identity shared by lazy and file endpoints.
 *
 * Callers provide the original DiffParams and backend cache ID. File paths and
 * engine-specific fields are deliberately appended by the endpoint that owns them.
 */
function cachedSearchParams(
  params: DiffParams,
  cacheId: string,
): URLSearchParams {
  const search = new URLSearchParams({
    mode: params.mode,
    cache_id: cacheId,
    project_id: String(params.project_id),
  });
  if (params.mode === "preset") {
    search.set("preset_subset", params.preset_subset);
  }
  return search;
}

/**
 * Requests one immutable ChangeSet manifest for complete DiffParams.
 *
 * The query owns cancellation and receives a validated thin manifest. This
 * function does not start file requests or enrich manifest handles.
 */
function requestManifest(
  params: DiffParams,
  signal: AbortSignal,
): Promise<Manifest> {
  const search = manifestSearchParams(params);
  return requestJson(
    {
      input: `/api/manifest?${search.toString()}`,
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    ManifestSchema,
  );
}

/**
 * Requests delayed-file metadata for one immutable manifest cache.
 *
 * The original DiffParams, cache ID, and query signal are required. The result
 * describes lazy presentation only and does not trigger explicit file loading.
 */
function requestLazyInfo(
  params: DiffParams,
  cacheId: string,
  signal: AbortSignal,
): Promise<LazyInfo> {
  const search = cachedSearchParams(params, cacheId);
  return requestJson(
    {
      input: `/api/lazy-info?${search.toString()}`,
      init: {},
      signal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    LazyInfoSchema,
  );
}

/**
 * Requests one complete rendered file addressed by a manifest handle.
 *
 * Callers provide snapshot identity, cache identity, the exact manifest entry,
 * and cancellation. The engine determines timeout without altering query identity.
 */
function requestFileDiff(
  params: DiffParams,
  cacheId: string,
  entry: ManifestEntry,
  signal: AbortSignal,
): Promise<FileDiff> {
  const search = cachedSearchParams(params, cacheId);
  search.set("engine", params.engine);
  if (entry.left_path !== null && entry.left_path.length > 0) {
    search.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null && entry.right_path.length > 0) {
    search.set("right_path", entry.right_path);
  }
  const timeoutMs =
    params.engine === "difftastic" || params.engine === "gumtree"
      ? SLOW_DIFF_TIMEOUT_MS
      : REQUEST_TIMEOUT_MS;
  return requestJson(
    {
      input: `/api/file-diff?${search.toString()}`,
      init: {},
      signal,
      timeoutMs,
    },
    FileDiffSchema,
  );
}

const snapshotQuery = {
  staleTime: Infinity,
  retry: false,
} as const;

/**
 * Provides every canonical backend query and mutation definition.
 *
 * Consumers call facade methods and pass the returned definitions to TanStack.
 * They must not invoke private request functions, invent parallel query keys, or
 * copy returned backend data into Solid state.
 */
export const api = {
  changeSet: {
    key: ["change-set"] as const,

    /**
     * Defines the immutable manifest query for complete DiffParams.
     *
     * Consumers pass this definition to TanStack when a ChangeSet is ready.
     * Creating the definition alone does not start network work.
     */
    manifest(params: DiffParams) {
      return queryOptions({
        queryKey: ["change-set", "manifest", params] as const,
        queryFn: ({ signal }) => requestManifest(params, signal),
        meta: { errorTitle: "Failed to load ChangeSet manifest" },
        ...snapshotQuery,
      });
    },

    /**
     * Defines delayed-file metadata for one immutable manifest cache.
     *
     * Consumers supply the original parameters and cache ID. Query observation,
     * enabling, and explicit loading remain responsibilities of the ChangeSet.
     */
    lazyInfo(params: DiffParams, cacheId: string) {
      return queryOptions({
        queryKey: ["change-set", "lazy-info", params, cacheId] as const,
        queryFn: ({ signal }) => requestLazyInfo(params, cacheId, signal),
        meta: { errorTitle: "Failed to load delayed-file information" },
        ...snapshotQuery,
      });
    },

    /**
     * Defines one canonical rendered-file query from a manifest handle.
     *
     * Snapshot parameters, cache ID, and entry paths form stable query identity.
     * The definition does not decide sequential scheduling or rich/lazy state.
     */
    file(params: DiffParams, cacheId: string, entry: ManifestEntry) {
      const locator = {
        left_path: entry.left_path,
        right_path: entry.right_path,
      };
      return queryOptions({
        queryKey: ["change-set", "file", params, cacheId, locator] as const,
        queryFn: ({ signal }) =>
          requestFileDiff(params, cacheId, entry, signal),
        meta: { errorTitle: "Failed to load file diff" },
        ...snapshotQuery,
      });
    },
  },

  repos: {
    /**
     * Defines the shared repository-list query with short measured freshness.
     *
     * Any mounted repository selector may observe this definition. TanStack owns
     * deduplication and freshness; consumers own selection and interaction.
     */
    list() {
      return queryOptions({
        queryKey: ["repos"] as const,
        queryFn: ({ signal }) => requestRepos(signal),
        staleTime: 5_000,
        meta: { errorTitle: "Failed to load repositories" },
      });
    },

    /**
     * Defines shared refs, branches, and remotes for one repository.
     *
     * Consumers must provide the selected project ID. The returned definition
     * supports stale-time-guarded warmups and explicit refetch by its observer.
     */
    refs(projectId: ProjectId) {
      return queryOptions({
        queryKey: ["repos", projectId, "refs"] as const,
        queryFn: ({ signal }) => requestRepoRefs(projectId, signal),
        staleTime: 30_000,
        meta: { errorTitle: "Failed to load refs" },
      });
    },

    /**
     * Defines backend branch defaults for one selected repository.
     *
     * Defaults remain fresh until explicit invalidation or full provider reset.
     * The definition never overwrites input that the user has already edited.
     */
    defaults(projectId: ProjectId) {
      return queryOptions({
        queryKey: ["repos", projectId, "defaults"] as const,
        queryFn: ({ signal }) => requestRepoDefaults(projectId, signal),
        staleTime: Infinity,
        meta: { errorTitle: "Failed to load repository defaults" },
      });
    },

    /**
     * Defines the mutation that removes one repository mark.
     *
     * Consumers pass the required project ID to `mutate`. They remain responsible
     * for invalidating repository data and repairing global selection afterward.
     */
    remove() {
      return mutationOptions({
        mutationKey: ["repos", "remove"] as const,
        mutationFn: requestRemoveRepo,
        meta: { errorTitle: "Failed to remove repository" },
      });
    },

    /**
     * Defines the mutation that saves one repository main-branch selection.
     *
     * Consumers pass the complete project-and-selection entity to `mutate` and
     * reconcile the returned backend state through the canonical query cache.
     */
    saveMainBranch() {
      return mutationOptions({
        mutationKey: ["repos", "save-main-branch"] as const,
        mutationFn: requestSaveMainBranch,
        meta: { errorTitle: "Failed to save main branch" },
      });
    },
  },

  presets: {
    /**
     * Defines the shared bounded preset-catalog query.
     *
     * Preset controls may observe this single definition and rely on TanStack for
     * freshness and deduplication; current kind and subset remain local UI state.
     */
    catalogs() {
      return queryOptions({
        queryKey: ["presets"] as const,
        queryFn: ({ signal }) => requestPresets(signal),
        staleTime: 5_000,
        meta: { errorTitle: "Failed to load presets" },
      });
    },
  },

  profile: {
    /**
     * Defines backend preferences for one concrete profile.
     *
     * Consumers must provide a selected profile ID and may not observe this query
     * before one exists. Backend absence or invalidity remains a visible error.
     */
    preferences(profileId: number) {
      return queryOptions({
        queryKey: ["profile", profileId, "preferences"] as const,
        queryFn: ({ signal }) => requestPreferences(profileId, signal),
        meta: { errorTitle: "Failed to load preferences" },
      });
    },

    /**
     * Defines the mutation that registers a profile from a username.
     *
     * Consumers pass the required username and own selection, persistence, and
     * cache updates after the backend returns the validated profile.
     */
    register() {
      return mutationOptions({
        mutationKey: ["profile", "register"] as const,
        mutationFn: requestRegisterProfile,
        meta: { errorTitle: "Failed to register profile" },
      });
    },

    /**
     * Defines the mutation that renames one existing profile.
     *
     * Consumers pass the complete profile ID and username command and reconcile
     * the returned profile without inventing optimistic backend state.
     */
    rename() {
      return mutationOptions({
        mutationKey: ["profile", "rename"] as const,
        mutationFn: requestRenameProfile,
        meta: { errorTitle: "Failed to rename profile" },
      });
    },

    /**
     * Defines the mutation that saves one complete preferences entity.
     *
     * Consumers pass every editable preference with the profile ID. Cache updates
     * occur only from the backend-confirmed result.
     */
    savePreferences() {
      return mutationOptions({
        mutationKey: ["profile", "save-preferences"] as const,
        mutationFn: requestSavePreferences,
        meta: { errorTitle: "Failed to save preferences" },
      });
    },
  },

  pullRequest: {
    /**
     * Defines the mutation that prepares one pull-request URL.
     *
     * Consumers pass the required URL and must treat the validated repository and
     * refs as authoritative workspace replacement after success.
     */
    prepare() {
      return mutationOptions({
        mutationKey: ["pull-request", "prepare"] as const,
        mutationFn: requestPreparePullRequest,
        meta: { errorTitle: "Failed to prepare pull request" },
      });
    },
  },
} as const;
