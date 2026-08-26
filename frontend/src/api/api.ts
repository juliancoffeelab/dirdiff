/**
 * Defines the complete typed boundary between the browser UI and Python API.
 *
 * The module exports backend value types and the single `api` facade containing
 * canonical TanStack query and mutation definitions. It privately contains runtime
 * response validation, HTTP request construction, timeout handling, query keys,
 * and request functions. It must not contain UI state, component behavior, query
 * observers, Toast presentation, or ChangeSet file-fetch sequencing.
 */
import { mutationOptions, queryOptions } from "@tanstack/solid-query";
import { z } from "zod";

const DiffEngineSchema = z.enum([
  "dirdiff",
  "git",
  "difftastic",
  "gumtree",
  "tokendiff",
]);

/**
 * Selects the backend diff implementation used for every requested file.
 *
 * Callers pass the engine to file requests separately from DiffParams because
 * manifest observation does not render files. The value must not represent
 * inline/split view.
 */
export type DiffEngine = z.infer<typeof DiffEngineSchema>;

/**
 * Reports whether the engine's renders are heavy enough to need protection.
 *
 * Heavy engines take long enough per attempt to need the slow initial
 * transport timeout, and concurrent requests degrade the backend outright
 * (difftastic measured 2.4x slower total load at three in flight), so the
 * file lane never prefetches them. Only engines measured to tolerate
 * concurrent renders are named light (dirdiff 23% and git 14% faster total
 * load with prefetch — git spawns per file too, so heaviness is decided by
 * measurement, not process boundary); an engine added later is heavy until
 * measured otherwise.
 */
export function isHeavyEngine(engine: DiffEngine): boolean {
  return !(engine === "dirdiff" || engine === "git");
}

/**
 * Identifies one backend preset catalog and its corresponding preset project.
 *
 * The value is both the Preset Tab kind and the `project_id` used by preset
 * DiffParams. It must not identify a repository-backed project.
 *
 * The set of catalogs is a backend directory listing, so this is not an
 * enumeration here. A value is legitimate only when it is the `id` of a
 * catalog the catalog query returned; the frontend must not invent one, and
 * one read from the URL is checked against that listing before it is used.
 */
export type PresetType = string;

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
 * derives the remaining display data from this backend value.
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
 * identity only and must not absorb backend preferences.
 */
export type UserProfile = z.infer<typeof UserProfileSchema>;

const PreferencesSchema = z.strictObject({
  user_profile_id: z.number().int().positive(),
  aggressive_folds: z.boolean(),
});

/**
 * Describes backend preferences for one user profile.
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

const RemoteBranchRefSchema = z.strictObject({
  structured: z.strictObject({
    remote: z.string(),
    branch: z.string(),
  }),
  gitref: z.string(),
});

const BuiltinRefSchema = z.enum(["HEAD", "index", "worktree"]);

/**
 * Identifies one built-in Git ref supported by repository autocomplete.
 *
 * The backend may return only these established values. Consumers may rely on
 * every value having its corresponding application description; arbitrary Git
 * refs and repository branch names must use the other RefChoices categories.
 */
export type BuiltinRef = z.infer<typeof BuiltinRefSchema>;

