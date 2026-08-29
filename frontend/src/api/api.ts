/**
 * Defines the typed HTTP boundary between the HUD and Python backend.
 *
 * Callers use `api` definitions with TanStack Query and consume only values that
 * the matching runtime schema validated. This module constructs HTTP entities,
 * combines cancellation with transport deadlines, and verifies repeated review
 * and pagination identities before publishing those responses.
 *
 * It defines backend data and transport policy, not query-observer lifetimes or
 * presentation. File sequencing belongs to the ChangeSet file lane, and failures
 * remain errors for the nearest query or UI boundary to present.
 */
import { mutationOptions, queryOptions } from "@tanstack/solid-query";
import { z } from "zod";
import { assert } from "../utils";

/**
 * Defines the closed set of engine names accepted by frontend API definitions.
 *
 * `DiffEngine` is inferred from this validator so query keys, URL state, and
 * file parameters cannot acquire an engine name the backend does not expose.
 */
const DiffEngineSchema = z.enum([
  /** Dirdiff's syntax-aware text renderer. */
  "dirdiff",
  /** Git's line-oriented diff renderer. */
  "git",
  /** Difftastic's syntax-aware external renderer. */
  "difftastic",
  /** GumTree's syntax-tree renderer. */
  "gumtree",
  /** Dirdiff's document-wide token renderer. */
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
 * concurrent renders are named light. Prefetch made dirdiff 23% faster and git
 * 14% faster over the total load. Git also spawns per file, so measurement,
 * not the process boundary, decides heaviness. A new engine remains heavy
 * until measured otherwise.
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

/**
 * Validates one repository record returned by the marked-repositories endpoint.
 *
 * Strict object validation rejects backend shape drift before repository data
 * reaches selection controls. The inferred `RepoMark` is the validated result.
 */
const RepoMarkSchema = z.strictObject({
  /** Positive backend identity used for selection, removal, and repo-backed query keys. */
  id: z.number().int().positive(),
  /**
   * Filesystem path registered with the backend and displayed by the HUD.
   *
   * Frontend code treats this as an opaque label and must not use it to access
   * the local filesystem; repository operations identify the mark by `id`.
   */
  path: z.string(),
  /** Backend-derived repository caption shown by selection controls. */
  name: z.string(),
  /** Backend timestamp describing when this path was marked, not its VCS state. */
  marked_at: z.string(),
});

/**
 * Describes one repository available to repository-backed Tabs.
 *
 * UI state stores only the record's identity and derives display data from the
 * current repositories query.
 */
export type RepoMark = z.infer<typeof RepoMarkSchema>;

/**
 * Validates the identity returned by profile login, registration, and rename.
 *
 * It accepts only a positive database identity and non-empty name, keeping
 * preferences and other profile state outside the identity record.
 */
const UserProfileSchema = z.strictObject({
  /** Positive database identity used for authorship and preference addressing. */
  id: z.number().int().positive(),
  /** Non-empty backend-confirmed name shown as the review author. */
  username: z.string().min(1),
});

/**
 * Describes the selected user identity returned by profile mutations.
 *
 * The complete value may be persisted explicitly in localStorage. It contains
 * identity only and must not absorb backend preferences.
 */
export type UserProfile = z.infer<typeof UserProfileSchema>;

/**
 * Validates the complete preferences record returned for one Profile.
 *
 * The positive Profile identity keeps the record tied to its query key, while
 * strict validation rejects undeclared preference fields until the interface is
 * intentionally extended.
 */
const PreferencesSchema = z.strictObject({
  /** Profile identity repeated by the response for the addressed preferences entity. */
  user_profile_id: z.number().int().positive(),
  /** Whether every valid backend fold hint should begin folded. */
  aggressive_folds: z.boolean(),
});

/**
 * Describes backend preferences for one user profile.
 *
 * Callers may cache this complete value under its profile query key. The value
 * must not be copied into workspace state or persisted separately by the UI.
 */
export type Preferences = z.infer<typeof PreferencesSchema>;

/**
 * Validates the local arm of a structured branch selection.
 *
 * The literal discriminant keeps a remote field impossible in this arm; callers
 * must provide the selected branch text explicitly.
 */
const LocalBranchSelectionSchema = z.strictObject({
  /** Discriminant forbidding a remote name in this selection arm. */
  source: z.literal("local"),
  /** Local branch text; the schema also admits empty text used by unfinished controls. */
  branch: z.string(),
});

/**
 * Validates the remote arm of a structured branch selection.
 *
 * Both remote and branch remain required because neither may be inferred from
 * repository defaults after the user has made a selection.
 */
const RemoteBranchSelectionSchema = z.strictObject({
  /** Discriminant requiring both remote and branch fields in this arm. */
  source: z.literal("remote"),
  /** Configured remote name selected by the caller; defaults must be applied before use. */
  remote: z.string(),
  /** Branch text relative to `remote`; the schema admits empty unfinished input. */
  branch: z.string(),
});

/**
 * Validates either complete branch-selection arm by its source discriminant.
 *
 * The union is shared by repository metadata responses and Branch Review
 * parameters, so the same shape crosses both directions of the API boundary.
 */
const BranchSelectionSchema = z.discriminatedUnion("source", [
  LocalBranchSelectionSchema,
  RemoteBranchSelectionSchema,
]);

/**
 * Identifies one structured local or remote branch selection.
 *
 * The discriminated shape keeps local and remote selections distinct. Free-form
 * refs use strings instead of this structured contract.
 */
export type BranchSelection = z.infer<typeof BranchSelectionSchema>;

/**
 * Validates one remote-branch suggestion in both structured and Git-ref forms.
 *
 * Refs controls use the Git spelling while Branch Review uses the structured
 * pair. Keeping both backend-authored forms prevents the UI from parsing refs.
 */
const RemoteBranchRefSchema = z.strictObject({
  /** Remote and branch pair consumed by Branch Review without parsing Git syntax. */
  structured: z.strictObject({
    /** Configured remote name associated with this backend suggestion. */
    remote: z.string(),
    /** Branch component associated with `remote`. */
    branch: z.string(),
  }),
  /** Complete Git ref spelling consumed by the free-form Refs Tab. */
  gitref: z.string(),
});

/**
 * Validates the built-in Git sides for which the HUD has fixed descriptions.
 *
 * Arbitrary refs remain strings in other categories; extending this enum also
 * requires the consuming controls to provide matching presentation.
 */
const BuiltinRefSchema = z.enum([
  /** Commit currently checked out by the repository. */
  "HEAD",
  /** Git staging area between the checked-out commit and worktree. */
  "index",
  /** Current files in the repository working directory. */
  "worktree",
]);

/**
 * Identifies one built-in Git ref supported by repository autocomplete.
 *
 * The backend may return only these established values. Consumers may rely on
 * every value having its corresponding application description; arbitrary Git
 * refs and repository branch names must use the other RefChoices categories.
 */
export type BuiltinRef = z.infer<typeof BuiltinRefSchema>;

/**
 * Validates the complete categorized autocomplete data for one repository.
 *
 * Arrays retain backend order, and remote branches preserve their paired forms.
 * The validator does not select or filter a ref for any Tab.
 */
const RefChoicesSchema = z.strictObject({
  /** Backend-supported fixed refs presented with exhaustive HUD descriptions. */
  builtins: z.array(BuiltinRefSchema),
  /** Local branch names in backend order. */
  local_branches: z.array(z.string()),
  /** Configured remote names in backend order. */
  remotes: z.array(z.string()),
  /** Remote branch suggestions carrying both Git and structured spellings. */
  remote_branches: z.array(RemoteBranchRefSchema),
});

/**
 * Describes all ref autocomplete categories for one repository snapshot.
 *
 * Consumers use this backend-authored categorization for autocomplete without
 * copying it into selection state or parsing remote ref spellings.
 */
export type RefChoices = z.infer<typeof RefChoicesSchema>;

/**
 * Validates the repository-ref endpoint envelope.
 *
 * The explicit envelope leaves room for endpoint-level metadata without making
 * `RefChoices` itself responsible for repository response structure.
 */
const RepoRefsSchema = z.strictObject({
  /** Complete categorized choices for the addressed repository. */
  ref_choices: RefChoicesSchema,
});

/**
 * Contains the canonical ref choices returned for one repository.
 *
 * Consumers share this single backend entity and filter it locally. It must not
 * contain live autocomplete input or a selected ref.
 */
export type RepoRefs = z.infer<typeof RepoRefsSchema>;

/**
 * Validates both successful defaults and the backend's explicit heuristic failure.
 *
 * This is the wire response only. The query function rejects the failure arm so
 * consumers receive `RepoDefaults` rather than a partially useful union.
 */
const RepoDefaultsResponseSchema = z.strictObject({
  /** Backend base choice or its explicit heuristic-failure sentinel. */
  default_base_selection: z.union([
    BranchSelectionSchema,
    z.strictObject({
      /** Discriminant preventing a heuristic failure from resembling a selection. */
      kind: z.literal("error"),
      /** Stable failure code converted into `RepositoryDefaultsHeuristicError`. */
      error: z.literal("heuristic_fail"),
    }),
  ]),
  /** Complete review-side choice available even when base inference failed. */
  preferred_review_selection: BranchSelectionSchema,
});

/**
 * Validates the successful branch-review defaults exposed to HUD consumers.
 *
 * Both selections are required and complete. Heuristic failure is handled
 * before this schema's inferred type reaches a component.
 */
const RepoDefaultsSchema = z.strictObject({
  /** Complete base-side selection accepted after heuristic validation. */
  default_base_selection: BranchSelectionSchema,
  /** Complete review-side selection paired with the same repository response. */
  preferred_review_selection: BranchSelectionSchema,
});

/**
 * Contains the two complete branch-review defaults for one repository.
 *
 * The query rejects a backend heuristic failure instead of representing it as
 * defaults data. Successful values seed untouched controls but must not overwrite
 * user-edited input.
 */
export type RepoDefaults = z.infer<typeof RepoDefaultsSchema>;

/**
 * Validates the value returned after saving a repository's main branch.
 *
 * The response carries the backend's positive repository identity and accepted
 * structured selection. The current caller uses success to refresh defaults.
 */
const RepoMainBranchSchema = z.strictObject({
  /** Repository identity echoed by the successful save response. */
  project_id: z.number().int().positive(),
  /** Complete backend-accepted main-branch selection. */
  selection: BranchSelectionSchema,
});

/**
 * Reports the repository main-branch selection stored by the backend.
 *
 * The value is not a local optimistic placeholder. Current callers use mutation
 * success to invalidate defaults rather than deriving cache identity from it.
 */
export type RepoMainBranch = z.infer<typeof RepoMainBranchSchema>;

/**
 * Validates the authoritative result of preparing one Pull Request URL.
 *
 * Every string must be present because the resulting repository identity, URL,
 * and two commits jointly form the selected Pull Request Tab value.
 */
const PreparedPullRequestSchema = z.strictObject({
  /** Repository prepared or registered for the resulting Pull Request Tab. */
  project_id: z.number().int().positive(),
  /** Backend-authoritative URL used as the Pull Request correspondence identity. */
  pull_request_url: z.string().min(1),
  /** Prepared base commit passed to manifest capture as the left side. */
  left_commit: z.string().min(1),
  /** Prepared review commit passed to manifest capture as the right side. */
  right_commit: z.string().min(1),
});

/**
 * Contains the authoritative repository, Room identity, and commits prepared
 * from one Pull Request URL.
 *
 * Callers preserve the complete backend result when reconstructing a Pull Request
 * Tab and must not reinterpret it as a Branch Review selection.
 */
export type PreparedPullRequest = z.infer<typeof PreparedPullRequestSchema>;

/**
 * Validates one selectable group inside a preset catalog.
 *
 * Both backend identity and display name must be non-empty. Manifest content is
 * loaded only after the group identity enters `PresetDiffParams`.
 */
const PresetGroupSchema = z.strictObject({
  /** Non-empty backend identity sent as `preset_subset` after selection. */
  id: z.string().min(1),
  /** User-visible group caption that never enters manifest parameters. */
  display_name: z.string().min(1),
});

/**
 * Describes one selectable preset within a catalog.
 *
 * The group contains selection identity and presentation only, never manifest or
 * rendered-file content.
 */
export type PresetGroup = z.infer<typeof PresetGroupSchema>;

/**
 * Validates one preset catalog and its ordered selectable groups.
 *
 * The default identity and group list remain separate backend fields. This schema
 * validates each shape but does not cross-check that the identity names a group.
 */
const PresetCatalogSchema = z.strictObject({
  /** Catalog identity sent as the preset `project_id`. */
  id: z.string().min(1),
  /** User-visible catalog caption shown by the kind selector. */
  name: z.string().min(1),
  /** Backend-declared group identity used when controls have no restored subset. */
  default_preset: z.string().min(1),
  /** Selectable groups in backend display order. */
  groups: z.array(PresetGroupSchema),
});

/**
 * Describes one preset catalog: how to select it, its caption, its groups.
 *
 * The catalog is directory metadata only. Controls use its declared default when
 * no subset is restored and send the chosen group identity in DiffParams.
 */
export type PresetCatalog = z.infer<typeof PresetCatalogSchema>;

/**
 * Validates the ordered catalog listing returned by the preset endpoint.
 *
 * The array admits any number of backend-provided catalogs and preserves their
 * order for the picker; the frontend declares no substitute catalog.
 */
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
  /**
   * Exact positive repository identity used by every repository-backed Tab.
   *
   * Callers obtain it from validated repository data or restored canonical URL
   * state. Preset projects use their separate string identity type.
   */
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
  /**
   * Discriminant selecting the fixed current-worktree workflow.
   *
   * It is sent unchanged to manifest and must not be used for another ref pair.
   */
  tab: "head";
  /**
   * Fixed captured left side for Head review.
   *
   * The HUD does not expose a control that may replace this value.
   */
  left: "HEAD";
  /**
   * Fixed live right side for Head review.
   *
   * It includes indexed and unstaged worktree content according to backend rules.
   */
  right: "worktree";
  /**
   * Required inclusion of untracked files in Head review.
   *
   * This literal distinguishes the workflow from ref comparisons, where the
   * option does not exist.
   */
  show_untracked: true;
};

/**
 * Requests a selected diff between two Git refs in one repository.
 *
 * Both refs are complete selected values returned by Refs Controls, not live
 * input or autocomplete suggestions.
 */
export type RefsDiffParams = RepoBackedDiffParams & {
  /**
   * Discriminant selecting free-form ref comparison.
   *
   * It tells the backend to interpret the two sides as exact Git ref strings.
   */
  tab: "refs";
  /**
   * User-accepted Git ref for the old side.
   *
   * It may come from autocomplete or free-form input and is never reconstructed
   * from the right side or repository defaults.
   */
  left: string;
  /**
   * User-accepted Git ref for the new side.
   *
   * It may come from autocomplete or free-form input and remains independent of
   * the old-side choice.
   */
  right: string;
};

/**
 * Requests a structured base/review branch ChangeSet in one repository.
 *
 * Both selections are complete local or remote values. The transport must not
 * reconstruct missing remotes or substitute repository defaults.
 */
export type BranchReviewDiffParams = RepoBackedDiffParams & {
  /**
   * Discriminant selecting structured Branch Review semantics.
   *
   * Free-form ref comparison must use the Refs variant instead.
   */
  tab: "branch-review";
  /**
   * Complete local or remote branch selected as the merge base side.
   *
   * The value is accepted control state, not a live default that may later
   * replace user input.
   */
  base_selection: BranchSelection;
  /**
   * Complete local or remote branch selected as the review side.
   *
   * Its source arm remains explicit so the backend applies branch-review rules
   * without parsing display text.
   */
  review_selection: BranchSelection;
};

/**
 * Requests the repository state prepared for one Pull Request.
 *
 * The URL is the complete Room correspondence key. The two commits are capture
 * inputs and must never be represented as Branch Review selections.
 */
export type PullRequestDiffParams = RepoBackedDiffParams & {
  /**
   * Discriminant selecting a prepared Pull Request correspondence.
   *
   * The value must come from successful Pull Request preparation.
   */
  tab: "pull-request";
  /**
   * Exact URL whose forge identity establishes the Room correspondence.
   *
   * Callers preserve the prepared value rather than normalizing it again.
   */
  pull_request_url: string;
  /**
   * Prepared base commit passed to snapshot capture.
   *
   * It is not a Branch Review selection and must remain paired with the prepared
   * URL and right commit.
   */
  left_commit: string;
  /**
   * Prepared review commit passed to snapshot capture.
   *
   * It is authoritative preparation output, not a ref control value.
   */
  right_commit: string;
};

/**
 * Requests one selected preset fixture.
 *
 * The preset kind is the backend project ID and `preset_subset` is the selected
 * catalog group. Repository selection is deliberately absent.
 */
export type PresetDiffParams = {
  /**
   * Catalog identity used as the preset backend project.
   *
   * It must match a validated catalog ID and never a numeric repository identity.
   */
  project_id: PresetType;
  /**
   * Discriminant selecting preset capture.
   *
   * This variant carries no repository or Git-ref inputs.
   */
  tab: "preset";
  /**
   * Exact group identity selected inside the catalog.
   *
   * The backend uses it to locate fixture sides; the display name is not sent.
   */
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

/**
 * Validates backend aggregate statistics for one immutable manifest.
 *
 * The refinement enforces paired line-count availability so the HUD never sees
 * one side of an aggregate whose other side is unknown.
 */
const ManifestSummarySchema = z
  .strictObject({
    /** Total manifest File leaves, equal to added, removed, and updated totals. */
    changed_files: z.number().int(),
    /** File pairs present only on the right, including applicable untracked Files. */
    added_files: z.number().int(),
    /** File pairs present only on the left. */
    removed_files: z.number().int(),
    /** File relationships present on both sides, including renames and copies. */
    updated_files: z.number().int(),
    /** Backend-wide added-line count, or null together with `removed_lines`. */
    added_lines: z.number().int().nullable(),
    /** Backend-wide removed-line count, or null together with `added_lines`. */
    removed_lines: z.number().int().nullable(),
    /** Backend-reported entries omitted from the manifest tree, not delayed Files. */
    skipped_files: z.number().int(),
    /** Notebook cells with any change, or null when no cell summary exists. */
    changed_cells: z.number().int().nullable(),
    /** Notebook cells present only on the right, or null when unavailable. */
    added_cells: z.number().int().nullable(),
    /** Notebook cells present only on the left, or null when unavailable. */
    removed_cells: z.number().int().nullable(),
    /** Paired notebook cells whose content changed, or null when unavailable. */
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
 * The summary comes from manifest capture and must not be progressively
 * reconstructed from individual File responses.
 */
export type ManifestSummary = z.infer<typeof ManifestSummarySchema>;

/**
 * Validates the tracked-file arm and its exact Git change classification.
 *
 * The literal discriminant keeps these statuses unavailable to untracked files.
 */
const GitFileKindSchema = z.strictObject({
  /** Discriminant selecting tracked Git classification. */
  type: z.literal("git"),
  /** Exact Git relationship; side presence remains in the containing File pair. */
  status: z.enum(["modified", "added", "deleted", "renamed", "copied"]),
});

/**
 * Validates the untracked-file arm without inventing a Git status.
 *
 * Strict validation rejects status-like fields that blur the two variants.
 */
const UntrackedFileKindSchema = z.strictObject({
  /** Discriminant for worktree content absent from Git's tracked set. */
  type: z.literal("untracked"),
});

/**
 * Validates backend file classification by its tracked/untracked discriminant.
 *
 * Manifest, lazy-info, and file responses share this union so a File's kind has
 * one meaning throughout progressive loading.
 */
const FileKindSchema = z.discriminatedUnion("type", [
  GitFileKindSchema,
  UntrackedFileKindSchema,
]);

/**
 * Identifies how Git classifies one manifest or rendered-file entry.
 *
 * The discriminant keeps Git status unavailable for untracked content. This is
 * backend provenance, not File presentation state.
 */
export type FileKind = z.infer<typeof FileKindSchema>;

/**
 * Validates the complete backend vocabulary for intentionally delayed files.
 *
 * Query failures stay outside this enum. A new reason requires an explicit HUD
 * interpretation instead of arriving as arbitrary prose.
 */
const LazyReasonSchema = z.enum([
  /** Content size makes automatic rendering too expensive. */
  "too_big",
  /** Generated content is usually better reviewed through its source. */
  "generated",
  /** The deletion is shown before loading the removed content. */
  "deleted",
  /** Untracked content is shown before loading its full diff. */
  "untracked",
  /** A rename without content changes is shown before loading its content. */
  "pure_renamed",
]);

/**
 * Explains why the backend intentionally delays one manifest file.
 *
 * A null lazy field means ordinary sequential loading. Errors are query state
 * and must not be encoded as another LazyReason.
 */
export type LazyReason = z.infer<typeof LazyReasonSchema>;

/**
 * Validates one present side of a captured File identity.
 *
 * Nullability belongs to the enclosing pair. A present path is never empty or
 * repaired from the opposite side.
 */
const FilePathSchema = z.string().min(1);

/**
 * Rejects a backend file identity that has neither a left nor right path.
 *
 * Response schemas that include a file identity call this after validating
 * each present path as non-empty. The callback adds one schema issue for a
 * completely absent identity and must not infer one side from the other or
 * alter the decoded response.
 *
 * @param paths Validated nullable sides whose joint presence is checked.
 * @param context Zod refinement context that receives the identity diagnostic.
 */
function validateFilePaths(
  paths: {
    /**
     * Captured old-side path, or `null` when the File exists only on the right.
     *
     * The refinement treats it jointly with `right_path` and never synthesizes
     * a missing side.
     */
    left_path: string | null;
    /**
     * Captured new-side path, or `null` when the File exists only on the left.
     *
     * At least this path or `left_path` must be present for a valid identity.
     */
    right_path: string | null;
  },
  context: z.RefinementCtx,
): void {
  if (paths.left_path === null && paths.right_path === null) {
    context.addIssue({
      code: "custom",
      message: "File identity requires a left or right path.",
    });
  }
}

/**
 * Validates one thin manifest File handle and its paired path invariant.
 *
 * Rendered content and local expansion state cannot enter this backend entity.
 */
const ManifestEntrySchema = z
  .strictObject({
    /** Tracked or untracked provenance shared with later File responses. */
    file_kind: FileKindSchema,
    /** Non-empty old-side path, or null when the File exists only on the right. */
    left_path: FilePathSchema.nullable(),
    /** Non-empty new-side path, or null when the File exists only on the left. */
    right_path: FilePathSchema.nullable(),
    /** Backend deferral reason, or null for ordinary automatic loading. */
    lazy: LazyReasonSchema.nullable(),
  })
  .superRefine(validateFilePaths);

/**
 * Provides the complete thin handle for one manifest file.
 *
 * At least one path is present and every present path is non-empty. The thin
 * handle is sufficient for later File endpoints and contains no rendered rows or
 * local presentation state.
 */
export type ManifestEntry = z.infer<typeof ManifestEntrySchema>;

/**
 * Represents one file leaf in the recursive manifest tree.
 *
 * `name` is tree presentation data and `entry` is the exact backend handle used
 * for later requests. A file node cannot contain child nodes.
 */
export type ManifestFile = {
  /**
   * Discriminant selecting a file leaf during recursive tree traversal.
   *
   * FileTree and manifest flattening rely on this arm having no child array.
   */
  type: "file";
  /**
   * Backend-authored label for this leaf inside its containing directory.
   *
   * It is presentation data and never replaces the path pair in `entry` as File
   * identity.
   */
  name: string;
  /**
   * Exact thin backend handle used to load this File later.
   *
   * Callers pass it through to the file lane and never attach response or
   * expansion state to it.
   */
  entry: ManifestEntry;
};

/**
 * Represents one directory in the recursive manifest tree.
 *
 * `path` is the stable expansion key and `entries` preserve manifest order. A
 * directory carries no file-load handle.
 */
export type ManifestDirectory = {
  /**
   * Discriminant selecting an interior directory node.
   *
   * This arm contains child nodes and never a file-load handle.
   */
  type: "directory";
  /**
   * Backend-authored segment displayed for this directory row.
   *
   * It is not the stable expansion identity when identical names occur at
   * different depths.
   */
  name: string;
  /**
   * Stable manifest path used as the directory expansion key.
   *
   * It identifies this exact node across derivations from the same immutable
   * snapshot.
   */
  path: string;
  /**
   * Ordered child files and directories contained directly by this node.
   *
   * Traversal preserves backend order when producing FileTree rows and the flat
   * File lane.
   */
  entries: ManifestNode[];
};

/**
 * Represents one ordered node in a manifest tree.
 *
 * Consumers discriminate by `type` and must preserve recursive entry order when
 * deriving FileCards or FileTree rows.
 */
export type ManifestNode = ManifestFile | ManifestDirectory;

/**
 * Recursively validates ordered manifest file and directory nodes.
 *
 * `z.lazy` exists for the directory recursion. The explicit type annotation
 * keeps the runtime validator aligned with the public union.
 */
const ManifestNodeSchema: z.ZodType<ManifestNode> = z.lazy(() =>
  z.discriminatedUnion("type", [
    z.strictObject({
      /** Discriminant selecting a manifest leaf with no child collection. */
      type: z.literal("file"),
      /** Backend-authored leaf caption used only for tree presentation. */
      name: z.string(),
      /** Exact thin File handle passed to lazy-info and File operations. */
      entry: ManifestEntrySchema,
    }),
    z.strictObject({
      /** Discriminant selecting an interior manifest node. */
      type: z.literal("directory"),
      /** Backend-authored segment displayed for this directory row. */
      name: z.string(),
      /** Stable manifest path used as the directory expansion key. */
      path: z.string(),
      /** Direct child nodes in backend order. */
      entries: z.array(ManifestNodeSchema),
    }),
  ]),
);

/**
 * Validates the complete manifest response that establishes one Snapshot.
 *
 * The opaque identity addresses every later file and review read; the ordered
 * tree remains thin and immutable for that lifetime.
 */
const ManifestSchema = z.strictObject({
  /** Opaque Snapshot identity addressing every later File and review operation. */
  snapshot_id: z.string().regex(/^[0-9a-f]{32}$/),
  /** Backend-authored ChangeSet title used for active snapshot presentation. */
  display_name: z.string(),
  /** User-visible caption for the captured old side. */
  left_label: z.string(),
  /** User-visible caption for the captured new side. */
  right_label: z.string(),
  /** Immutable manifest-wide counts available before Files render. */
  summary: ManifestSummarySchema,
  /** Thin recursive File hierarchy in backend order. */
  tree: z.array(ManifestNodeSchema),
});

/**
 * Describes one ordered immutable ChangeSet snapshot returned by the manifest
 * endpoint.
 *
 * The Snapshot identity isolates all subsequent reads. The tree remains thin and
 * must not be mutated with progressive File results.
 */
export type Manifest = z.infer<typeof ManifestSchema>;

/**
 * Validates lightweight presentation data for one intentionally delayed File.
 *
 * It repeats the path-pair invariant because lazy-info is an independent
 * backend response and callers must not repair it from manifest data.
 */
const LazyInfoFileSchema = z
  .strictObject({
    /** Tracked or untracked provenance repeated independently of the manifest. */
    file_kind: FileKindSchema,
    /** Non-empty old-side path, or null when absent from capture. */
    left_path: FilePathSchema.nullable(),
    /** Non-empty new-side path, or null when absent from capture. */
    right_path: FilePathSchema.nullable(),
    /** Complete File caption shown before rendered content exists. */
    display_name: z.string(),
    /** Changed-line total when cheaply available, otherwise null. */
    changed_lines: z.number().int().nullable(),
    /** Added-line total when cheaply available, otherwise null. */
    added_lines: z.number().int().nullable(),
    /** Removed-line total when cheaply available, otherwise null. */
    removed_lines: z.number().int().nullable(),
    /** Backend deferral reason; null is allowed by transport but not invented by callers. */
    lazy: LazyReasonSchema.nullable(),
  })
  .superRefine(validateFilePaths);

/**
 * Contains the complete lightweight presentation data for one delayed file.
 *
 * At least one path is present and each present path is non-empty. Callers must
 * preserve unavailable counts and never fill missing values from another response.
 */
export type LazyInfoFile = z.infer<typeof LazyInfoFileSchema>;

/**
 * Validates the lazy-info endpoint envelope and every delayed File record.
 *
 * The response may contain any number of files, but each identity and nullable
 * count is checked before the lane consumes it.
 */
const LazyInfoSchema = z.strictObject({
  /** Complete delayed-File records returned for the addressed Snapshot. */
  files: z.array(LazyInfoFileSchema),
});

/**
 * Contains lightweight metadata for the intentionally delayed manifest files.
 *
 * The response is backend query data and must not become a second mutable file
 * store inside ChangeSet.
 */
export type LazyInfo = z.infer<typeof LazyInfoSchema>;

/**
 * Validates complete header statistics for one rendered text File.
 *
 * These are concrete engine results for this response, not nullable manifest
 * aggregates or progressively accumulated client counters.
 */
const TextFileSummarySchema = z.strictObject({
  /** Non-equal aligned rows across all rendered text bays in this File. */
  changed_lines: z.number().int(),
  /** Changed rows with both source sides present. */
  modified_lines: z.number().int(),
  /** Rendered rows containing only a right source line. */
  added_lines: z.number().int(),
  /** Rendered rows containing only a left source line. */
  removed_lines: z.number().int(),
  /** Rows classified as moved by the selected engine, excluding token movement. */
  moved_lines: z.number().int(),
  /** Whether capture retained a left side, including a present empty File. */
  left_exists: z.boolean(),
  /** Whether capture retained a right side, including a present empty File. */
  right_exists: z.boolean(),
});

/**
 * Contains complete statistics for one ordinary rendered text file.
 *
 * These values belong to the file response and its header. They must not be
 * substituted with manifest aggregates or partially loaded counters.
 */
export type TextFileSummary = z.infer<typeof TextFileSummarySchema>;

/**
 * Validates the renderer's closed classification for one aligned row.
 *
 * The HUD maps these values directly and never derives status from line-number
 * presence or text equality.
 */
const RowStatusSchema = z.enum([
  /** Both aligned source lines have equal content. */
  "equal",
  /** Both source lines are present and their content differs. */
  "replace",
  /** Only the new-side source line is present. */
  "insert",
  /** Only the old-side source line is present. */
  "delete",
  /** The renderer matched content that changed position. */
  "move",
]);

/**
 * Classifies one aligned diff row according to the backend renderer.
 *
 * The value drives presentation only and must not be recomputed from text or
 * line-number presence in the browser.
 */
export type RowStatus = z.infer<typeof RowStatusSchema>;

/**
 * Validates one lossless display slice produced by rendering enrichment.
 *
 * Text, syntax, diff, and whitespace facts arrive together so the browser need
 * not intersect separate token ranges.
 */
const DecoratedPartSchema = z.strictObject({
  /** Exact lossless source slice rendered in sequence with its siblings. */
  text: z.string(),
  /** Backend syntax classes applied to this complete slice. */
  syntax_classes: z.array(z.string()),
  /** Token-level diff classification, distinct from the containing row status. */
  diff_status: z.enum(["unchanged", "replace", "insert", "delete", "move"]),
  /** Whether every character in `text` is whitespace. */
  is_whitespace: z.boolean(),
  /** Whether this whitespace slice precedes the side's first non-whitespace text. */
  is_leading_whitespace: z.boolean(),
});

/**
 * Describes one backend-produced text slice with complete display decoration.
 *
 * Ordered parts reconstruct one row side exactly. Consumers render the supplied
 * decoration rather than intersecting another token or offset representation.
 */
export type DecoratedPart = z.infer<typeof DecoratedPartSchema>;

/**
 * Validates one complete aligned text row and its optional hunk stop.
 *
 * Nullable side values represent genuine one-sided rows. The browser consumes
 * this alignment without constructing substitute counterparts.
 */
const DiffRowSchema = z.strictObject({
  /** Backend alignment classification for the complete row. */
  status: RowStatusSchema,
  /** Positive old-side line number, or null when that side is absent. */
  left_no: z.number().int().positive().nullable(),
  /** Positive new-side line number, or null when that side is absent. */
  right_no: z.number().int().positive().nullable(),
  /** Exact old-side text, or null together with an absent old-side line. */
  left_text: z.string().nullable(),
  /** Exact new-side text, or null together with an absent new-side line. */
  right_text: z.string().nullable(),
  /** Ordered lossless decoration for the present old side; absent sides use no parts. */
  left_parts: z.array(DecoratedPartSchema),
  /** Ordered lossless decoration for the present new side; absent sides use no parts. */
  right_parts: z.array(DecoratedPartSchema),
  /** Bay-local hunk index on its first row, otherwise null. */
  hunk_index: z.number().int().nonnegative().nullable(),
});

/**
 * Contains one complete backend-aligned row for text or notebook source.
 *
 * Nullable side values represent genuinely absent lines. Alignment and hunk
 * identity come from the renderer and must not be reconstructed in the browser.
 */
export type DiffRow = z.infer<typeof DiffRowSchema>;

/**
 * Validates one backend hint for a foldable unchanged source span.
 *
 * Hints preserve source coordinates and policy only; current folded state stays
 * with the mounted text grid.
 */
const FoldHintSchema = z.strictObject({
  /** Zero-based inclusive row index where this candidate range begins. */
  start_row: z.number().int(),
  /** Zero-based exclusive row index immediately after the candidate range. */
  end_row: z.number().int(),
  /**
   * Structural category used by the frontend's initial folding policy.
   *
   * Function-like and class-like values name declarations, container names a
   * structural body, section names a document section, and top-level groups
   * unchanged root items. It is not parser-node identity or current folded state.
   */
  kind: z.enum([
    "function_like",
    "class_like",
    "container",
    "section",
    "top_level",
  ]),
  /** Backend-authored description shown on the folded range. */
  label: z.string(),
});

/**
 * Describes one backend fold suggestion over unchanged rows.
 *
 * The range must never contain a real hunk target. It is an input to frontend
 * fold presentation, not mutable expansion state.
 */
export type FoldHint = z.infer<typeof FoldHintSchema>;

/**
 * Validates one warning attached to the smallest degraded bay result.
 *
 * The type is stable classification and the message is display prose; consumers
 * must not parse prose to recover a warning kind.
 */
const BayWarningSchema = z.strictObject({
  /** Stable non-empty warning category used without parsing display prose. */
  type: z.string().min(1),
  /** Concrete user-visible explanation of the bay's degraded but usable output. */
  message: z.string(),
});

/**
 * Describes non-fatal engine or format damage attached to one bay.
 *
 * Callers display the complete backend message while continuing to render valid
 * file data. It must not be treated as a request error or hidden Toast.
 */
export type BayWarning = z.infer<typeof BayWarningSchema>;

/**
 * Validates changed-line counts for a bay whose content has line semantics.
 *
 * Image bays omit stats rather than sending zeros that imply an engine inspected
 * lines and found no changes.
 */
const BayStatsSchema = z.strictObject({
  /** Non-equal aligned rows in this text bay only. */
  changed_lines: z.number().int(),
  /** Changed bay rows with both source sides present. */
  modified_lines: z.number().int(),
  /** Bay rows containing only a right source line. */
  added_lines: z.number().int(),
  /** Bay rows containing only a left source line. */
  removed_lines: z.number().int(),
  /** Rows this bay's engine classified as moved, excluding token movement. */
  moved_lines: z.number().int(),
});

/**
 * Contains one bay's engine line counts before file-level aggregation.
 *
 * These belong to the bay. When it has a disclosure header, the header may show
 * them even while its body is closed. Side existence is a File fact and lives
 * on the composed-diff `summary`, not here.
 */
export type BayStats = z.infer<typeof BayStatsSchema>;

/**
 * Validates an ordinary composition-level change classification.
 *
 * Movement uses its separate object arm because it carries old and new heading
 * context in addition to a status kind.
 */
const ChangeStatusSchema = z.strictObject({
  /** Whole-bay semantic outcome for a bay that did not move between frames. */
  kind: z.enum(["added", "removed", "changed", "unchanged"]),
});

/**
 * Validates a moved composition unit and its optional source headings.
 *
 * The format builder supplies this metadata; presentation never infers movement
 * by comparing rendered content.
 */
const MovedChangeStatusSchema = z.strictObject({
  /** Discriminant selecting a bay whose logical frame position changed. */
  kind: z.literal("moved"),
  /** Old frame heading, or null when the left position had no useful name. */
  from_heading: z.string().nullable(),
  /** New frame heading, or null when the right position has no useful name. */
  to_heading: z.string().nullable(),
});

/**
 * Validates the text arm of a composed bay's content.
 *
 * It carries rendered rows, fold hints, and its own statistics together so the
 * browser never needs to run or approximate an engine.
 */
const TextKindPayloadSchema = z.strictObject({
  /** Discriminant selecting the row-based text widget. */
  kind: z.literal("text"),
  /** Format-authored caption for this bay's old-side column. */
  left_label: z.string(),
  /** Format-authored caption for this bay's new-side column. */
  right_label: z.string(),
  /** Complete aligned rows in renderer source order. */
  rows: z.array(DiffRowSchema),
  /** Valid candidate ranges whose indexes address `rows`. */
  fold_hints: z.array(FoldHintSchema),
  /** Engine line totals for this bay before File aggregation. */
  stats: BayStatsSchema,
});

/**
 * Contains what a `text` bay holds: rows decorated by the shared renderer.
 *
 * This arm contains only line-oriented renderer output. The enclosing bay keeps
 * identity, change classification, warnings, and disclosure policy.
 */
export type TextKindPayload = z.infer<typeof TextKindPayloadSchema>;

/**
 * Validates captured image-side facts used to construct media presentation.
 *
 * The reference describes bytes but neither contains nor fetches them. The
 * media endpoint remains the only byte source.
 */
const MediaRefSchema = z.strictObject({
  /** Non-empty captured media type used for presentation, not content negotiation. */
  media_type: z.string().min(1),
  /** Exact non-negative captured byte count. */
  byte_size: z.number().int().nonnegative(),
  /** Non-empty backend digest identifying the captured bytes. */
  digest: z.string().min(1),
});

/**
 * Describes one captured media side without carrying its bytes.
 *
 * The record proves that a captured side exists without carrying its bytes.
 * Media content comes from `/api/file-media`, addressed by Snapshot, side, and
 * File pair, never inline in this payload.
 */
export type MediaRef = z.infer<typeof MediaRefSchema>;

/**
 * Validates the image arm with one explicit nullable reference per side.
 *
 * Null means that captured side does not exist; it never triggers a substitute
 * URL or byte source in the widget.
 */
const ImageKindPayloadSchema = z.strictObject({
  /** Discriminant selecting the browser image widget. */
  kind: z.literal("image"),
  /** Captured old-side media facts, or null when that side is absent. */
  left: MediaRefSchema.nullable(),
  /** Captured new-side media facts, or null when that side is absent. */
  right: MediaRefSchema.nullable(),
});

/**
 * Contains what an `image` bay holds: two optional references to pictures.
 *
 * A null side is absent rather than an empty picture. The widget obtains bytes
 * from the media endpoint and lets the browser decode their dimensions.
 */
export type ImageKindPayload = z.infer<typeof ImageKindPayloadSchema>;

/**
 * Validates a bay's content arm by its renderer-independent kind.
 *
 * Consumers narrow this discriminant at the widget dispatch point while keeping
 * the enclosing bay identity unchanged.
 */
const BayKindPayloadSchema = z.discriminatedUnion("kind", [
  TextKindPayloadSchema,
  ImageKindPayloadSchema,
]);

/**
 * Contains one bay's content, dispatched to a widget by its `kind`.
 *
 * Two variants, because there are two things a reviewer can look at: lines, and
 * a picture. Named facts about bytes, such as a blob File's only bay and an
 * image File's facts bay, are lines. They arrive as `text` and need no variant.
 * A new kind is a variant here plus a matching widget; nothing about frames, hunk
 * numbering, or the composed-diff envelope changes to admit it.
 */
export type BayKindPayload = z.infer<typeof BayKindPayloadSchema>;

/**
 * Validates one complete composed bay envelope and its narrowed content.
 *
 * Identity, labels, change facts, warnings, and content stay attached as one
 * navigable unit throughout frontend rendering.
 */
const BayPayloadSchema = z.strictObject({
  /** Non-empty File-local coordinate shared with line pins and review targets. */
  bay_key: z.string().min(1),
  /** User-visible bay caption used by optional chrome and inline text layout. */
  label: z.string(),
  /** Additional user-visible context, or null when the bay has none. */
  detail: z.string().nullable(),
  /** Whether the bay may expose a disclosure control for its body. */
  collapsible: z.boolean(),
  /** Initial body visibility, meaningful only under the bay disclosure policy. */
  default_expanded: z.boolean(),
  /** Format-authored whole-bay change, including optional movement context. */
  change: z.discriminatedUnion("kind", [
    ChangeStatusSchema,
    MovedChangeStatusSchema,
  ]),
  /** Non-fatal damage attached to this bay in backend order. */
  warnings: z.array(BayWarningSchema),
  /** Validated content arm dispatched to exactly one matching widget. */
  kind_data: BayKindPayloadSchema,
});

/**
 * Represents one bay of a composed diff: its identity, plus what it holds.
 *
 * Bay chrome, visibility, navigation, and tinting consume the envelope without
 * interpreting its content arm. The frame walk narrows content once at widget
 * dispatch.
 */
export type BayPayload = z.infer<typeof BayPayloadSchema>;

/**
 * Names what happened to one bay, as the format builder determined it.
 *
 * Only the builder can answer this. A notebook cell that moved and one whose
 * output changed beyond its rendered text both produce rows identical on both
 * sides, so the frontend renders this value as a tint and status and never
 * infers it. A `moved` variant carries the name the bay wore in the old and
 * the new document, `null` on a side the builder cannot name; every other
 * outcome is fully told by its `kind`.
 */
export type BayChange = BayPayload["change"];

/**
 * Validates one ordered logical frame and the non-empty bays it contains.
 *
 * The optional heading is backend-authored format context. A bay-less frame is
 * rejected because it has no reviewable content or change state.
 */
const FrameSchema = z.strictObject({
  /** Non-empty backend identity for this logical part of the File. */
  frame_key: z.string().min(1),
  /** Backend-authored frame caption, or null for a heading-less frame. */
  heading: z.string().nullable(),
  /**
   * Non-empty ordered bays that define the frame's visible status and content.
   *
   * Composition creates a frame only after its first bay exists. Rejecting an
   * empty array exposes backend damage instead of rendering an untinted heading
   * with no reviewable content.
   */
  bays: z.array(BayPayloadSchema).min(1),
});

/**
 * Contains one presentational frame: an optional heading over ordered bays.
 *
 * A frame carries no annotations of its own. Navigation and review targets
 * belong to its bays. A bay with `collapsible=true` also lets the reviewer open
 * or close its body; other bays remain shown.
 */
export type Frame = z.infer<typeof FrameSchema>;

/**
 * Validates the complete multi-frame representation returned for one File.
 *
 * The response keeps format composition and per-bay renderer results together;
 * the client must not split them into competing stores.
 */
const ComposedDiffSchema = z
  .strictObject({
    /** Complete File caption used by the card header and activity presentation. */
    display_name: z.string(),
    /** User-visible caption for the captured old File side. */
    left_label: z.string(),
    /** User-visible caption for the captured new File side. */
    right_label: z.string(),
    /** File-wide text-line totals and captured-side existence. */
    summary: TextFileSummarySchema,
    /** Tracked or untracked provenance repeated with the rendered response. */
    file_kind: FileKindSchema,
    /** Non-empty old-side path, or null when absent from capture. */
    left_path: FilePathSchema.nullable(),
    /** Non-empty new-side path, or null when absent from capture. */
    right_path: FilePathSchema.nullable(),
    /** Backend policy for initial File expansion before an explicit client choice. */
    default_expanded: z.boolean(),
    /** Ordered logical frames containing all renderable bays. */
    frames: z.array(FrameSchema),
  })
  .superRefine(validateFilePaths);

/**
 * Contains the single renderable response shape returned by `/api/file-diff`.
 *
 * At least one normalized path is present. A plain text File is represented by
 * one heading-less frame containing the conventional flat-file text bay; callers
 * do not branch on a separate render kind.
 */
export type FileDiff = z.infer<typeof ComposedDiffSchema>;

/**
 * Validates the opaque hexadecimal identity shared by review entities and Snapshots.
 *
 * Consumers compare and transmit the full value but never parse its bytes or
 * infer which entity kind it identifies from the spelling.
 */
export const ReviewIdSchema = z.string().regex(/^[0-9a-f]{32}$/u);

/**
 * Identifies one review entity, operation, or retained Snapshot opaquely.
 *
 * The spelling does not encode the entity kind. Callers keep IDs in their typed
 * field context and compare or transmit the full value without parsing it.
 */
export type ReviewId = z.infer<typeof ReviewIdSchema>;

/**
 * Validates one normalized repository-relative path used in review coordinates.
 *
 * Absolute paths, empty segments, and dot traversal are rejected before a path
 * can enter persisted review addressing or media URLs.
 */
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

/**
 * Validates the complete nullable path pair identifying one captured File.
 *
 * At least one normalized side must exist. The pair remains intact for renames,
 * where neither path alone is a stable identity.
 */
export const ReviewFilePairSchema = z
  .strictObject({
    /** Normalized old-side path, or null when the captured File lacks that side. */
    left_path: ReviewFilePathSchema.nullable(),
    /** Normalized new-side path, or null when the captured File lacks that side. */
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

/**
 * Identifies one captured File through its complete nullable path pair.
 *
 * At least one side exists. Both remain present for a rename, so consumers keep
 * the pair intact across review, media, line-pin, and navigation coordinates.
 */
export type ReviewFilePair = z.infer<typeof ReviewFilePairSchema>;

/**
 * Validates one non-empty one-based inclusive review line range.
 *
 * The refinement rejects reversed endpoints before a target can be persisted or
 * used for marker and excerpt calculations.
 */
export const ReviewLineRangeSchema = z
  .strictObject({
    /** Positive one-based first line included in the review range. */
    start_line: z.number().int().positive(),
    /** Positive one-based final line, never before `start_line`. */
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

/**
 * Identifies a non-empty one-based inclusive review line range.
 *
 * The validated end never precedes the start. Callers use the same coordinates
 * for marker selection, persisted targets, and origin excerpts.
 */
export type ReviewLineRange = z.infer<typeof ReviewLineRangeSchema>;

/**
 * Names the bay a flatfile composes into.
 *
 * A flatfile is a File no format claims. It has no internal structure, so
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
 *
 * @param snapshotId Opaque Snapshot that captured the requested bytes.
 * @param file Complete nullable File pair used by all sub-file coordinates.
 * @param side Captured side whose original bytes the browser should fetch.
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

/**
 * Validates one non-empty public bay key used by review coordinates.
 *
 * The validator treats keys as opaque composition output and never recognizes a
 * format or widget from their text.
 */
export const ReviewTextBaySchema = z.strictObject({
  /** Non-empty File-local key passed through without format or widget inference. */
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

/**
 * Validates one text review target and the existence of its selected File side.
 *
 * The target keeps File, bay, side, and inclusive range together. The refinement
 * rejects coordinates aimed at a nullable side absent from the pair.
 */
const TextReviewTargetSchema = z
  .strictObject({
    /** Discriminant selecting the line-oriented review target contract. */
    kind: z.literal("text"),
    /** Complete captured File pair that remains intact across renames. */
    file: ReviewFilePairSchema,
    /** Opaque composed bay containing the selected line range. */
    bay: ReviewTextBaySchema,
    /** Captured side whose path must be present in `file`. */
    side: z.enum(["left", "right"]),
    /** Non-empty inclusive backend line range on the selected side. */
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
/**
 * Public runtime validator for review targets accepted by browser write commands.
 *
 * It currently exposes the text-target contract directly; adding another target
 * kind requires an explicit public union rather than permissive parsing.
 */
export const ReviewTargetSchema = TextReviewTargetSchema;

/**
 * Identifies one exact rendered text range accepted for Thread creation.
 *
 * Callers construct this complete coordinate from a mounted line host and must
 * not assemble a partial or side-absent target.
 */
export type ReviewTarget = z.infer<typeof ReviewTargetSchema>;

/**
 * Validates immutable display attribution returned with one review Comment.
 *
 * The record binds a positive Profile identity to the name captured for display;
 * it does not contain preferences or authentication state.
 */
const ReviewAuthorSchema = z.strictObject({
  /** Positive Profile identity used for authorship checks. */
  profile_id: z.number().int().positive(),
  /** Non-empty author name captured for immutable Comment presentation. */
  display_name: z.string().min(1),
});

/**
 * Describes immutable Profile attribution returned with a review Comment.
 *
 * The positive identity supports authorship checks while the captured display
 * name is presentation. It does not represent current login or preferences.
 */
export type ReviewAuthor = z.infer<typeof ReviewAuthorSchema>;

/**
 * Validates one current Comment or retained deletion tombstone.
 *
 * The refinement keeps deletion and body absence equivalent, while sequence and
 * revision remain nonnegative server-authored ordering values.
 */
const ReviewCommentSchema = z
  .strictObject({
    /** Opaque identity retained even after the Comment becomes a tombstone. */
    comment_id: ReviewIdSchema,
    /** Zero-based contiguous position inside the containing Thread. */
    sequence: z.number().int().nonnegative(),
    /** Immutable Profile attribution returned with this Comment. */
    author: ReviewAuthorSchema,
    /** Server revision required by later edit and delete actions. */
    revision: z.number().int().nonnegative(),
    /** Complete current text, or null exactly when `deleted` is true. */
    body: z.string().nullable(),
    /** Whether the record is a retained deletion tombstone with no body. */
    deleted: z.boolean(),
    /** Offset-aware creation timestamp supplied by the backend. */
    created_at: z.string().datetime({ offset: true }),
    /** Offset-aware timestamp of the latest accepted Comment revision. */
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

/**
 * Describes one current Comment or its retained deletion tombstone.
 *
 * Tombstones retain identity and attribution. Callers use the server ordering and
 * revision directly rather than sorting or manufacturing optimistic revisions.
 */
export type ReviewComment = z.infer<typeof ReviewCommentSchema>;

/**
 * Validates every current-Snapshot placement outcome for a persisted Thread.
 *
 * Each arm carries only data newly determined by derivation; immutable File,
 * side, and usually bay identity remain on the origin.
 */
const ThreadPlacementSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    /** Discriminant for an unchanged region whose current range remains valid. */
    kind: z.literal("region-kept"),
    /** Current inclusive range corresponding to the immutable origin. */
    range: ReviewLineRangeSchema,
  }),
  z.strictObject({
    /** Discriminant for a region still found at current lines but changed in content. */
    kind: z.literal("region-changed"),
    /** Current inclusive range at which the changed origin was reattached. */
    range: ReviewLineRangeSchema,
  }),
  z.strictObject({
    /** Origin bay remains, but no current region matches the stored excerpt. */
    kind: z.literal("region-lost"),
  }),
  z.strictObject({
    /** Origin bay disappeared; `bay` is the current whole-bay landing selected by derivation. */
    kind: z.literal("bay-lost"),
    /** Opaque current bay used for navigation instead of the lost origin bay. */
    bay: ReviewTextBaySchema,
  }),
  z.strictObject({
    /** Origin side no longer exists in the current File pair. */
    kind: z.literal("side-lost"),
  }),
  z.strictObject({
    /** Origin File is absent from the current Snapshot. */
    kind: z.literal("file-absent"),
  }),
  z.strictObject({
    /** Current File exists but could not produce reviewable content. */
    kind: z.literal("file-unreadable"),
  }),
  z.strictObject({
    /** File-level historical Thread remains valid without a text range. */
    kind: z.literal("whole-file"),
  }),
]);

/**
 * Identifies where one Thread sits in a Snapshot, and what became of it.
 *
 * Each variant states only facts newly determined in the current Snapshot.
 * Immutable File, side, and usually bay identity remain on the origin.
 */
export type ThreadPlacement = z.infer<typeof ThreadPlacementSchema>;

/**
 * Validates one non-empty origin excerpt and its selected subrange.
 *
 * The refinement proves both selected endpoints lie inside the stored line array
 * so presentation can index it without repair or clipping.
 */
const ReviewExcerptSchema = z
  .strictObject({
    /** Captured side from which every stored excerpt line came. */
    side: z.enum(["left", "right"]),
    /** Positive one-based line number corresponding to `lines[0]`. */
    start_line: z.number().int().positive(),
    /** First selected origin line, constrained inside the stored excerpt. */
    selected_start_line: z.number().int().positive(),
    /** Last selected origin line, no earlier than `selected_start_line`. */
    selected_end_line: z.number().int().positive(),
    /** Non-empty immutable source lines retained from the origin Snapshot. */
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

/**
 * Describes a bounded selected-side excerpt retained from the origin Snapshot.
 *
 * The selected inclusive range lies inside the non-empty stored lines. Current
 * placement may move independently, but this historical context never changes.
 */
export type ReviewExcerpt = z.infer<typeof ReviewExcerptSchema>;

/**
 * Validates an immutable text target together with its captured origin excerpt.
 *
 * This is persistence history, not the Thread's placement in the current Snapshot.
 */
const TextReviewOriginSchema = TextReviewTargetSchema.extend({
  /** Immutable selected-side context captured when the Thread was created. */
  excerpt: ReviewExcerptSchema,
});
/**
 * Validates the retained File-start origin used by File-level historical Threads.
 *
 * It has no bay, range, or excerpt because its coordinate is the captured File
 * header rather than rendered text.
 */
const FileStartReviewOriginSchema = z.strictObject({
  /** Discriminant for a historical Thread attached to the File header. */
  kind: z.literal("file-start"),
  /** Complete captured File identity retained across later Snapshots. */
  file: ReviewFilePairSchema,
  /** Captured side with which the File-level Thread was associated. */
  side: z.enum(["left", "right"]),
});
/**
 * Validates either immutable Thread-origin form by its target kind.
 *
 * Consumers narrow this history separately from current placement and never
 * merge the two into one mutable coordinate.
 */
const ReviewOriginSchema = z.discriminatedUnion("kind", [
  TextReviewOriginSchema,
  FileStartReviewOriginSchema,
]);

/**
 * Identifies the immutable creation target of one Thread.
 *
 * This immutable history remains separate from current placement. Text origins
 * retain their selected excerpt; File-level historical origins have no text
 * coordinate.
 */
export type ReviewOrigin = z.infer<typeof ReviewOriginSchema>;

/**
 * Validates one complete Thread, its origin, current placement, and Comments.
 *
 * Comment refinement enforces contiguous sequence order and unique identities so
 * discussion rendering never needs to sort or deduplicate backend data.
 */
const ReviewThreadSchema = z.strictObject({
  /** Opaque Thread identity used by all discussion and lifecycle actions. */
  thread_id: ReviewIdSchema,
  /** Snapshot whose canonical query contains this current Thread representation. */
  snapshot_id: ReviewIdSchema,
  /** Offset-aware timestamp of Thread creation. */
  created_at: z.string().datetime({ offset: true }),
  /**
   * Current lifecycle state: open accepts ordinary actions, resolved records
   * completed work, and deleted remains as a retained tombstone.
   */
  state: z.enum(["open", "resolved", "deleted"]),
  /**
   * Roles currently expected to act: author, reviewer, both, or neither.
   * Callers use the backend value directly instead of deriving it from Comments.
   */
  attention: z.enum(["author", "reviewer", "both", "none"]),
  /** Server discussion revision required by state-changing actions. */
  discussion_revision: z.number().int().nonnegative(),
  /** Immutable creation coordinate and historical excerpt. */
  origin_target: ReviewOriginSchema,
  /** Current-Snapshot derivation outcome interpreted relative to `origin_target`. */
  placement: ThreadPlacementSchema,
  /** Non-empty contiguous Comment history with identities unique in this Thread. */
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

/**
 * Describes one complete runtime-validated discussion in an exact Snapshot.
 *
 * The Thread keeps immutable origin separate from current placement. Callers use
 * its validated Comment order and server revisions directly.
 */
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

/**
 * Addresses the exact current code landing available for one Thread.
 *
 * It combines immutable File and side origin with the bay and line chosen by
 * current placement. Threads without a code landing produce no value of this type.
 */
export type ThreadCodePoint = {
  /**
   * Complete captured File pair containing the navigable code.
   *
   * It comes from immutable Thread origin and remains a pair across renames.
   */
  file: ReviewFilePair;
  /**
   * Public bay identity that currently contains the navigation landing.
   *
   * A `bay-lost` placement may supply this instead of the origin bay; callers
   * treat it as opaque.
   */
  bay: ReviewTextBay;
  /**
   * Captured side on which navigation should locate the code line.
   *
   * It always comes from Thread origin and is present in the File pair.
   */
  side: "left" | "right";
  /**
   * One-based line at which navigation should land inside the bay.
   *
   * Region placements use their current start; whole-bay landings use line one.
   */
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
 *
 * # Returns
 *
 * - `ThreadCodePoint`: The complete current code coordinate.
 * - `null`: The File-level origin names no bay. Callers must keep that Thread
 *   in History and omit code navigation.
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

/**
 * Validates the authoritative Thread fragment returned by one accepted write.
 *
 * The nullable Comment distinguishes state-only actions from Comment writes,
 * while revision and attention always describe the post-write Thread.
 */
const ReviewThreadUpdateSchema = z.strictObject({
  /** Thread identity repeated by the accepted action response. */
  thread_id: ReviewIdSchema,
  /** Snapshot identity repeated for publication into the addressed query. */
  snapshot_id: ReviewIdSchema,
  /** Authoritative post-write open, resolved, or retained-deleted lifecycle state. */
  state: z.enum(["open", "resolved", "deleted"]),
  /** Authoritative post-write author, reviewer, both-role, or no-role attention. */
  attention: z.enum(["author", "reviewer", "both", "none"]),
  /** Authoritative post-write discussion revision. */
  discussion_revision: z.number().int().nonnegative(),
  /** Added or changed Comment, or null for a Thread-only lifecycle action. */
  comment: ReviewCommentSchema.nullable(),
});

/**
 * Describes the authoritative Thread fragment returned by one accepted action.
 *
 * Callers verify the repeated identities and merge this authoritative fragment
 * into the addressed canonical Thread only.
 */
export type ReviewThreadUpdate = z.infer<typeof ReviewThreadUpdateSchema>;

/**
 * Validates one bounded page of Threads under a fixed Snapshot activity boundary.
 *
 * Refinement rejects cross-Snapshot Threads and duplicate Thread or Comment IDs,
 * so page assembly can append without repairing backend identity.
 */
const ReviewThreadPageSchema = z
  .strictObject({
    /** Snapshot identity shared by every Thread in this transport page. */
    snapshot_id: ReviewIdSchema,
    /** Fixed activity boundary established by page one and repeated thereafter. */
    through_activity_id: z.number().int().nonnegative(),
    /** Validated page slice in canonical backend order. */
    threads: z.array(ReviewThreadSchema),
    /** Positive page number requested and repeated by the backend. */
    page: z.number().int().positive(),
    /** Positive transport page size repeated for response verification. */
    limit: z.number().int().positive(),
    /** Total Threads expected across all pages at the activity boundary. */
    total_threads: z.number().int().nonnegative(),
    /** Whether the caller must fetch the next consecutive page. */
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

/**
 * Describes one explicitly bounded transport page for an exact Snapshot.
 *
 * The first page establishes the activity boundary. Pagination code validates and
 * appends every later page before exposing the complete canonical Thread set.
 */
type ReviewThreadPage = z.infer<typeof ReviewThreadPageSchema>;

/**
 * Validates review input as non-empty text containing at least one non-space.
 *
 * It preserves accepted whitespace verbatim and rejects only bodies that cannot
 * produce visible Comment content.
 */
const ReviewBodySchema = z
  .string()
  .min(1)
  .refine((body) => body.trim().length > 0, {
    message: "Review bodies cannot contain only whitespace.",
  });
/**
 * Validates authorship, exact target, and first body for Thread creation.
 *
 * All three values are required because the backend creates the Thread and its
 * first Comment atomically.
 */
const CreateReviewThreadRequestSchema = z.strictObject({
  /** Existing Profile attributed as author of the first Comment. */
  profile_id: z.number().int().positive(),
  /** Complete valid code coordinate for the new Thread. */
  target: ReviewTargetSchema,
  /** Nonblank first Comment text preserved verbatim after validation. */
  body: ReviewBodySchema,
});
/**
 * Validates one authored reply and its attention effect.
 *
 * Attention is an explicit action choice, not a default inferred from body or
 * current Thread state.
 */
const AddReviewCommentRequestSchema = z.strictObject({
  /** Existing Profile attributed as author of this reply. */
  profile_id: z.number().int().positive(),
  /** Nonblank reply text preserved verbatim after validation. */
  body: ReviewBodySchema,
  /**
   * Explicit attention effect applied with the reply: alert raises both-role
   * attention, while inert preserves the current attention state.
   */
  attention: z.enum(["inert", "alert"]),
});
/**
 * Validates the Profile and complete replacement body for a Comment edit.
 *
 * Comment and Thread identities remain URL parameters, so the JSON body cannot
 * disagree with the addressed entities.
 */
const EditReviewCommentRequestSchema = z.strictObject({
  /** Existing Profile required to match Comment authorship. */
  profile_id: z.number().int().positive(),
  /** Complete nonblank replacement text, never a patch. */
  body: ReviewBodySchema,
});
/**
 * Validates authorship for a review action whose other identity is in the URL.
 *
 * The body contains only the acting Profile, keeping deletion and state-action
 * semantics in their explicit endpoint and path.
 */
const ReviewProfileActionRequestSchema = z.strictObject({
  /** Existing Profile attributed to the endpoint's explicit lifecycle action. */
  profile_id: z.number().int().positive(),
});

/**
 * Carries validated authorship, code target, and first body for Thread creation.
 *
 * Snapshot identity is added separately by private transport so one command body
 * cannot conflict with the addressed Snapshot.
 */
export type CreateReviewThreadRequest = z.infer<
  typeof CreateReviewThreadRequestSchema
>;
/**
 * Carries validated authorship, reply body, and attention effect for one Comment.
 *
 * Snapshot and Thread identities are transport inputs; this value contains no
 * default action or local draft state.
 */
export type AddReviewCommentRequest = z.infer<
  typeof AddReviewCommentRequestSchema
>;
/**
 * Carries validated Profile attribution and complete replacement Comment text.
 *
 * The endpoint separately addresses the Comment, so this value cannot name a
 * different entity or encode a partial patch.
 */
export type EditReviewCommentRequest = z.infer<
  typeof EditReviewCommentRequestSchema
>;
/**
 * Attributes one Comment or Thread action to an existing Profile.
 *
 * Action meaning and entity identity remain in the explicit endpoint and path;
 * this body cannot invent another transition field.
 */
export type ReviewProfileActionRequest = z.infer<
  typeof ReviewProfileActionRequestSchema
>;

/**
 * Validates dirdiff's ordinary JSON error envelope.
 *
 * Transport handling uses its complete error text when no structured review
 * domain failure applies.
 */
const ErrorResponseSchema = z.strictObject({
  /** Complete backend error text used when no structured domain failure applies. */
  error: z.string(),
});

/**
 * Validates the stable codes emitted by browser review domain failures.
 *
 * Callers may branch on these values. Human-readable response messages remain
 * presentation text and must not create additional classifications.
 */
const ReviewErrorCodeSchema = z.enum([
  /** The acting Profile identity does not exist. */
  "profile_not_found",
  /** The addressed Thread identity does not exist. */
  "thread_not_found",
  /** The addressed Comment identity does not exist. */
  "comment_not_found",
  /** Authored input or its captured target is invalid. */
  "invalid_target",
  /** The supplied discussion revision is stale. */
  "revision_conflict",
  /** The action cannot follow the Thread's current state. */
  "state_conflict",
  /** The acting Profile may not perform the action. */
  "forbidden",
]);
/**
 * Validates one structured review failure body.
 *
 * It pairs a stable code with non-empty display text before transport handling
 * constructs `ReviewRequestError`.
 */
const ReviewErrorResponseSchema = z.strictObject({
  /** Stable review failure classification used for caller behavior. */
  code: ReviewErrorCodeSchema,
  /** Non-empty user-visible explanation kept separate from classification. */
  message: z.string().min(1),
});

/**
 * Classifies one stable browser review domain failure.
 *
 * Consumers branch on the code for behavior and present the separately validated
 * message as prose. Unknown codes fail response validation.
 */
export type ReviewErrorCode = z.infer<typeof ReviewErrorCodeSchema>;

/**
 * Validates FastAPI's plain HTTP exception envelope.
 *
 * It is checked after dirdiff-specific error shapes and contributes only its
 * detail text to the visible transport failure.
 */
const HttpExceptionResponseSchema = z.strictObject({
  /** FastAPI exception detail used as ordinary transport failure text. */
  detail: z.string(),
});

/**
 * Transport deadline for ordinary reads and lightweight mutations.
 *
 * This bounds one HTTP attempt only; TanStack retries are disabled separately.
 */
const REQUEST_TIMEOUT_MS = 8_000;
/**
 * Longer initial file-render deadline reserved for measured heavy engines.
 *
 * Explicit user retry may select the unbounded policy instead of this timer.
 */
const SLOW_DIFF_TIMEOUT_MS = 20_000;
/**
 * Transport deadline for forge preparation, including remote network and Git work.
 *
 * The value applies to the preparation attempt and never to later Snapshot reads.
 */
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
  /**
   * Complete URL or Request-compatible address passed directly to `fetch`.
   *
   * Callers construct all path and search parameters before entering transport.
   */
  input: string;
  /**
   * Complete fetch initialization, including method, headers, and body.
   *
   * Transport adds only the combined AbortSignal and never invents HTTP options.
   */
  init: RequestInit;
  /**
   * Caller cancellation source, or `null` for mutations without one.
   *
   * A present AbortSignal is forwarded with its exact reason and detached after
   * the attempt settles.
   */
  abortSignal: AbortSignal | null;
  /**
   * Positive finite transport deadline in milliseconds, or `null` for no timer.
   *
   * Null is explicit policy for irreversible writes and user-requested unbounded
   * file retry; it is not a missing default.
   */
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
  /**
   * Combined browser AbortSignal passed to the associated `fetch` attempt.
   *
   * It aborts for either caller cancellation or this instance's optional timer.
   */
  abortSignal: AbortSignal;
  /**
   * Releases the caller listener and transport timer retained by this instance.
   *
   * The request boundary calls it exactly once after settlement. It does not
   * abort work or change timeout classification, and repeated use is unnecessary.
   */
  dispose(): void;
  /**
   * Reports whether this instance's timer, rather than the caller, caused abort.
   *
   * Transport reads it only while classifying a failed settled attempt. It does
   * not wait, mutate cancellation, or inspect arbitrary abort reasons.
   */
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
  /**
   * Stable Toast-lifetime classification attached to this transport failure.
   *
   * Presentation may expire `timeout` errors; `other` remains until dismissal.
   * No other reason value is represented.
   */
  readonly error_reason: RequestErrorReason;

  /**
   * Constructs one classified request failure from complete transport context.
   *
   * `cause` is null when no underlying exception exists and otherwise preserves
   * the original thrown value. Callers must provide every argument explicitly.
   *
   * @param errorReason Stable timeout or ordinary failure classification.
   * @param message Complete user-visible transport failure text.
   * @param cause Original thrown value, or `null` when no exception caused it.
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
  /**
   * Stable browser review domain classification validated from the response.
   *
   * Consumers branch on this code and use inherited message text only for
   * presentation.
   */
  readonly code: ReviewErrorCode;

  /**
   * Constructs one failure from the complete structured review error body.
   *
   * The failure always has ordinary persistent Toast lifetime because review
   * codes describe domain rejection, not transport timeout.
   *
   * @param code Validated stable domain classification.
   * @param message Non-empty display text returned with that classification.
   */
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
 *
 * @param callerAbortSignal Caller cancellation to forward, or explicit `null`.
 * @param timeoutMs Positive finite deadline in milliseconds, or explicit `null`.
 */
function createMultiAbortSignal(
  callerAbortSignal: AbortSignal | null,
  timeoutMs: number | null,
): MultiAbortSignal {
  const abortController = new AbortController();
  let didTimeout = false;
  assert(
    timeoutMs === null || (Number.isFinite(timeoutMs) && timeoutMs > 0),
    "HTTP timeout must be a positive finite duration.",
  );

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
 * their message; unknown or plain-text bodies become `RequestError.message`
 * unchanged.
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
 *
 * @param request Complete HTTP execution and cancellation policy.
 * @param schema Runtime validator authoritative for the successful JSON body.
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
 *
 * @param projectId Repository whose complete ref choices are requested.
 * @param abortSignal Query cancellation that bounds this read attempt.
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
  /**
   * Constructs the stable domain failure for an uninferable base branch.
   *
   * The response schema has already proved this exact failure arm. The instance
   * carries no repository or transport data and selects only query failure
   * handling and its specific Toast title.
   */
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
 *
 * @param projectId Repository whose branch-review defaults are requested.
 * @param abortSignal Query cancellation that bounds this read attempt.
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
  assert(
    response.status === 204,
    `Repository deletion requires 204 No Content; received ${response.status} ${response.statusText}.`,
  );
}

/**
 * Saves one repository's complete main-branch selection.
 *
 * Callers provide both the repository identity and an explicit local or remote
 * selection. The validated backend entity is returned without updating caches.
 */
function requestSaveMainBranch(input: {
  /**
   * Exact repository whose stored main branch will change.
   *
   * It comes from validated repository selection and is repeated in the URL, not
   * in the JSON body.
   */
  projectId: ProjectId;
  /**
   * Complete local or remote branch selection to persist.
   *
   * The backend receives the discriminated object unchanged; the command does
   * not derive a remote or substitute a default.
   */
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

/**
 * Selects the one existing Profile with an exact submitted username.
 *
 * Login never creates a missing Profile or changes the supplied text. The caller
 * persists selection only after the validated identity returns.
 */
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
  /**
   * Positive backend identity of the Profile being renamed.
   *
   * It selects the URL resource and never comes from the submitted new name.
   */
  profileId: number;
  /**
   * Complete replacement display name accepted by the caller's input boundary.
   *
   * Backend validation remains authoritative; this function does not trim or
   * repair it before submission.
   */
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
 *
 * @param profileId Exact selected Profile whose preferences are read.
 * @param abortSignal Query cancellation that bounds this read attempt.
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
  /**
   * Exact Profile whose persisted preferences will change.
   *
   * It addresses the URL resource and must match the caller's selected Profile.
   */
  profileId: number;
  /**
   * Complete accepted value for the aggressive-folding preference.
   *
   * The boolean is sent directly and the validated response remains authoritative
   * for cache and presentation updates.
   */
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
 *
 * @param params Complete selected Tab value serialized without reconstruction.
 * @param abortSignal TanStack cancellation that bounds this manifest attempt.
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
 *
 * @param snapshotId Opaque manifest Snapshot whose delayed files are described.
 * @param abortSignal TanStack cancellation that bounds this lazy-info attempt.
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
 *
 * @param engine Backend renderer selected for this file attempt.
 * @param snapshotId Opaque Snapshot established by the containing manifest.
 * @param entry Exact thin manifest handle for the File being rendered.
 * @param abortSignal TanStack cancellation that bounds this file attempt.
 * @param timeout Explicit bounded initial load or unbounded user-retry policy.
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

/**
 * Reads every bounded transport page into one complete Snapshot Thread set.
 *
 * The first page fixes the append-only activity boundary used by every later
 * page. The function rejects identity drift, duplicates, or an incomplete final
 * count instead of returning a repaired collection.
 *
 * @param snapshotId Exact Snapshot whose canonical Thread set is read.
 * @param abortSignal TanStack cancellation shared by every page in this read.
 */
async function requestReviewThreads(
  snapshotId: ReviewId,
  abortSignal: AbortSignal,
): Promise<ReviewThread[]> {
  /**
   * Reads and validates one transport page within this complete Snapshot read.
   *
   * `pageNumber` is the exact positive page requested. `throughActivityId` is
   * `null` only for the first page; later calls pass the boundary returned by
   * that first response. The helper shares the outer Snapshot and AbortSignal.
   *
   * @param pageNumber Positive transport page to request and verify.
   * @param throughActivityId Fixed first-page activity boundary, or `null` initially.
   */
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
    assert(
      response.snapshot_id === snapshotId && response.page === pageNumber,
      "Review response returned another page identity.",
    );
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
    } else {
      assert(
        response.through_activity_id === throughActivityId,
        "Review transport pages use different activity pivots.",
      );
    }
    for (const thread of response.threads) {
      assert(
        !threadIds.has(thread.thread_id),
        "Review transport pages contain a duplicate Thread.",
      );
      threadIds.add(thread.thread_id);
      threads.push(thread);
    }
    if (!response.has_more) {
      assert(
        threads.length === response.total_threads,
        "Review transport pages do not contain every Thread.",
      );
      return threads;
    }
    pageNumber += 1;
  }
}

/**
 * Carries one complete Thread-creation command into the private mutation function.
 *
 * Snapshot addressing stays outside the validated JSON body until transport
 * assembles the endpoint payload, preventing a body from naming another Snapshot.
 */
type CreateReviewThreadInput = {
  /**
   * Exact Snapshot in which the new Thread and first Comment are created.
   *
   * The response must repeat this identity or the mutation rejects it.
   */
  snapshotId: ReviewId;
  /**
   * Validated authorship, target, and first Comment body.
   *
   * Transport parses it again at the HTTP boundary before combining it with the
   * Snapshot identity.
   */
  body: CreateReviewThreadRequest;
};

/**
 * Requires one mutation response to match the exact addressed Thread pair.
 *
 * A mismatch throws at the transport boundary so canonical query data cannot be
 * updated with a response for another Snapshot or Thread.
 *
 * @param thread Response fragment carrying backend Snapshot and Thread identities.
 * @param snapshotId Exact Snapshot addressed by the mutation.
 * @param threadId Exact Thread addressed by the mutation.
 */
function assertReviewThreadIdentity(
  thread: {
    /**
     * Snapshot identity repeated by the backend response.
     *
     * It must equal the command's addressed Snapshot before callers publish data.
     */
    snapshot_id: ReviewId;
    /**
     * Thread identity repeated by the backend response.
     *
     * It must equal the command's addressed Thread before callers publish data.
     */
    thread_id: ReviewId;
  },
  snapshotId: ReviewId,
  threadId: ReviewId,
): void {
  assert(
    thread.snapshot_id === snapshotId && thread.thread_id === threadId,
    "Review mutation response returned another Snapshot-bound Thread.",
  );
}

/**
 * Creates one Snapshot-bound Thread and its first Comment atomically.
 *
 * The irreversible write has no transport timeout. Its validated response must
 * repeat the addressed Snapshot before the caller publishes it to canonical data.
 */
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
  assert(
    thread.snapshot_id === input.snapshotId,
    "Review mutation response returned another Snapshot.",
  );
  return thread;
}

/**
 * Carries one complete reply command into the private mutation function.
 *
 * Snapshot and Thread addressing remain transport fields, while the body holds
 * authorship, visible text, and the explicit attention action.
 */
type AddReviewCommentInput = {
  /**
   * Exact Snapshot containing the Thread receiving the reply.
   *
   * The accepted response must repeat it before publication.
   */
  snapshotId: ReviewId;
  /**
   * Exact Thread to which the new Comment is appended.
   *
   * It remains paired with `snapshotId`; a Thread ID alone is not an API address.
   */
  threadId: ReviewId;
  /**
   * Validated Profile, reply body, and attention effect.
   *
   * Transport combines it with the addressed identities without defaulting any
   * action field.
   */
  body: AddReviewCommentRequest;
};

/**
 * Appends one Comment through an exact Snapshot-bound Thread.
 *
 * Transport waits without a timeout for the authoritative update, then verifies
 * both response identities before returning post-write state to the caller.
 */
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

/**
 * Carries one complete Comment-edit command into the private mutation function.
 *
 * The command keeps the containing Thread identity for response verification even
 * though the endpoint directly addresses the Comment.
 */
type EditReviewCommentInput = {
  /**
   * Exact Snapshot containing the authored Comment.
   *
   * It participates in both the request body and response identity assertion.
   */
  snapshotId: ReviewId;
  /**
   * Exact containing Thread expected in the update response.
   *
   * It prevents a valid Comment response from being published under another
   * canonical Thread.
   */
  threadId: ReviewId;
  /**
   * Exact authored Comment whose body is replaced.
   *
   * The ID addresses the endpoint action and is never inferred from Thread order.
   */
  commentId: ReviewId;
  /**
   * Validated Profile attribution and complete replacement text.
   *
   * Editing replaces the body; this command carries no patch or local revision.
   */
  body: EditReviewCommentRequest;
};

/**
 * Replaces one authored Comment body through its exact Snapshot-bound Thread.
 *
 * The response must identify the containing Thread even though the endpoint acts
 * on a Comment, preventing publication into another canonical discussion.
 */
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

/**
 * Carries one exact Comment tombstone action into private transport.
 *
 * Snapshot and Thread identities verify the returned update; the body supplies
 * only the acting Profile because deletion semantics belong to the endpoint.
 */
type ReviewCommentActionInput = {
  /**
   * Exact Snapshot containing the Comment.
   *
   * It is sent and then checked against the accepted update.
   */
  snapshotId: ReviewId;
  /**
   * Exact containing Thread expected in the accepted update.
   *
   * The Thread remains part of canonical query placement after its Comment changes.
   */
  threadId: ReviewId;
  /**
   * Exact Comment to retain as a deletion tombstone.
   *
   * Transport sends it directly and never selects a Comment by sequence.
   */
  commentId: ReviewId;
  /**
   * Validated Profile attribution for the destructive action.
   *
   * No deletion flag is needed because the endpoint itself names the action.
   */
  body: ReviewProfileActionRequest;
};

/**
 * Tombstones one current Comment through an exact Snapshot-bound Thread.
 *
 * The write preserves Comment identity on the backend. Transport verifies the
 * returned Thread pair and never converts deletion into local array removal.
 */
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

/**
 * Carries one exact Thread lifecycle action into private transport.
 *
 * The action verb remains a separate required function parameter so the command
 * body cannot smuggle an unsupported or contradictory transition.
 */
type ReviewThreadActionInput = {
  /**
   * Exact Snapshot containing the Thread whose lifecycle changes.
   *
   * It participates in request addressing and response verification.
   */
  snapshotId: ReviewId;
  /**
   * Exact Thread receiving the explicit resolve, reopen, or delete action.
   *
   * The accepted update must repeat it before publication.
   */
  threadId: ReviewId;
  /**
   * Validated Profile attribution for the lifecycle action.
   *
   * Transition meaning comes only from the separate `action` argument.
   */
  body: ReviewProfileActionRequest;
};

/**
 * Applies one explicit lifecycle action to an exact Snapshot-bound Thread.
 *
 * The irreversible write has no transport timeout and returns only after the
 * authoritative response is validated against the addressed identities.
 *
 * @param action Supported lifecycle transition selecting the exact endpoint.
 * @param input Snapshot-bound Thread identity and acting Profile attribution.
 */
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

/**
 * Shared lifetime policy for immutable Snapshot-addressed query definitions.
 *
 * Snapshot data never becomes stale, and unused results leave the cache
 * immediately because a replacement Snapshot has a different identity. Each
 * definition still supplies its own key, function, and failure title.
 */
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
    /**
     * Defines the complete canonical Thread-set query for an exact Snapshot.
     *
     * `snapshotId` is the whole query identity. Observation reads every bounded
     * page under one activity boundary before exposing data, and creating this
     * definition alone starts no network work.
     */
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
      /**
       * Defines the mutation that creates one Thread and its first Comment.
       *
       * The caller supplies `CreateReviewThreadInput` at execution time and must
       * publish canonical query data after success. Creating the definition performs
       * no write.
       */
      create() {
        return mutationOptions({
          mutationKey: ["review", "thread", "create"] as const,
          mutationFn: requestCreateReviewThread,
          meta: { errorTitle: "Failed to create review Thread" },
        });
      },
      /**
       * Defines one explicit resolve, reopen, or delete Thread mutation.
       *
       * `action` fixes both mutation identity and endpoint before execution. The
       * later input supplies only the addressed Thread and acting Profile.
       */
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
      /**
       * Defines the mutation that appends one Comment to an exact Thread.
       *
       * The execution input includes Snapshot, Thread, authorship, body, and
       * attention action. Callers publish the validated update after success.
       */
      add() {
        return mutationOptions({
          mutationKey: ["review", "comment", "add"] as const,
          mutationFn: requestAddReviewComment,
          meta: { errorTitle: "Failed to add review Comment" },
        });
      },
      /**
       * Defines the mutation that replaces one authored Comment body.
       *
       * It performs no optimistic write itself. The caller executes it with exact
       * identities and publishes only the validated authoritative update.
       */
      edit() {
        return mutationOptions({
          mutationKey: ["review", "comment", "edit"] as const,
          mutationFn: requestEditReviewComment,
          meta: { errorTitle: "Failed to edit review Comment" },
        });
      },
      /**
       * Defines the mutation that tombstones one authored Comment.
       *
       * The caller supplies the exact Snapshot-bound Comment and acting Profile;
       * successful data retains the Comment identity as a backend tombstone.
       */
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