const RefChoicesSchema = z.strictObject({
  builtins: z.array(BuiltinRefSchema),
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

const RepoDefaultsResponseSchema = z.strictObject({
  default_base_selection: z.union([
    BranchSelectionSchema,
    z.strictObject({
      kind: z.literal("error"),
      error: z.literal("heuristic_fail"),
    }),
  ]),
  preferred_review_selection: BranchSelectionSchema,
});

const RepoDefaultsSchema = z.strictObject({
  default_base_selection: BranchSelectionSchema,
  preferred_review_selection: BranchSelectionSchema,
});

/**
 * Contains the two complete branch-review defaults for one repository.
 *
 * The query rejects a backend heuristic failure instead of representing it as
 * defaults data. Successful values remain realtime inputs for untouched controls
 * and must not overwrite user-edited autocomplete input.
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

const PreparedPullRequestSchema = z.strictObject({
  project_id: z.number().int().positive(),
  pull_request_url: z.string().min(1),
  left_commit: z.string().min(1),
  right_commit: z.string().min(1),
});

/**
 * Contains the authoritative repository, Room identity, and commits prepared
 * from one Pull Request URL.
 *
 * The URL participates only in Pull Request correspondence. The commits
 * participate only in manifest capture. Callers must preserve the complete value
 * and must not convert it into Branch Review selections.
 */
export type PreparedPullRequest = z.infer<typeof PreparedPullRequestSchema>;

const PresetGroupSchema = z.strictObject({
  id: z.string().min(1),
  display_name: z.string().min(1),
});

/**
 * Describes one selectable preset within a catalog.
 *
 * `id` is sent as `preset_subset`; `display_name` is presentation data. The
 * group contains no manifest or rendered-file content.
 */
export type PresetGroup = z.infer<typeof PresetGroupSchema>;

const PresetCatalogSchema = z.strictObject({
  id: z.string().min(1),
  name: z.string().min(1),
  default_preset: z.string().min(1),
  groups: z.array(PresetGroupSchema),
});

/**
 * Describes one preset catalog: how to select it, its caption, its groups.
 *
 * `id` is sent as `project_id`; `name` is the caption the picker draws and
 * selects nothing. The catalog is directory metadata only. Consumers must
 * request a ChangeSet separately after the user selects one group.
 */
export type PresetCatalog = z.infer<typeof PresetCatalogSchema>;

const PresetCatalogsSchema = z.array(PresetCatalogSchema);

/**
 * Contains every preset catalog the backend currently offers, in its order.
 *
 * This is a list because the backend's catalogs are a directory listing, so
 * neither their number nor their names are known to this schema. Consumers
 * find a catalog by matching `id` and must not copy catalogs into component
 * state merely to expose them to Preset controls.
 */
export type PresetCatalogs = z.infer<typeof PresetCatalogsSchema>;

/**
 * Contains fields shared by every repository-backed manifest request.
 *
 * The project is always required. Preset requests use their own complete
 * parameter shape because their project identity is a PresetType.
 */
type RepoBackedDiffParams = {
  project_id: ProjectId;
};

/**
 * Requests the current HEAD against the complete worktree, including untracked
 * files.
 *
 * Callers construct the complete fixed Tab fields; none are optional or
 * inferred by the transport layer.
 */
export type HeadDiffParams = RepoBackedDiffParams & {
  tab: "head";
  left: "HEAD";
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
  tab: "refs";
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
  tab: "branch-review";
  base_selection: BranchSelection;
  review_selection: BranchSelection;
};

/**
 * Requests the repository state prepared for one Pull Request.
 *
 * The URL is the complete Room correspondence key. The two commits are capture
 * inputs and must never be represented as Branch Review selections.
 */
export type PullRequestDiffParams = RepoBackedDiffParams & {
  tab: "pull-request";
  pull_request_url: string;
  left_commit: string;
  right_commit: string;
};

/**
 * Requests one selected preset fixture.
 *
 * The preset kind is the backend project ID and `preset_subset` is the selected
 * catalog group. Repository selection is deliberately absent.
 */
export type PresetDiffParams = {
  project_id: PresetType;
  tab: "preset";
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
  | PullRequestDiffParams
  | PresetDiffParams;

const ManifestSummarySchema = z
  .strictObject({
    changed_files: z.number().int(),
    added_files: z.number().int(),
    removed_files: z.number().int(),
    updated_files: z.number().int(),
    added_lines: z.number().int().nullable(),
    removed_lines: z.number().int().nullable(),
    skipped_files: z.number().int(),
    changed_cells: z.number().int().nullable(),
    added_cells: z.number().int().nullable(),
    removed_cells: z.number().int().nullable(),
    modified_cells: z.number().int().nullable(),
  })
  .superRefine((summary, context) => {
    if ((summary.added_lines === null) !== (summary.removed_lines === null)) {
      context.addIssue({
        code: "custom",
        message: "added_lines and removed_lines must have equal presence",
      });
    }
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

const FilePathSchema = z.string().min(1);

/**
 * Rejects a backend file identity that has neither a left nor right path.
 *
 * Response schemas that include a file identity call this after validating
 * each present path as non-empty. The callback adds one schema issue for a
 * completely absent identity and must not infer one side from the other or
 * alter the decoded response.
 */
function validateFilePaths(
  paths: { left_path: string | null; right_path: string | null },
  context: z.RefinementCtx,
): void {
  if (paths.left_path === null && paths.right_path === null) {
    context.addIssue({
      code: "custom",
      message: "File identity requires a left or right path.",
    });
  }
}

const ManifestEntrySchema = z
  .strictObject({
    file_kind: FileKindSchema,
    left_path: FilePathSchema.nullable(),
    right_path: FilePathSchema.nullable(),
    lazy: LazyReasonSchema.nullable(),
  })
  .superRefine(validateFilePaths);

/**
 * Provides the complete thin handle for one manifest file.
 *
 * At least one path is present and every present path is non-empty. Paths, kind
 * and lazy reason are sufficient for later file endpoints. The handle deliberately
 * contains no rendered rows, file summary, or copied state.
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
 * `path` is the stable expansion key and `entries` preserve manifest order. A
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
  snapshot_id: z.string().regex(/^[0-9a-f]{32}$/),
  display_name: z.string(),
  left_label: z.string(),
  right_label: z.string(),
  summary: ManifestSummarySchema,
  tree: z.array(ManifestNodeSchema),
});

/**
 * Describes one ordered immutable ChangeSet snapshot returned by the manifest
 * endpoint.
 *
 * The snapshot ID isolates all subsequent file queries. The tree remains thin and
 * must not be mutated with progressive file results.
 */
export type Manifest = z.infer<typeof ManifestSchema>;

const LazyInfoFileSchema = z
  .strictObject({
    file_kind: FileKindSchema,
    left_path: FilePathSchema.nullable(),
    right_path: FilePathSchema.nullable(),
    display_name: z.string(),
    changed_lines: z.number().int().nullable(),
    added_lines: z.number().int().nullable(),
    removed_lines: z.number().int().nullable(),
    lazy: LazyReasonSchema.nullable(),
  })
  .superRefine(validateFilePaths);

/**
 * Contains the complete lightweight presentation data for one delayed file.
 *
 * Every field comes from `/api/lazy-info`. At least one path is present and each
 * present path is non-empty; callers must not fill missing values from the
 * manifest or a failed file request.
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

const DecoratedPartSchema = z.strictObject({
  text: z.string(),
  syntax_classes: z.array(z.string()),
  diff_status: z.enum(["unchanged", "replace", "insert", "delete", "move"]),
  is_whitespace: z.boolean(),
  is_leading_whitespace: z.boolean(),
});

/**
 * Describes one backend-produced text slice with complete display decoration.
 *
 * Ordered parts reconstruct one row side exactly. Consumers render their text,
 * syntax classes, diff status, and whitespace metadata directly rather than
 * intersecting another token or offset representation.
 */
export type DecoratedPart = z.infer<typeof DecoratedPartSchema>;

const DiffRowSchema = z.strictObject({
  status: RowStatusSchema,
  left_no: z.number().int().positive().nullable(),
  right_no: z.number().int().positive().nullable(),
  left_text: z.string().nullable(),
  right_text: z.string().nullable(),
  left_parts: z.array(DecoratedPartSchema),
  right_parts: z.array(DecoratedPartSchema),
  hunk_index: z.number().int().nonnegative().nullable(),
});

/**
 * Contains one complete backend-aligned row for text or notebook source.
 *
 * Nullable side fields represent genuinely absent lines; present line numbers
 * are positive backend coordinates. `hunk_index` is the backend-provided
 * navigation identity and must not be reconstructed from rows.
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

const BayWarningSchema = z.strictObject({
  type: z.string().min(1),
  message: z.string(),
});

/**
 * Describes non-fatal engine or format damage attached to one bay.
 *
 * Callers display the complete backend message while continuing to render valid
 * file data. It must not be treated as a request error or hidden Toast.
 */
export type BayWarning = z.infer<typeof BayWarningSchema>;

const BayStatsSchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  moved_lines: z.number().int(),
});

/**
 * Contains one bay's engine line counts before file-level aggregation.
 *
 * These belong to the bay and its collapsed placeholder. Side existence is a
 * File fact and lives on the composed-diff `summary`, not here.
 */
export type BayStats = z.infer<typeof BayStatsSchema>;

const ChangeStatusSchema = z.strictObject({
  kind: z.enum(["added", "removed", "changed", "unchanged"]),
});

const MovedChangeStatusSchema = z.strictObject({
  kind: z.literal("moved"),
  from_heading: z.string().nullable(),
  to_heading: z.string().nullable(),
});

const TextKindPayloadSchema = z.strictObject({
  kind: z.literal("text"),
  left_label: z.string(),
  right_label: z.string(),
  rows: z.array(DiffRowSchema),
  fold_hints: z.array(FoldHintSchema),
  stats: BayStatsSchema,
});

/**
 * Contains what a `text` bay holds: rows decorated by the shared renderer.
 *
 * `left_label`/`right_label` are the two side headings inside the grid,
 * distinct from the bay's own `label`. Rows, fold hints, stats, and the engine
 * warning live here rather than on the bay because they exist only where an
 * engine ran, so a holder of an image bay cannot reach for them.
 */
export type TextKindPayload = z.infer<typeof TextKindPayloadSchema>;

const MediaRefSchema = z.strictObject({
  media_type: z.string().min(1),
  byte_size: z.number().int().nonnegative(),
  digest: z.string().min(1),
});

/**
 * Describes one captured media side without carrying its bytes.
 *
 * This is everything an image bay says about captured content: enough for a
 * widget to know the side exists and to request it. The bytes come from
 * `/api/file-media`, addressed by Snapshot, side, and File pair — never inline
 * in the payload. The same three facts also reach the reviewer as rows, in the
 * `text` bay stating them.
 */
export type MediaRef = z.infer<typeof MediaRefSchema>;

const ImageKindPayloadSchema = z.strictObject({
  kind: z.literal("image"),
  left: MediaRefSchema.nullable(),
  right: MediaRefSchema.nullable(),
});

/**
 * Contains what an `image` bay holds: two optional references to pictures.
 *
 * A `null` side is a File that was added or removed, and must read as absent
 * rather than as an empty picture frame. The bay carries no dimensions: the
 * widget asks the endpoint for the bytes and lets the browser decode them.
 */
export type ImageKindPayload = z.infer<typeof ImageKindPayloadSchema>;

const BayKindPayloadSchema = z.discriminatedUnion("kind", [
  TextKindPayloadSchema,
  ImageKindPayloadSchema,
]);

/**
 * Contains one bay's content, dispatched to a widget by its `kind`.
 *
 * Two variants, because there are two things a reviewer can look at: lines, and
 * a picture. Named facts about bytes — a blob File's only bay, an image File's
 * facts bay — are lines, so they arrive as `text` and need no variant. A new
 * kind is a variant here plus a matching widget; nothing about frames, hunk
 * numbering, or the composed-diff envelope changes to admit it.
 */
export type BayKindPayload = z.infer<typeof BayKindPayloadSchema>;

const BayPayloadSchema = z.strictObject({
  bay_key: z.string().min(1),
  label: z.string(),
  detail: z.string().nullable(),
  collapsible: z.boolean(),
  default_expanded: z.boolean(),
  change: z.discriminatedUnion("kind", [
    ChangeStatusSchema,
    MovedChangeStatusSchema,
  ]),
  warnings: z.array(BayWarningSchema),
  kind_data: BayKindPayloadSchema,
});

/**
 * Represents one bay of a composed diff: its identity, plus what it holds.
 *
 * `bay_key` is the sub-file coordinate shared with line pins and review
 * targets. `label` names the whole bay where it is shown by name, which is its
 * collapsed placeholder and the content column of the inline grid.
 *
 * The discriminator sits one level down, in `kind_data`. Bay chrome, collapse
 * state, navigation, and tinting read this record and never learn the kind;
 * only the frame walk's widget dispatch descends into `kind_data`.
 */
export type BayPayload = z.infer<typeof BayPayloadSchema>;

/**
 * Names what happened to one bay, as the format builder determined it.
 *
 * Only the builder can answer this. A notebook cell that moved and one whose
 * output changed beyond its rendered text both produce rows identical on both
 * sides, so the frontend renders this value — a tint and a status — and never
 * infers it. A `moved` variant carries the name the bay wore in the old and
 * the new document, `null` on a side the builder cannot name; every other
 * outcome is fully told by its `kind`.
 */
export type BayChange = BayPayload["change"];

const FrameSchema = z.strictObject({
  frame_key: z.string().min(1),
  heading: z.string().nullable(),
  // A frame is what its body bay says it is: its tint and its status are both
  // read from `bays[0]`. Composition appends a frame only once a bay exists, so
  // a bay-less frame is a backend bug, and rejecting it here fails the File into
  // its LazyFile error state instead of rendering a silent, untinted heading the
  // reviewer scrolls past as untouched.
  bays: z.array(BayPayloadSchema).min(1),
});

/**
 * Contains one presentational frame: an optional heading over ordered bays.
 *
 * A frame carries no annotations of its own. Everything a reviewer acts on
 * belongs to a bay, which can be navigated to, collapsed, and commented on.
 */
export type Frame = z.infer<typeof FrameSchema>;

const ComposedDiffSchema = z
  .strictObject({
    display_name: z.string(),
    left_label: z.string(),
    right_label: z.string(),
    summary: TextFileSummarySchema,
    file_kind: FileKindSchema,
    left_path: FilePathSchema.nullable(),
    right_path: FilePathSchema.nullable(),
    default_expanded: z.boolean(),
    frames: z.array(FrameSchema),
  })
  .superRefine(validateFilePaths);

/**
 * Contains the single renderable response shape returned by `/api/file-diff`.
 *
 * Every file is one composed diff: File-level metadata plus an ordered list of
 * frames, each holding an ordered list of bays. There is no `render_kind`; a
 * plain text file is one heading-less frame holding one `text` bay keyed
 * `flatfile`.
 * At least one path is present and each present path is non-empty.
 */
export type FileDiff = z.infer<typeof ComposedDiffSchema>;

export const ReviewIdSchema = z.string().regex(/^[0-9a-f]{32}$/u);

/** Identifies one review entity, operation, or retained Snapshot. */
export type ReviewId = z.infer<typeof ReviewIdSchema>;

const ReviewFilePathSchema = z
  .string()
  .min(1)
  .refine(
    (path) =>
      !path.startsWith("/") &&
      path !== "." &&
      path !== ".." &&
      path
        .split("/")
        .every((part) => part !== "" && part !== "." && part !== ".."),
    { message: "Review File paths must be normalized relative names." },
  );

export const ReviewFilePairSchema = z
  .strictObject({
    left_path: ReviewFilePathSchema.nullable(),
    right_path: ReviewFilePathSchema.nullable(),
  })
  .superRefine((pair, context) => {
    if (pair.left_path === null && pair.right_path === null) {
      context.addIssue({
        code: "custom",
        message: "A review File pair requires at least one side.",
      });
    }
  });

/** Identifies one File through the complete nullable manifest pair. */
export type ReviewFilePair = z.infer<typeof ReviewFilePairSchema>;

export const ReviewLineRangeSchema = z
  .strictObject({
    start_line: z.number().int().positive(),
    end_line: z.number().int().positive(),
  })
  .superRefine((range, context) => {
    if (range.end_line < range.start_line) {
      context.addIssue({
        code: "custom",
        message: "Review range end precedes its start.",
      });
    }
  });

/** Identifies one one-based inclusive review line range. */
export type ReviewLineRange = z.infer<typeof ReviewLineRangeSchema>;

/**
 * Names the bay a flatfile composes into.
 *
 * A flatfile is a File no format claims — it has no internal structure, so
 * composition gives it exactly one bay with this key. Callers compare
 * against it instead of testing for a format.
 */
export const FLATFILE_BAY_KEY = "flatfile";

/**
 * Names the picture bay of an image File.
 *
 * An image File composes this bay and the `text` bay stating what its bytes
 * are. The key is deliberately not `FLATFILE_BAY_KEY`: a File that stops being
 * text keeps its stored review targets addressable only if the key they name
 * disappears, so a line range recorded against the old text bay reads as a
 * missing bay rather than landing on a bay with no lines.
 */
export const IMAGE_BAY_KEY = "image";

/**
 * Names the `text` bay stating what an image File's bytes are.
 *
 * A bay key names the classification that produced the bay rather than the kind
 * of the bay, which is why this is not simply `"facts"`: a blob File's bay
 * holds the very same three lines under `BLOB_BAY_KEY`, and one shared key
 * would let a target survive a File changing classification.
 */
export const IMAGE_FACTS_BAY_KEY = "image-facts";

/**
 * Names the `text` bay unreadable content composes into.
 *
 * This is the terminal every File reaches that no other format claimed. Its
 * media type, size, and digest are all that can honestly be shown for it, and
 * those are lines, so the bay holding them is an ordinary `text` bay. The key
 * exists for the same reason and under the same rule as the ones above.
 */
export const BLOB_BAY_KEY = "blob";

/**
 * Builds the URL serving one captured side of one File as its exact bytes.
 *
 * Callers supply the Snapshot the composed diff came from, the same nullable
 * File pair every review and pin coordinate uses, and which side they want.
 * The pair is the address because a renamed File has two different paths and
 * neither identifies it alone. The result is a plain URL for `src` or `href`:
 * the bytes are the browser's to fetch, decode, and cache, and no validated
 * transport wraps a picture.
 */
export function fileMediaUrl(
  snapshotId: string,
  file: ReviewFilePair,
  side: "left" | "right",
): string {
  const search = snapshotSearchParams(snapshotId);
  search.set("side", side);
  if (file.left_path !== null) {
    search.set("left_path", file.left_path);
  }
  if (file.right_path !== null) {
    search.set("right_path", file.right_path);
  }
  return `/api/file-media?${search.toString()}`;
}

export const ReviewTextBaySchema = z.strictObject({
  bay_key: z.string().min(1),
});

/**
 * Identifies one composed bay by the key composition gave it.
 *
 * The key is the universal sub-file coordinate: `"flatfile"` for a File with no
 * internal structure, and the composer's own key for every other bay.
 * Callers pass it through and must not parse it or infer a format from it.
 */
export type ReviewTextBay = z.infer<typeof ReviewTextBaySchema>;

const TextReviewTargetSchema = z
  .strictObject({
    kind: z.literal("text"),
    file: ReviewFilePairSchema,
    bay: ReviewTextBaySchema,
    side: z.enum(["left", "right"]),
    range: ReviewLineRangeSchema,
  })
  .superRefine((target, context) => {
    if (
      (target.side === "left" && target.file.left_path === null) ||
      (target.side === "right" && target.file.right_path === null)
    ) {
      context.addIssue({
        code: "custom",
        message: "The selected review side is absent from the File pair.",
      });
    }
  });
export const ReviewTargetSchema = TextReviewTargetSchema;

/** Identifies one rendered text range. */
export type ReviewTarget = z.infer<typeof ReviewTargetSchema>;

const ReviewAuthorSchema = z.strictObject({
  profile_id: z.number().int().positive(),
  display_name: z.string().min(1),
});

/** Returns one ordinary Profile attribution. */
export type ReviewAuthor = z.infer<typeof ReviewAuthorSchema>;

const ReviewCommentSchema = z
  .strictObject({
    comment_id: ReviewIdSchema,
    sequence: z.number().int().nonnegative(),
    author: ReviewAuthorSchema,
    revision: z.number().int().nonnegative(),
    body: z.string().nullable(),
    deleted: z.boolean(),
    created_at: z.string().datetime({ offset: true }),
    updated_at: z.string().datetime({ offset: true }),
  })
  .superRefine((comment, context) => {
    if (comment.deleted !== (comment.body === null)) {
      context.addIssue({
        code: "custom",
        message: "Deleted Comment state and body presence disagree.",
      });
    }
  });

/** Returns one current Comment or retained deletion tombstone. */
export type ReviewComment = z.infer<typeof ReviewCommentSchema>;

const ThreadPlacementSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("region-kept"),
    range: ReviewLineRangeSchema,
  }),
  z.strictObject({
    kind: z.literal("region-changed"),
    range: ReviewLineRangeSchema,
  }),
  z.strictObject({ kind: z.literal("region-lost") }),
  z.strictObject({ kind: z.literal("bay-lost"), bay: ReviewTextBaySchema }),
  z.strictObject({ kind: z.literal("side-lost") }),
  z.strictObject({ kind: z.literal("file-absent") }),
  z.strictObject({ kind: z.literal("file-unreadable") }),
  z.strictObject({ kind: z.literal("whole-file") }),
]);

/**
 * Identifies where one Thread sits in a Snapshot, and what became of it.
 *
 * Each variant names one derivation outcome and states only what the origin
 * does not: the File pair and side are the origin's in every variant, and the
 * bay is the origin's in all but `bay-lost`. `region-kept` and `whole-file`
 * report nothing wrong; the six others are the complete outdated vocabulary,
 * one name per state.
 */
export type ThreadPlacement = z.infer<typeof ThreadPlacementSchema>;

const ReviewExcerptSchema = z
  .strictObject({
    side: z.enum(["left", "right"]),
    start_line: z.number().int().positive(),
    selected_start_line: z.number().int().positive(),
    selected_end_line: z.number().int().positive(),
    lines: z.array(z.string()).min(1),
  })
  .superRefine((excerpt, context) => {
    const endLine = excerpt.start_line + excerpt.lines.length - 1;
    if (
      excerpt.selected_start_line < excerpt.start_line ||
      excerpt.selected_end_line < excerpt.selected_start_line ||
      excerpt.selected_end_line > endLine
    ) {
      context.addIssue({
        code: "custom",
        message: "Selected review range exceeds its excerpt.",
      });
    }
  });

/** Returns one bounded selected-side excerpt from the origin Snapshot. */
export type ReviewExcerpt = z.infer<typeof ReviewExcerptSchema>;

const TextReviewOriginSchema = TextReviewTargetSchema.extend({
  excerpt: ReviewExcerptSchema,
});
const FileStartReviewOriginSchema = z.strictObject({
  kind: z.literal("file-start"),
  file: ReviewFilePairSchema,
  side: z.enum(["left", "right"]),
});
const ReviewOriginSchema = z.discriminatedUnion("kind", [
  TextReviewOriginSchema,
  FileStartReviewOriginSchema,
]);

/**
 * Identifies the immutable creation target of one Thread.
 *
 * This is the only place a response states a Thread's File pair, bay, and
 * side; a placement repeats none of them. A text origin carries the excerpt
 * cut from its own Snapshot, and a retained historical File-level origin has
 * no excerpt field at all.
 */
export type ReviewOrigin = z.infer<typeof ReviewOriginSchema>;

const ReviewThreadSchema = z.strictObject({
  thread_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  created_at: z.string().datetime({ offset: true }),
  state: z.enum(["open", "resolved", "deleted"]),
  attention: z.enum(["author", "reviewer", "both", "none"]),
  discussion_revision: z.number().int().nonnegative(),
  origin_target: ReviewOriginSchema,
  placement: ThreadPlacementSchema,
  comments: z
    .array(ReviewCommentSchema)
    .min(1)
    .superRefine((comments, context) => {
      const commentIds = new Set<string>();
      comments.forEach((comment, index) => {
        if (comment.sequence !== index) {
          context.addIssue({
            code: "custom",
            message: "Thread Comments must have contiguous sequence order.",
            path: [index, "sequence"],
          });
        }
        if (commentIds.has(comment.comment_id)) {
          context.addIssue({
            code: "custom",
            message: "Thread Comment identities must be unique.",
            path: [index, "comment_id"],
          });
        }
        commentIds.add(comment.comment_id);
      });
    }),
});

/** Returns one runtime-validated discussion through an exact Snapshot. */
export type ReviewThread = z.infer<typeof ReviewThreadSchema>;

/**
 * Reports whether this Snapshot changed what the Thread was written against.
 *
 * `region-kept` and `whole-file` are the placements that state nothing went
 * wrong; every other kind is one of the outdated states. Three call sites in
 * two modules ask this question, so the mapping is stated once here instead
 * of as a kind list repeated at each of them.
 */
export function threadOutdated(thread: ReviewThread): boolean {
  const kind = thread.placement.kind;
  return kind !== "region-kept" && kind !== "whole-file";
}

/** Addresses the exact captured code one Thread navigates to. */
export type ThreadCodePoint = {
  file: ReviewFilePair;
  bay: ReviewTextBay;
  side: "left" | "right";
  line: number;
};

/**
 * Returns the code this Thread navigates to, or `null` if it navigates to none.
 *
 * The File pair and side are the origin's; the bay is the origin's except for
 * a `bay-lost` landing, which states the bay derivation chose instead; the
 * line is the placement's range where it has one, and the bay's first line
 * where it does not. `null` means the placement names no bay at all, so
 * History is that Thread's only home. ChangeSet navigation and History's view
 * control both need this assembly, which is why it is stated once.
 */
export function threadCodePoint(thread: ReviewThread): ThreadCodePoint | null {
  const origin = thread.origin_target;
  if (origin.kind === "file-start") {
    return null;
  }
  const placement = thread.placement;
  switch (placement.kind) {
    case "region-kept":
    case "region-changed":
      return {
        file: origin.file,
        bay: origin.bay,
        side: origin.side,
        line: placement.range.start_line,
      };
    case "region-lost":
      return {
        file: origin.file,
        bay: origin.bay,
        side: origin.side,
        line: 1,
      };
    case "bay-lost":
      return {
        file: origin.file,
        bay: placement.bay,
        side: origin.side,
        line: 1,
      };
    default:
      return null;
  }
}

const ReviewThreadUpdateSchema = z.strictObject({
  thread_id: ReviewIdSchema,
  snapshot_id: ReviewIdSchema,
  state: z.enum(["open", "resolved", "deleted"]),
  attention: z.enum(["author", "reviewer", "both", "none"]),
  discussion_revision: z.number().int().nonnegative(),
  comment: ReviewCommentSchema.nullable(),
});

/** Returns the revision, state, and Comment changed by one accepted action. */
export type ReviewThreadUpdate = z.infer<typeof ReviewThreadUpdateSchema>;

const ReviewThreadPageSchema = z
  .strictObject({
    snapshot_id: ReviewIdSchema,
    through_activity_id: z.number().int().nonnegative(),
    threads: z.array(ReviewThreadSchema),
    page: z.number().int().positive(),
    limit: z.number().int().positive(),
    total_threads: z.number().int().nonnegative(),
    has_more: z.boolean(),
  })
  .superRefine((page, context) => {
    const threadIds = new Set<ReviewId>();
    const commentIds = new Set<ReviewId>();
    page.threads.forEach((thread, index) => {
      if (thread.snapshot_id !== page.snapshot_id) {
        context.addIssue({
          code: "custom",
          message: "Review page contains a Thread from another Snapshot.",
          path: ["threads", index, "snapshot_id"],
        });
      }
      if (threadIds.has(thread.thread_id)) {
        context.addIssue({
          code: "custom",
          message: "Review Snapshot Thread identities must be unique.",
          path: ["threads", index, "thread_id"],
        });
      }
      threadIds.add(thread.thread_id);
      thread.comments.forEach((comment, commentIndex) => {
        if (commentIds.has(comment.comment_id)) {
          context.addIssue({
            code: "custom",
            message: "Review Snapshot Comment identities must be unique.",
            path: ["threads", index, "comments", commentIndex, "comment_id"],
          });
        }
        commentIds.add(comment.comment_id);
      });
    });
  });

/** Returns one explicitly bounded transport page for an exact Snapshot. */
type ReviewThreadPage = z.infer<typeof ReviewThreadPageSchema>;

const ReviewBodySchema = z
  .string()
  .min(1)
  .refine((body) => body.trim().length > 0, {
    message: "Review bodies cannot contain only whitespace.",
  });
const CreateReviewThreadRequestSchema = z.strictObject({
  profile_id: z.number().int().positive(),
  target: ReviewTargetSchema,
  body: ReviewBodySchema,
});
const AddReviewCommentRequestSchema = z.strictObject({
  profile_id: z.number().int().positive(),
  body: ReviewBodySchema,
  attention: z.enum(["inert", "alert"]),
});
const EditReviewCommentRequestSchema = z.strictObject({
  profile_id: z.number().int().positive(),
  body: ReviewBodySchema,
});
const ReviewProfileActionRequestSchema = z.strictObject({
  profile_id: z.number().int().positive(),
});

/** Creates one Thread and its first Comment. */
export type CreateReviewThreadRequest = z.infer<
  typeof CreateReviewThreadRequestSchema
>;
/** Appends one Comment to an existing Thread. */
export type AddReviewCommentRequest = z.infer<
  typeof AddReviewCommentRequestSchema
>;
/** Replaces one authored Comment body. */
export type EditReviewCommentRequest = z.infer<
  typeof EditReviewCommentRequestSchema
>;
/** Attributes one Comment or Thread action to an existing Profile. */
export type ReviewProfileActionRequest = z.infer<
  typeof ReviewProfileActionRequestSchema
>;

const ErrorResponseSchema = z.strictObject({
  error: z.string(),
});

const ReviewErrorCodeSchema = z.enum([
  "profile_not_found",
  "thread_not_found",
  "comment_not_found",
  "invalid_target",
  "revision_conflict",
  "state_conflict",
  "forbidden",
]);
const ReviewErrorResponseSchema = z.strictObject({
  code: ReviewErrorCodeSchema,
  message: z.string().min(1),
});

/** Classifies one stable browser review domain failure. */
export type ReviewErrorCode = z.infer<typeof ReviewErrorCodeSchema>;

const HttpExceptionResponseSchema = z.strictObject({
  detail: z.string(),
});

const REQUEST_TIMEOUT_MS = 8_000;
const SLOW_DIFF_TIMEOUT_MS = 20_000;
const PULL_REQUEST_TIMEOUT_MS = 60_000;

/**
 * Selects the timeout policy for one canonical file-diff HTTP attempt.
 *
 * `bounded` applies the engine-specific initial-attempt limit. `unbounded`
 * disables only the transport timer for an explicit file RetryButton attempt.
 * The value controls execution policy, not file identity, and must never enter
 * the TanStack query key or backend parameters.
 */
export type FileDiffTimeout = "bounded" | "unbounded";

/**
 * Describes every required input to one private HTTP request.
 *
 * `abortSignal` is genuinely nullable because TanStack queries provide
 * cancellation while mutation commands do not. `timeoutMs` is genuinely
 * nullable because explicit file retries and irreversible review writes have
 * no transport timeout. Every field
 * remains explicit so transport behavior is never selected through omitted
 * arguments.
 */
type HttpRequest = {
  input: string;
  init: RequestInit;
  abortSignal: AbortSignal | null;
  timeoutMs: number | null;
};

/**
 * Owns the AbortSignal formed from caller cancellation and an optional timeout.
 *
 * `abortSignal` is the exact browser `AbortSignal` passed to `fetch()`. `dispose()`
 * releases the timeout and caller listener after the HTTP attempt settles,
 * while `timedOut()` distinguishes the owned timer from caller cancellation.
 */
type MultiAbortSignal = {
  abortSignal: AbortSignal;
  dispose(): void;
  timedOut(): boolean;
};

/**
 * Classifies transport errors for Toast expiration policy.
 *
 * Timeout failures may expire, while every other request failure remains
 * visible.
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
 * Represents one validated browser review domain failure.
 *
 * `code` is the stable backend classification and `message` remains presentation
 * text. Consumers must branch on `code` rather than interpreting prose.
 */
export class ReviewRequestError extends RequestError {
  readonly code: ReviewErrorCode;

  /** Constructs one failure from the complete structured review error body. */
  constructor(code: ReviewErrorCode, message: string) {
    super("other", message, null);
    this.name = "ReviewRequestError";
    this.code = code;
  }
}

/**
 * Combines a caller AbortSignal with one transport-owned timeout.
 *
 * The caller supplies either its AbortSignal or explicit null. A
 * numeric timeout must be positive and creates one owned timer; null creates no
 * timer. The returned MultiAbortSignal forwards cancellation and removes its
 * caller AbortSignal listener and timer.
 */
function createMultiAbortSignal(
  callerAbortSignal: AbortSignal | null,
  timeoutMs: number | null,
): MultiAbortSignal {
  const abortController = new AbortController();
  let didTimeout = false;
  if (timeoutMs !== null && (!Number.isFinite(timeoutMs) || timeoutMs <= 0)) {
    throw new Error("HTTP timeout must be a positive finite duration.");
  }

  /**
   * Forwards cancellation from the caller's AbortSignal into the owned controller.
   *
   * This callback is installed only when a caller AbortSignal exists and
   * forwards that AbortSignal's exact reason. It owns no independent cancellation
   * or cleanup; the enclosing MultiAbortSignal installs and removes it.
   */
  function abortFromCallerSignal(): void {
    if (callerAbortSignal === null) {
      return;
    }
    abortController.abort(callerAbortSignal.reason);
  }

  if (callerAbortSignal !== null) {
    if (callerAbortSignal.aborted) {
      abortController.abort(callerAbortSignal.reason);
    } else {
      callerAbortSignal.addEventListener("abort", abortFromCallerSignal, {
        once: true,
      });
    }
  }

  let timeoutId: number | null = null;
  if (timeoutMs !== null) {
    timeoutId = window.setTimeout(() => {
      didTimeout = true;
      abortController.abort();
    }, timeoutMs);
  }

  return {
    abortSignal: abortController.signal,
    /**
     * Releases the timeout and caller listener retained by this instance.
     *
     * `requestResponse` calls this after the HTTP attempt settles. Cleanup does
     * not abort the attempt or change how its failure is classified.
     */
    dispose() {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      if (callerAbortSignal !== null) {
        callerAbortSignal.removeEventListener("abort", abortFromCallerSignal);
      }
    },
    /**
     * Reports whether this instance's transport timer caused cancellation.
     *
     * Callers read the value while classifying a settled attempt. Cancellation
     * forwarded from the caller's AbortSignal always returns false.
     */
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
      `Request failed with HTTP status ${response.status} ${response.statusText}, but the response contained no error body.`,
      null,
    );
  }

  try {
    const payload: unknown = JSON.parse(bodyText);
    const parsedReviewError = ReviewErrorResponseSchema.safeParse(payload);
    if (parsedReviewError.success) {
      throw new ReviewRequestError(
        parsedReviewError.data.code,
        parsedReviewError.data.message,
      );
    }
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
 * Performs one HTTP request with explicit cancellation and timeout cleanup.
 *
 * Callers provide the complete HttpRequest. Successful and unsuccessful HTTP
 * responses are returned unchanged; pre-response failures become classified
 * RequestErrors while intentional upstream cancellation remains AbortError.
 */
async function requestResponse(request: HttpRequest): Promise<Response> {
  const multiAbortSignal = createMultiAbortSignal(
    request.abortSignal,
    request.timeoutMs,
  );
  try {
    return await fetch(request.input, {
      ...request.init,
      signal: multiAbortSignal.abortSignal,
    });
  } catch (error) {
    // Error copy names only the endpoint and never exposes request query values.
    const queryStart = request.input.indexOf("?");
    const label =
      queryStart === -1 ? request.input : request.input.slice(0, queryStart);
    if (multiAbortSignal.timedOut()) {
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
    multiAbortSignal.dispose();
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
 * Requests the complete repository list used by repository selectors.
 *
 * The caller supplies cancellation and receives only schema-validated repository
 * marks. This function neither selects a repository nor caches the response.
 */
function requestRepos(abortSignal: AbortSignal): Promise<RepoMark[]> {
  return requestJson(
    {
      input: "/api/repos",
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    z.array(RepoMarkSchema),
  );
}

/**
 * Requests all refs, local branches, and remotes for one repository.
 *
 * Callers provide a real backend project ID and the query AbortSignal.
 * Selection, autocomplete filtering, and cache freshness remain UI concerns.
 */
function requestRepoRefs(
  projectId: ProjectId,
  abortSignal: AbortSignal,
): Promise<RepoRefs> {
  const params = new URLSearchParams({ project_id: String(projectId) });
  return requestJson(
    {
      input: `/api/repo-refs?${params.toString()}`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoRefsSchema,
  );
}

/**
 * Represents a repository-defaults request whose base-branch heuristic failed.
 *
 * The backend reports this domain failure inside its validated response. The API
 * boundary throws this error so query consumers receive either two complete
 * defaults or a failed query; the type also selects the failure-specific Toast
 * title without mislabeling transport or schema failures.
 */
class RepositoryDefaultsHeuristicError extends Error {
  constructor() {
    super("The backend could not infer a base branch for this repository.");
    this.name = "RepositoryDefaultsHeuristicError";
  }
}

/**
 * Requests the backend-selected branch-review defaults for one repository.
 *
 * The project must already exist and the caller supplies cancellation. The result
 * is validated as a complete entity and is not merged with local input state.
 */
async function requestRepoDefaults(
  projectId: ProjectId,
  abortSignal: AbortSignal,
): Promise<RepoDefaults> {
  const params = new URLSearchParams({ project_id: String(projectId) });
  const response = await requestJson(
    {
      input: `/api/repo-defaults?${params.toString()}`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoDefaultsResponseSchema,
  );
  const base = response.default_base_selection;
  if ("error" in base) {
    switch (base.error) {
      case "heuristic_fail":
        throw new RepositoryDefaultsHeuristicError();
    }
  }
  return {
    default_base_selection: base,
    preferred_review_selection: response.preferred_review_selection,
  };
}

/**
 * Removes exactly one saved repository mark by its backend project ID.
 *
 * Completion requires the backend's exact 204 No Content response. Cache
 * invalidation and any replacement repository selection are responsibilities
 * of the caller.
 */
async function requestRemoveRepo(projectId: ProjectId): Promise<void> {
  const response = await requestResponse({
    input: `/api/repos/${projectId}`,
    init: { method: "DELETE" },
    abortSignal: null,
    timeoutMs: REQUEST_TIMEOUT_MS,
  });
  if (!response.ok) {
    return throwResponseError(response);
  }
  if (response.status !== 204) {
    throw new Error(
      `Repository deletion requires 204 No Content; received ${response.status} ${response.statusText}.`,
    );
  }
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
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    RepoMainBranchSchema,
  );
}

/**
 * Requests the complete bounded preset catalog collection.
 *
 * The caller supplies query cancellation and receives every catalog the
 * backend found, in its order. This function performs no tab or subset
 * selection.
 */
function requestPresets(abortSignal: AbortSignal): Promise<PresetCatalogs> {
  return requestJson(
    {
      input: "/api/presets",
      init: {},
      abortSignal,
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
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    UserProfileSchema,
  );
}

/** Selects the one existing profile with an exact username. */
function requestLoginProfile(username: string): Promise<UserProfile> {
  return requestJson(
    {
      input: `/api/user-profile?username=${encodeURIComponent(username)}`,
      init: {},
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    UserProfileSchema,
  );
}

/**
 * Renames one existing profile selected by its exact backend ID.
 *
 * Callers provide the complete rename command. The validated updated profile
 * is returned, while the caller remains responsible for selected identity and
 * local-storage updates.
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
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    UserProfileSchema,
  );
}

/**
 * Requests backend preferences for one selected profile.
 *
 * A concrete profile ID and cancellation AbortSignal are required. This function
 * does not supply defaults when the backend response is absent or malformed.
 */
function requestPreferences(
  profileId: number,
  abortSignal: AbortSignal,
): Promise<Preferences> {
  return requestJson(
    {
      input: `/api/user-profile/${profileId}/preferences`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
}

/**
 * Saves the complete editable preference entity for one profile.
 *
 * Callers provide an explicit profile ID and aggressive-folds value. The
 * validated backend result is returned without mutating UI state.
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
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
}

/**
 * Resolves one Pull Request URL into its repository, URL identity, and commits.
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
      abortSignal: null,
      timeoutMs: PULL_REQUEST_TIMEOUT_MS,
    },
    PreparedPullRequestSchema,
  );
}

/**
 * Encodes complete DiffParams for the snapshot-producing manifest endpoint.
 *
 * Each Tab variant serializes its own exact fields into the HTTP parameters.
 * This function performs encoding only and never changes or defaults selection.
 */
function manifestSearchParams(params: DiffParams): URLSearchParams {
  const search = new URLSearchParams({
    tab: params.tab,
    project_id: String(params.project_id),
  });
  switch (params.tab) {
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
    case "pull-request":
      search.set("pull_request_url", params.pull_request_url);
      search.set("left_commit", params.left_commit);
      search.set("right_commit", params.right_commit);
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
 * Encodes the opaque `snapshot_id` shared by lazy and file endpoints.
 *
 * File paths and the selected rendering engine are deliberately appended only
 * by the file endpoint that uses them.
 */
function snapshotSearchParams(snapshotId: string): URLSearchParams {
  return new URLSearchParams({ snapshot_id: snapshotId });
}

/**
 * Requests one immutable ChangeSet manifest for complete DiffParams.
 *
 * TanStack Query supplies cancellation and receives a validated thin manifest. This
 * function does not start file requests or enrich manifest handles.
 */
function requestManifest(
  params: DiffParams,
  abortSignal: AbortSignal,
): Promise<Manifest> {
  const search = manifestSearchParams(params);
  return requestJson(
    {
      input: `/api/manifest?${search.toString()}`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    ManifestSchema,
  );
}

/**
 * Requests delayed-file metadata addressed by one manifest `snapshot_id`.
 *
 * The snapshot ID and query AbortSignal are required. The result describes lazy
 * presentation only and does not trigger explicit file loading.
 */
function requestLazyInfo(
  snapshotId: string,
  abortSignal: AbortSignal,
): Promise<LazyInfo> {
  const search = snapshotSearchParams(snapshotId);
  return requestJson(
    {
      input: `/api/lazy-info?${search.toString()}`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    LazyInfoSchema,
  );
}

/**
 * Requests one complete rendered file addressed by a manifest handle.
 *
 * Callers provide the rendering engine, opaque `snapshot_id`, exact manifest
 * entry, cancellation, and a bounded-or-unbounded timeout policy. The timeout
 * policy affects execution only and must not alter query identity.
 */
function requestFileDiff(
  engine: DiffEngine,
  snapshotId: string,
  entry: ManifestEntry,
  abortSignal: AbortSignal,
  timeout: FileDiffTimeout,
): Promise<FileDiff> {
  const search = snapshotSearchParams(snapshotId);
  search.set("engine", engine);
  if (entry.left_path !== null) {
    search.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null) {
    search.set("right_path", entry.right_path);
  }
  const timeoutMs =
    timeout === "unbounded"
      ? null
      : isHeavyEngine(engine)
        ? SLOW_DIFF_TIMEOUT_MS
        : REQUEST_TIMEOUT_MS;
  return requestJson(
    {
      input: `/api/file-diff?${search.toString()}`,
      init: {},
      abortSignal,
      timeoutMs,
    },
    ComposedDiffSchema,
  );
}

/** Reads every bounded transport page into one complete Snapshot Thread set. */
async function requestReviewThreads(
  snapshotId: ReviewId,
  abortSignal: AbortSignal,
): Promise<ReviewThread[]> {
  /** Read and validate one transport page within this complete Snapshot read. */
  async function page(
    pageNumber: number,
    throughActivityId: number | null,
  ): Promise<ReviewThreadPage> {
    const search = snapshotSearchParams(snapshotId);
    search.set("page", String(pageNumber));
    search.set("limit", "100");
    if (throughActivityId !== null) {
      search.set("through_activity_id", String(throughActivityId));
    }
    const response = await requestJson(
      {
        input: `/api/review/threads?${search.toString()}`,
        init: {},
        abortSignal,
        timeoutMs: REQUEST_TIMEOUT_MS,
      },
      ReviewThreadPageSchema,
    );
    if (response.snapshot_id !== snapshotId || response.page !== pageNumber) {
      throw new Error("Review response returned another page identity.");
    }
    return response;
  }

  const threads: ReviewThread[] = [];
  const threadIds = new Set<ReviewId>();
  let pageNumber = 1;
  let throughActivityId: number | null = null;
  while (true) {
    const response = await page(pageNumber, throughActivityId);
    if (throughActivityId === null) {
      throughActivityId = response.through_activity_id;
    } else if (response.through_activity_id !== throughActivityId) {
      throw new Error("Review transport pages use different activity pivots.");
    }
    for (const thread of response.threads) {
      if (threadIds.has(thread.thread_id)) {
        throw new Error("Review transport pages contain a duplicate Thread.");
      }
      threadIds.add(thread.thread_id);
      threads.push(thread);
    }
    if (!response.has_more) {
      if (threads.length !== response.total_threads) {
        throw new Error("Review transport pages do not contain every Thread.");
      }
      return threads;
    }
    pageNumber += 1;
  }
}

type CreateReviewThreadInput = {
  snapshotId: ReviewId;
  body: CreateReviewThreadRequest;
};

/** Requires one mutation response to match the exact addressed Thread pair. */
function assertReviewThreadIdentity(
  thread: { snapshot_id: ReviewId; thread_id: ReviewId },
  snapshotId: ReviewId,
  threadId: ReviewId,
): void {
  if (thread.snapshot_id !== snapshotId || thread.thread_id !== threadId) {
    throw new Error(
      "Review mutation response returned another Snapshot-bound Thread.",
    );
  }
}

/** Creates one Snapshot-bound Thread and its first Comment. */
async function requestCreateReviewThread(
  input: CreateReviewThreadInput,
): Promise<ReviewThread> {
  const thread = await requestJson(
    {
      input: "/api/review/post_comment",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          snapshot_id: input.snapshotId,
          ...CreateReviewThreadRequestSchema.parse(input.body),
        }),
      },
      abortSignal: null,
      // Irreversible review writes carry no transport timeout: the backend
      // commits regardless of a dropped connection, so the browser must wait
      // for the one authoritative response instead of inventing a failure.
      timeoutMs: null,
    },
    ReviewThreadSchema,
  );
  if (thread.snapshot_id !== input.snapshotId) {
    throw new Error("Review mutation response returned another Snapshot.");
  }
  return thread;
}

type AddReviewCommentInput = {
  snapshotId: ReviewId;
  threadId: ReviewId;
  body: AddReviewCommentRequest;
};

/** Appends one Comment through an exact Snapshot-bound Thread. */
async function requestAddReviewComment(
  input: AddReviewCommentInput,
): Promise<ReviewThreadUpdate> {
  const thread = await requestJson(
    {
      input: "/api/review/post_comment",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          snapshot_id: input.snapshotId,
          thread_id: input.threadId,
          ...AddReviewCommentRequestSchema.parse(input.body),
        }),
      },
      abortSignal: null,
      // Irreversible review writes carry no transport timeout: the backend
      // commits regardless of a dropped connection, so the browser must wait
      // for the one authoritative response instead of inventing a failure.
      timeoutMs: null,
    },
    ReviewThreadUpdateSchema,
  );
  assertReviewThreadIdentity(thread, input.snapshotId, input.threadId);
  return thread;
}

type EditReviewCommentInput = {
  snapshotId: ReviewId;
  threadId: ReviewId;
  commentId: ReviewId;
  body: EditReviewCommentRequest;
};

/** Edits one authored Comment through an exact Snapshot-bound Thread. */
async function requestEditReviewComment(
  input: EditReviewCommentInput,
): Promise<ReviewThreadUpdate> {
  const thread = await requestJson(
    {
      input: "/api/review/edit_comment",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          snapshot_id: input.snapshotId,
          comment_id: input.commentId,
          ...EditReviewCommentRequestSchema.parse(input.body),
        }),
      },
      abortSignal: null,
      // Irreversible review writes carry no transport timeout: the backend
      // commits regardless of a dropped connection, so the browser must wait
      // for the one authoritative response instead of inventing a failure.
      timeoutMs: null,
    },
    ReviewThreadUpdateSchema,
  );
  assertReviewThreadIdentity(thread, input.snapshotId, input.threadId);
  return thread;
}

type ReviewCommentActionInput = {
  snapshotId: ReviewId;
  threadId: ReviewId;
  commentId: ReviewId;
  body: ReviewProfileActionRequest;
};

/** Tombstones one current Comment through an exact Snapshot-bound Thread. */
async function requestDeleteReviewComment(
  input: ReviewCommentActionInput,
): Promise<ReviewThreadUpdate> {
  const thread = await requestJson(
    {
      input: "/api/review/delete_comment",
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          snapshot_id: input.snapshotId,
          comment_id: input.commentId,
          ...ReviewProfileActionRequestSchema.parse(input.body),
        }),
      },
      abortSignal: null,
      // Irreversible review writes carry no transport timeout: the backend
      // commits regardless of a dropped connection, so the browser must wait
      // for the one authoritative response instead of inventing a failure.
      timeoutMs: null,
    },
    ReviewThreadUpdateSchema,
  );
  assertReviewThreadIdentity(thread, input.snapshotId, input.threadId);
  return thread;
}

type ReviewThreadActionInput = {
  snapshotId: ReviewId;
  threadId: ReviewId;
  body: ReviewProfileActionRequest;
};

/** Applies one explicit lifecycle action to an exact Snapshot-bound Thread. */
async function requestChangeReviewThreadState(
  action: "resolve" | "reopen" | "delete",
  input: ReviewThreadActionInput,
): Promise<ReviewThreadUpdate> {
  const thread = await requestJson(
    {
      input: `/api/review/${action}_thread`,
      init: {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          snapshot_id: input.snapshotId,
          thread_id: input.threadId,
          ...ReviewProfileActionRequestSchema.parse(input.body),
        }),
      },
      abortSignal: null,
      // Irreversible review writes carry no transport timeout: the backend
      // commits regardless of a dropped connection, so the browser must wait
      // for the one authoritative response instead of inventing a failure.
      timeoutMs: null,
    },
    ReviewThreadUpdateSchema,
  );
  assertReviewThreadIdentity(thread, input.snapshotId, input.threadId);
  return thread;
}

const snapshotQuery = {
  staleTime: Infinity,
  gcTime: 0,
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
        queryFn: ({ signal: abortSignal }) =>
          requestManifest(params, abortSignal),
        meta: { errorTitle: "Failed to load ChangeSet manifest" },
        ...snapshotQuery,
      });
    },

    /**
     * Defines delayed-file metadata addressed by one manifest `snapshot_id`.
     *
     * Consumers supply the snapshot ID. Query observation, enabling, and
     * explicit loading remain responsibilities of the ChangeSet.
     */
    lazyInfo(snapshotId: string) {
      return queryOptions({
        queryKey: ["change-set", "lazy-info", snapshotId] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestLazyInfo(snapshotId, abortSignal),
        meta: { errorTitle: "Failed to load delayed-file information" },
        ...snapshotQuery,
      });
    },

    /**
     * Defines one canonical rendered-file query from a manifest handle.
     *
     * Engine, snapshot ID, and entry paths form stable query identity.
     * Timeout controls only this attempt's transport and is deliberately absent
     * from the query key. The definition does not decide sequential scheduling
     * or rich/lazy state.
     */
    file(
      engine: DiffEngine,
      snapshotId: string,
      entry: ManifestEntry,
      timeout: FileDiffTimeout,
    ) {
      const locator = {
        left_path: entry.left_path,
        right_path: entry.right_path,
      };
      return queryOptions({
        queryKey: ["change-set", "file", engine, snapshotId, locator] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestFileDiff(engine, snapshotId, entry, abortSignal, timeout),
        meta: { errorTitle: "Failed to load file diff" },
        ...snapshotQuery,
      });
    },
  },

  review: {
    /** Defines the complete canonical Thread set for an exact Snapshot. */
    snapshot(snapshotId: ReviewId) {
      return queryOptions({
        queryKey: ["review", snapshotId] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestReviewThreads(snapshotId, abortSignal),
        meta: { errorTitle: "Failed to load review Threads" },
        ...snapshotQuery,
      });
    },

    thread: {
      /** Defines Thread creation with its first Comment. */
      create() {
        return mutationOptions({
          mutationKey: ["review", "thread", "create"] as const,
          mutationFn: requestCreateReviewThread,
          meta: { errorTitle: "Failed to create review Thread" },
        });
      },
      /** Defines one explicit Thread lifecycle transition. */
      changeState(action: "resolve" | "reopen" | "delete") {
        return mutationOptions({
          mutationKey: ["review", "thread", action] as const,
          mutationFn: (input: ReviewThreadActionInput) =>
            requestChangeReviewThreadState(action, input),
          meta: { errorTitle: `Failed to ${action} review Thread` },
        });
      },
    },

    comment: {
      /** Defines appending one Comment. */
      add() {
        return mutationOptions({
          mutationKey: ["review", "comment", "add"] as const,
          mutationFn: requestAddReviewComment,
          meta: { errorTitle: "Failed to add review Comment" },
        });
      },
      /** Defines editing one authored Comment. */
      edit() {
        return mutationOptions({
          mutationKey: ["review", "comment", "edit"] as const,
          mutationFn: requestEditReviewComment,
          meta: { errorTitle: "Failed to edit review Comment" },
        });
      },
      /** Defines tombstoning one authored Comment. */
      delete() {
        return mutationOptions({
          mutationKey: ["review", "comment", "delete"] as const,
          mutationFn: requestDeleteReviewComment,
          meta: { errorTitle: "Failed to delete review Comment" },
        });
      },
    },
  },

  repos: {
    /**
     * Defines the shared repository-list query with short measured freshness.
     *
     * Any mounted repository selector may observe this definition. TanStack owns
     * deduplication and freshness; consumers store selection and implement interaction.
     */
    list() {
      return queryOptions({
        queryKey: ["repos"] as const,
        queryFn: ({ signal: abortSignal }) => requestRepos(abortSignal),
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
        queryFn: ({ signal: abortSignal }) =>
          requestRepoRefs(projectId, abortSignal),
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
        queryFn: ({ signal: abortSignal }) =>
          requestRepoDefaults(projectId, abortSignal),
        staleTime: Infinity,
        meta: {
          errorTitle: (error) =>
            error instanceof RepositoryDefaultsHeuristicError
              ? "Heuristic for repository defaults failed"
              : "Failed to load repository defaults",
        },
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
     * Consumers pass the complete project-and-selection entity to `mutate`.
     * The validated result is authoritative; Branch Review invalidates the
     * canonical defaults query after success.
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
        queryFn: ({ signal: abortSignal }) => requestPresets(abortSignal),
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
        queryFn: ({ signal: abortSignal }) =>
          requestPreferences(profileId, abortSignal),
        meta: { errorTitle: "Failed to load preferences" },
      });
    },

    /**
     * Defines explicit selection of one existing profile by exact username.
     *
     * Login is a local identity choice rather than authentication. It never
     * creates a Profile when the supplied name does not exist.
     */
    login() {
      return mutationOptions({
        mutationKey: ["profile", "login"] as const,
        mutationFn: requestLoginProfile,
        meta: { errorTitle: "Failed to log in" },
      });
    },

    /**
     * Defines the mutation that registers a profile from a username.
     *
     * Consumers pass the required username. After the backend returns the validated
     * profile, consumers update selected identity and persistence explicitly.
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
     * Consumers pass the complete profile ID and username command and replace the
     * selected profile with the returned value without inventing optimistic state.
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
