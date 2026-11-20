import {
  infiniteQueryOptions,
  mutationOptions,
  queryOptions,
  type InfiniteData,
  type QueryClient,
} from "@tanstack/solid-query";
import { z } from "zod";
import { assert, expect } from "../utils";

export const DiffEngineSchema = z.enum([
  "dirdiff",
  "git",
  "difftastic",
  "gumtree",
  "diffsitter",
  "tokendiff",
]);

export type DiffEngine = z.infer<typeof DiffEngineSchema>;

export const GitAlgorithmSchema = z.enum([
  "myers",
  "minimal",
  "patience",
  "histogram",
]);

export const GitColorMovedSchema = z.enum([
  "no",
  "plain",
  "blocks",
  "zebra",
  "dimmed-zebra",
  "default",
]);

// NOTE: technically git allows you to provide them as comma-separated options.
//
// But, it will only pick one in the end, and for `allow-indentation-change`
// will error if you combine it with anything, and `no` will just reset all
// others.
export const GitColorMovedWsSchema = z.enum([
  "no",
  "ignore-space-at-eol",
  "ignore-space-change",
  "ignore-all-space",
  "allow-indentation-change",
]);

export const GitWordBoundaryModeSchema = z.enum([
  "default",
  "language",
  "custom",
]);

export const GumTreeMatcherSchema = z.enum([
  "gumtree-simple",
  "gumtree-classic",
  "gumtree-hybrid",
  "gumtree-simple-stable",
  "gumtree-simple-auto",
]);

export const EngineConfigSchema = z.discriminatedUnion("engine", [
  z.strictObject({
    engine: z.literal("git"),
    algorithm: GitAlgorithmSchema,
    ignore_space_change: z.boolean(),
    word_diff: z.boolean(),
    // The pattern travels only under `custom`, so the other modes share one
    // query identity regardless of the stored regex text.
    word_boundary: z.discriminatedUnion("mode", [
      z.strictObject({ mode: z.literal("default") }),
      z.strictObject({ mode: z.literal("language") }),
      z.strictObject({
        mode: z.literal("custom"),
        regex: z.string().min(1).nullable(),
      }),
    ]),
    color_moved: GitColorMovedSchema,
    color_moved_ws: GitColorMovedWsSchema,
  }),
  z.strictObject({
    engine: z.literal("difftastic"),
    ignore_comments: z.boolean(),
  }),
  z.strictObject({
    engine: z.literal("gumtree"),
    matcher: GumTreeMatcherSchema,
  }),
  z.strictObject({
    engine: z.literal("tokendiff"),
  }),
  z.strictObject({
    engine: z.literal("dirdiff"),
  }),
  z.strictObject({
    engine: z.literal("diffsitter"),
  }),
]);

export type EngineConfig = z.infer<typeof EngineConfigSchema>;

export function isHeavyEngine(engine: EngineConfig): boolean {
  switch (engine.engine) {
    case "dirdiff":
    case "tokendiff":
    case "git":
      return false;
    case "difftastic":
    case "gumtree":
    case "diffsitter":
      return true;
  }
}

export type PresetType = string;

export type ProjectId = number;

const RepoMarkSchema = z.strictObject({
  id: z.number().int().positive(),
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

const PreferenceValuesSchema = z.strictObject({
  aggressive_folds: z.boolean(),
  git_algorithm: GitAlgorithmSchema,
  git_color_moved: GitColorMovedSchema,
  git_color_moved_ws: GitColorMovedWsSchema,
  git_ignore_space_change: z.boolean(),
  git_word_diff: z.boolean(),
  git_word_boundary: GitWordBoundaryModeSchema,
  git_word_diff_regex: z.string().min(1).nullable(),
  difftastic_ignore_comments: z.boolean(),
  gumtree_matcher: GumTreeMatcherSchema,
});

const PreferencesSchema = PreferenceValuesSchema.extend({
  user_profile_id: z.number().int().positive().nullable(),
});

export type Preferences = z.infer<typeof PreferencesSchema>;

const PREFERENCES_STORAGE_KEY = "dirdiff:preferences";

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

export type BranchSelection = z.infer<typeof BranchSelectionSchema>;

const RemoteBranchRefSchema = z.strictObject({
  structured: z.strictObject({
    remote: z.string(),
    branch: z.string(),
  }),
  gitref: z.string(),
});

const BuiltinRefSchema = z.enum(["HEAD", "index", "worktree"]);

export type BuiltinRef = z.infer<typeof BuiltinRefSchema>;

const RefChoicesSchema = z.strictObject({
  builtins: z.array(BuiltinRefSchema),
  local_branches: z.array(z.string()),
  remotes: z.array(z.string()),
  remote_branches: z.array(RemoteBranchRefSchema),
});

export type RefChoices = z.infer<typeof RefChoicesSchema>;

const RepoRefsSchema = z.strictObject({
  ref_choices: RefChoicesSchema,
});

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

export type RepoDefaults = z.infer<typeof RepoDefaultsSchema>;

const RepoMainBranchSchema = z.strictObject({
  project_id: z.number().int().positive(),
  selection: BranchSelectionSchema,
});

export type RepoMainBranch = z.infer<typeof RepoMainBranchSchema>;

export const PreparedPullRequestSchema = z.strictObject({
  project_id: z.number().int().positive(),
  pull_request_url: z.string().min(1),
  left_commit: z.string().min(1),
  right_commit: z.string().min(1),
  base_branch: z.string().trim().min(1),
  head_branch: z.string().trim().min(1),
});

export type PreparedPullRequest = z.infer<typeof PreparedPullRequestSchema>;

const PresetGroupSchema = z.strictObject({
  id: z.string().min(1),
  display_name: z.string().min(1),
});

export type PresetGroup = z.infer<typeof PresetGroupSchema>;

const PresetCatalogSchema = z.strictObject({
  id: z.string().min(1),
  name: z.string().min(1),
  default_preset: z.string().min(1),
  groups: z.array(PresetGroupSchema),
});

export type PresetCatalog = z.infer<typeof PresetCatalogSchema>;

const PresetCatalogsSchema = z.array(PresetCatalogSchema);

export type PresetCatalogs = z.infer<typeof PresetCatalogsSchema>;

type RepoBackedDiffParams = {
  project_id: ProjectId;
};

export type HeadDiffParams = RepoBackedDiffParams & {
  tab: "head";
  show_untracked: true;
};

export type RefsDiffParams = RepoBackedDiffParams & {
  tab: "refs";
  left: string;
  right: string;
  show_untracked: false;
};

export type BranchReviewDiffParams = RepoBackedDiffParams & {
  tab: "branch-review";
  base: BranchSelection;
  review: BranchSelection;
};

export type PullRequestDiffParams = RepoBackedDiffParams & {
  tab: "pull-request";
  url: string;
  left_commit: string;
  right_commit: string;
};

export type PresetDiffParams = {
  catalog: PresetType;
  tab: "preset";
  subset: string;
};

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

export type ManifestSummary = z.infer<typeof ManifestSummarySchema>;

const TrackedFileKindSchema = z.strictObject({
  type: z.literal("tracked"),
  status: z.enum(["modified", "added", "deleted", "renamed", "copied"]),
});

const UntrackedFileKindSchema = z.strictObject({
  type: z.literal("untracked"),
});

const FileKindSchema = z.discriminatedUnion("type", [
  TrackedFileKindSchema,
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

const FilePathSchema = z.string().min(1);

function validateFilePaths(
  paths: {
    left_path: string | null;
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

const ManifestEntrySchema = z
  .strictObject({
    file_kind: FileKindSchema,
    left_path: FilePathSchema.nullable(),
    right_path: FilePathSchema.nullable(),
    lazy: LazyReasonSchema.nullable(),
  })
  .superRefine(validateFilePaths);

export type ManifestEntry = z.infer<typeof ManifestEntrySchema>;

export type ManifestFile = {
  type: "file";
  name: string;
  entry: ManifestEntry;
};

export type ManifestDirectory = {
  type: "directory";
  name: string;
  path: string;
  entries: ManifestNode[];
};

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

export type LazyInfoFile = z.infer<typeof LazyInfoFileSchema>;

const LazyInfoSchema = z.strictObject({
  files: z.array(LazyInfoFileSchema),
});

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

export type TextFileSummary = z.infer<typeof TextFileSummarySchema>;

const RowStatusSchema = z.enum([
  "equal",
  "replace",
  "insert",
  "delete",
  "move",
]);

export type RowStatus = z.infer<typeof RowStatusSchema>;

// Half-open range: lines are one-based, columns are zero-based Unicode
// characters.
const MoveSpanSchema = z
  .strictObject({
    start_line: z.number().int().positive(),
    start_column: z.number().int().nonnegative(),
    end_line: z.number().int().positive(),
    end_column: z.number().int().nonnegative(),
  })
  .refine(
    (span) =>
      span.end_line > span.start_line ||
      (span.end_line === span.start_line &&
        span.end_column > span.start_column),
    "A move must contain a nonempty source range.",
  );

const DecoratedPartSchema = z.strictObject({
  text: z.string(),
  syntax_classes: z.array(z.string()),
  diff_status: z.enum(["unchanged", "replace", "insert", "delete", "move"]),
  move: z
    .strictObject({
      kind: z.literal("paired"),
      left: MoveSpanSchema,
      right: MoveSpanSchema,
    })
    .optional(),
  is_whitespace: z.boolean(),
  is_leading_whitespace: z.boolean(),
});

export type DecoratedPart = z.infer<typeof DecoratedPartSchema>;

const DiffRowSchema = z.strictObject({
  move: z
    .strictObject({
      kind: z.literal("unpaired"),
      group: z.enum(["primary", "alternative"]),
      dimmed: z.boolean(),
    })
    .optional(),
  status: RowStatusSchema,
  left_no: z.number().int().positive().nullable(),
  right_no: z.number().int().positive().nullable(),
  left_text: z.string().nullable(),
  right_text: z.string().nullable(),
  left_parts: z.array(DecoratedPartSchema),
  right_parts: z.array(DecoratedPartSchema),
  hunk_index: z.number().int().nonnegative().nullable(),
});

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

export type FoldHint = z.infer<typeof FoldHintSchema>;

const BayWarningSchema = z.strictObject({
  type: z.string().min(1),
  message: z.string(),
});

export type BayWarning = z.infer<typeof BayWarningSchema>;

const BayStatsSchema = z.strictObject({
  changed_lines: z.number().int(),
  modified_lines: z.number().int(),
  added_lines: z.number().int(),
  removed_lines: z.number().int(),
  moved_lines: z.number().int(),
});

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
  interleave_inline_rows: z.boolean(),
});

export type TextKindPayload = z.infer<typeof TextKindPayloadSchema>;

const MediaRefSchema = z.strictObject({
  media_type: z.string().min(1),
  byte_size: z.number().int().nonnegative(),
  digest: z.string().min(1),
});

export type MediaRef = z.infer<typeof MediaRefSchema>;

const ImageKindPayloadSchema = z.strictObject({
  kind: z.literal("image"),
  left: MediaRefSchema.nullable(),
  right: MediaRefSchema.nullable(),
});

export type ImageKindPayload = z.infer<typeof ImageKindPayloadSchema>;

const BayKindPayloadSchema = z.discriminatedUnion("kind", [
  TextKindPayloadSchema,
  ImageKindPayloadSchema,
]);

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

export type BayPayload = z.infer<typeof BayPayloadSchema>;

export type BayChange = BayPayload["change"];

const FrameSchema = z.strictObject({
  frame_key: z.string().min(1),
  heading: z.string().nullable(),
  bays: z.array(BayPayloadSchema).min(1),
});

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

export type FileDiff = z.infer<typeof ComposedDiffSchema>;

export const ReviewIdSchema = z.string().regex(/^[0-9a-f]{32}$/u);

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

export type ReviewFilePair = z.infer<typeof ReviewFilePairSchema>;

// One-based inclusive line range.
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

export type ReviewLineRange = z.infer<typeof ReviewLineRangeSchema>;

export const FLATFILE_BAY_KEY = "flatfile";

export const IMAGE_BAY_KEY = "image";

export const IMAGE_FACTS_BAY_KEY = "image-facts";

export const BLOB_BAY_KEY = "blob";

export function fileMediaUrl(
  snapshotId: string,
  file: ReviewFilePair,
  bayKey: string,
  side: "left" | "right",
): string {
  const search = snapshotSearchParams(snapshotId);
  search.set("bay_key", bayKey);
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
export const ReviewTargetSchema = z.discriminatedUnion("kind", [
  TextReviewTargetSchema,
  z.strictObject({
    kind: z.literal("review"),
  }),
]);

export type ReviewTarget = z.infer<typeof ReviewTargetSchema>;

const ReviewAuthorSchema = z
  .strictObject({
    profile_id: z.number().int().positive(),
    display_name: z.string().min(1),
    is_agent: z.boolean(),
    model: z.string().nullable(),
    harness: z.string().nullable(),
  })
  .superRefine((author, context) => {
    if (
      !author.is_agent &&
      (author.model !== null || author.harness !== null)
    ) {
      context.addIssue({
        code: "custom",
        message: "A human review author has no model or harness.",
      });
    }
  });

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
  z.strictObject({
    kind: z.literal("region-lost"),
  }),
  z.strictObject({
    kind: z.literal("bay-lost"),
    bay: ReviewTextBaySchema,
  }),
  z.strictObject({
    kind: z.literal("side-lost"),
  }),
  z.strictObject({
    kind: z.literal("file-absent"),
  }),
  z.strictObject({
    kind: z.literal("file-unreadable"),
  }),
  z.strictObject({
    kind: z.literal("whole-file"),
  }),
  z.strictObject({
    kind: z.literal("review-scope"),
  }),
]);

const ThreadLocationSourceSchema = z.discriminatedUnion("kind", [
  z.strictObject({
    kind: z.literal("displayed-snapshot"),
  }),
  z.strictObject({
    kind: z.literal("origin-snapshot"),
    file_state: z.enum(["unchanged", "changed", "missing", "unreadable"]),
  }),
]);

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

export type ReviewExcerpt = z.infer<typeof ReviewExcerptSchema>;

const TextReviewOriginSchema = TextReviewTargetSchema.extend({
  excerpt: ReviewExcerptSchema,
});
const FileStartReviewOriginSchema = z.strictObject({
  kind: z.literal("file-start"),
  file: ReviewFilePairSchema,
  side: z.enum(["left", "right"]),
});
const ReviewScopeOriginSchema = z.strictObject({
  kind: z.literal("review"),
});
const ReviewOriginSchema = z.discriminatedUnion("kind", [
  TextReviewOriginSchema,
  FileStartReviewOriginSchema,
  ReviewScopeOriginSchema,
]);

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
  location_source: ThreadLocationSourceSchema,
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

export type ReviewThread = z.infer<typeof ReviewThreadSchema>;

export function threadOutdated(thread: ReviewThread): boolean {
  if (thread.location_source.kind === "origin-snapshot") {
    return thread.location_source.file_state !== "unchanged";
  }
  const kind = thread.placement.kind;
  return (
    kind !== "region-kept" && kind !== "whole-file" && kind !== "review-scope"
  );
}

export type ThreadCodePoint = {
  file: ReviewFilePair;
  bay: ReviewTextBay;
  side: "left" | "right";
  line: number;
};

export function threadPlacementCodePoint(
  thread: ReviewThread,
): ThreadCodePoint | null {
  const origin = thread.origin_target;
  if (origin.kind === "file-start" || origin.kind === "review") {
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

export type ReviewThreadUpdate = z.infer<typeof ReviewThreadUpdateSchema>;

export type AcceptedReviewActivity =
  | {
      kind: "created";
      thread: ReviewThread;
    }
  | {
      kind: "updated";
      update: ReviewThreadUpdate;
    };

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
      if (
        thread.location_source.kind === "displayed-snapshot" &&
        thread.snapshot_id !== page.snapshot_id
      ) {
        context.addIssue({
          code: "custom",
          message: "Displayed placement names another Snapshot.",
          path: ["threads", index, "snapshot_id"],
        });
      }
      if (
        thread.location_source.kind === "origin-snapshot" &&
        thread.snapshot_id === page.snapshot_id
      ) {
        context.addIssue({
          code: "custom",
          message: "Origin placement repeats the displayed Snapshot.",
          path: ["threads", index, "snapshot_id"],
        });
      }
      if (threadIds.has(thread.thread_id)) {
        context.addIssue({
          code: "custom",
          message: "Review Room Thread identities must be unique.",
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

export type ReviewThreadPage = z.infer<typeof ReviewThreadPageSchema>;

export type ReviewThreadPageParameter = {
  page: number;
  throughActivityId: number | null;
};

export type ReviewThreadPages = InfiniteData<
  ReviewThreadPage,
  ReviewThreadPageParameter
>;

const NO_REVIEW_THREADS: readonly ReviewThread[] = [];

export function reviewThreadList(
  data: ReviewThreadPages | undefined,
  acceptedActivities: readonly AcceptedReviewActivity[],
): readonly ReviewThread[] {
  if (data === undefined) {
    assert(
      acceptedActivities.length === 0,
      "Accepted review activity requires its originating page boundary.",
    );
    return NO_REVIEW_THREADS;
  }
  const threads = data.pages.flatMap((page) => page.threads);
  const threadIndexes = new Map(
    threads.map((thread, index) => [thread.thread_id, index]),
  );
  for (const activity of acceptedActivities) {
    if (activity.kind === "created") {
      const existingIndex = threadIndexes.get(activity.thread.thread_id);
      if (existingIndex === undefined) {
        threadIndexes.set(activity.thread.thread_id, threads.length);
        threads.push(activity.thread);
      } else {
        const existing = expect(
          threads[existingIndex],
          "Loaded review Thread disappeared during activity presentation.",
        );
        if (
          activity.thread.discussion_revision > existing.discussion_revision
        ) {
          threads[existingIndex] = activity.thread;
        }
      }
      continue;
    }
    const update = activity.update;
    const threadIndex = expect(
      threadIndexes.get(update.thread_id),
      "Accepted review update requires its visible Thread.",
    );
    const existing = expect(
      threads[threadIndex],
      "Updated review Thread disappeared during activity presentation.",
    );
    if (update.discussion_revision <= existing.discussion_revision) {
      continue;
    }
    assert(
      update.discussion_revision === existing.discussion_revision + 1,
      "Accepted review updates must remain contiguous.",
    );
    let comments = existing.comments;
    if (update.comment !== null) {
      if (update.comment.sequence === comments.length) {
        comments = [...comments, update.comment];
      } else {
        const matches = comments.filter(
          (comment) => comment.comment_id === update.comment?.comment_id,
        );
        assert(
          matches.length === 1,
          "Accepted review Comment update requires one visible Comment.",
        );
        comments = comments.map((comment) =>
          comment.comment_id === update.comment?.comment_id
            ? update.comment
            : comment,
        );
      }
    }
    threads[threadIndex] = {
      ...existing,
      state: update.state,
      attention: update.attention,
      discussion_revision: update.discussion_revision,
      comments,
    };
  }
  return threads;
}

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

export type CreateReviewThreadRequest = z.infer<
  typeof CreateReviewThreadRequestSchema
>;
export type AddReviewCommentRequest = z.infer<
  typeof AddReviewCommentRequestSchema
>;
export type EditReviewCommentRequest = z.infer<
  typeof EditReviewCommentRequestSchema
>;
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

export type ReviewErrorCode = z.infer<typeof ReviewErrorCodeSchema>;

const HttpExceptionResponseSchema = z.strictObject({
  detail: z.string(),
});

// Transport deadlines; SLOW_DIFF is for heavy engines' file renders,
// PULL_REQUEST
// covers forge network and Git work during preparation.
const REQUEST_TIMEOUT_MS = 8_000;
const SLOW_DIFF_TIMEOUT_MS = 20_000;
const PULL_REQUEST_TIMEOUT_MS = 60_000;

export type FileDiffTimeout = "bounded" | "unbounded";

type HttpRequest = {
  input: string;
  init: RequestInit;
  abortSignal: AbortSignal | null;
  timeoutMs: number | null;
};

type MultiAbortSignal = {
  abortSignal: AbortSignal;
  dispose(): void;
  timedOut(): boolean;
};

type RequestErrorReason = "timeout" | "other";

class RequestError extends Error {
  readonly error_reason: RequestErrorReason;

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

export class ReviewRequestError extends RequestError {
  readonly code: ReviewErrorCode;

  constructor(code: ReviewErrorCode, message: string) {
    super("other", message, null);
    this.name = "ReviewRequestError";
    this.code = code;
  }
}

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
    dispose() {
      if (timeoutId !== null) {
        window.clearTimeout(timeoutId);
      }
      if (callerAbortSignal !== null) {
        callerAbortSignal.removeEventListener("abort", abortFromCallerSignal);
      }
    },
    timedOut() {
      return didTimeout;
    },
  };
}

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
    // Error copy names only the endpoint and never exposes request query
    // values.
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

class RepositoryDefaultsHeuristicError extends Error {
  constructor() {
    super("The backend could not infer a base branch for this repository.");
    this.name = "RepositoryDefaultsHeuristicError";
  }
}

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

async function requestPreferences(
  profileId: number | null,
  abortSignal: AbortSignal,
): Promise<Preferences> {
  if (profileId === null) {
    const raw = window.localStorage.getItem(PREFERENCES_STORAGE_KEY);
    const values =
      raw === null
        ? {
            aggressive_folds: true,
            git_algorithm: "myers" as const,
            git_color_moved: "no" as const,
            git_color_moved_ws: "no" as const,
            git_ignore_space_change: false,
            git_word_diff: false,
            git_word_boundary: "default" as const,
            git_word_diff_regex: null,
            difftastic_ignore_comments: false,
            gumtree_matcher: "gumtree-simple" as const,
          }
        : PreferenceValuesSchema.extend({
            // Since localStorage might be permanently outdated, and
            // would require a manual migration otherwise, we supply
            // default values for fields that are not present.
            //
            // The alternative would be to pretty much erase it, which
            // is not the right trade-off here.
            git_ignore_space_change: z.boolean().default(false),
            git_word_diff: z.boolean().default(false),
            git_word_boundary: GitWordBoundaryModeSchema.default("default"),
            git_word_diff_regex: z.string().min(1).nullable().default(null),
            git_color_moved: GitColorMovedSchema.default("no"),
            git_color_moved_ws: GitColorMovedWsSchema.default("no"),
            difftastic_ignore_comments: z.boolean().default(false),
            gumtree_matcher: GumTreeMatcherSchema.default("gumtree-simple"),
          }).parse(JSON.parse(raw));
    return { ...values, user_profile_id: null };
  }
  const preferences = await requestJson(
    {
      input: `/api/user-profile/${profileId}/preferences`,
      init: {},
      abortSignal,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
  assert(
    preferences.user_profile_id === profileId,
    "Preferences must belong to the addressed Profile.",
  );
  // A disposed Profile query must not replace the next Profile's local cache.
  abortSignal.throwIfAborted();
  window.localStorage.setItem(
    PREFERENCES_STORAGE_KEY,
    JSON.stringify({
      aggressive_folds: preferences.aggressive_folds,
      git_algorithm: preferences.git_algorithm,
      git_color_moved: preferences.git_color_moved,
      git_color_moved_ws: preferences.git_color_moved_ws,
      git_ignore_space_change: preferences.git_ignore_space_change,
      git_word_diff: preferences.git_word_diff,
      git_word_boundary: preferences.git_word_boundary,
      git_word_diff_regex: preferences.git_word_diff_regex,
      difftastic_ignore_comments: preferences.difftastic_ignore_comments,
      gumtree_matcher: preferences.gumtree_matcher,
    }),
  );
  return preferences;
}

async function requestSavePreferences(input: {
  profileId: number | null;
  aggressiveFolds: boolean;
  gitAlgorithm: Preferences["git_algorithm"];
  gitColorMoved: Preferences["git_color_moved"];
  gitColorMovedWs: Preferences["git_color_moved_ws"];
  gitIgnoreSpaceChange: boolean;
  gitWordDiff: boolean;
  gitWordBoundary: Preferences["git_word_boundary"];
  gitWordDiffRegex: string | null;
  difftasticIgnoreComments: boolean;
  gumtreeMatcher: Preferences["gumtree_matcher"];
}): Promise<Preferences> {
  const values = PreferenceValuesSchema.parse({
    aggressive_folds: input.aggressiveFolds,
    git_algorithm: input.gitAlgorithm,
    git_color_moved: input.gitColorMoved,
    git_color_moved_ws: input.gitColorMovedWs,
    git_ignore_space_change: input.gitIgnoreSpaceChange,
    git_word_diff: input.gitWordDiff,
    git_word_boundary: input.gitWordBoundary,
    git_word_diff_regex: input.gitWordDiffRegex,
    difftastic_ignore_comments: input.difftasticIgnoreComments,
    gumtree_matcher: input.gumtreeMatcher,
  });
  if (input.profileId === null) {
    return { ...values, user_profile_id: null };
  }
  const saved = await requestJson(
    {
      input: `/api/user-profile/${input.profileId}/preferences`,
      init: {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(values),
      },
      abortSignal: null,
      timeoutMs: REQUEST_TIMEOUT_MS,
    },
    PreferencesSchema,
  );
  assert(
    saved.user_profile_id === input.profileId,
    "Saved preferences must belong to the addressed Profile.",
  );
  return saved;
}

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

function snapshotSearchParams(snapshotId: string): URLSearchParams {
  return new URLSearchParams({ snapshot_id: snapshotId });
}

function requestManifest(
  params: DiffParams,
  abortSignal: AbortSignal,
): Promise<Manifest> {
  const search = new URLSearchParams({ selection: JSON.stringify(params) });
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

function requestFileDiff(
  engine: EngineConfig,
  snapshotId: string,
  entry: ManifestEntry,
  abortSignal: AbortSignal,
  timeout: FileDiffTimeout,
): Promise<FileDiff> {
  const search = snapshotSearchParams(snapshotId);
  search.set("engine", JSON.stringify(EngineConfigSchema.parse(engine)));
  if (entry.left_path !== null) {
    search.set("left_path", entry.left_path);
  }
  if (entry.right_path !== null) {
    search.set("right_path", entry.right_path);
  }
  // The timeout policy affects execution only; it must not enter the query key.
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

type CreateReviewThreadInput = {
  snapshotId: ReviewId;
  body: CreateReviewThreadRequest;
};

function assertReviewThreadIdentity(
  thread: {
    snapshot_id: ReviewId;
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

type AddReviewCommentInput = {
  snapshotId: ReviewId;
  threadId: ReviewId;
  body: AddReviewCommentRequest;
};

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

export const api = {
  changeSet: {
    key: ["change-set"] as const,

    manifest(params: DiffParams) {
      return queryOptions({
        queryKey: ["change-set", "manifest", params] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestManifest(params, abortSignal),
        meta: { errorTitle: "Failed to load ChangeSet manifest" },
        ...snapshotQuery,
      });
    },

    lazyInfo(snapshotId: string) {
      return queryOptions({
        queryKey: ["change-set", "lazy-info", snapshotId] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestLazyInfo(snapshotId, abortSignal),
        meta: { errorTitle: "Failed to load delayed-file information" },
        ...snapshotQuery,
      });
    },

    file(
      engine: EngineConfig,
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
    snapshot(snapshotId: ReviewId) {
      const initialPageParam: ReviewThreadPageParameter = {
        page: 1,
        throughActivityId: null,
      };
      return infiniteQueryOptions<
        ReviewThreadPage,
        Error,
        ReviewThreadPages,
        readonly ["review", "pages", ReviewId],
        ReviewThreadPageParameter
      >({
        queryKey: ["review", "pages", snapshotId] as const,
        initialPageParam,
        queryFn: async ({ pageParam, signal: abortSignal }) => {
          const search = snapshotSearchParams(snapshotId);
          search.set("page", String(pageParam.page));
          search.set("limit", "20");
          if (pageParam.throughActivityId !== null) {
            search.set(
              "through_activity_id",
              String(pageParam.throughActivityId),
            );
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
            response.snapshot_id === snapshotId &&
              response.page === pageParam.page,
            "Review response returned another page identity.",
          );
          if (pageParam.throughActivityId !== null) {
            assert(
              response.through_activity_id === pageParam.throughActivityId,
              "Review transport pages use different activity pivots.",
            );
          }
          return response;
        },
        getNextPageParam: (lastPage, pages) => {
          const firstPage = expect(
            pages[0],
            "Review pagination requires its first page.",
          );
          assert(
            pages.every(
              (page, index) =>
                page.page === index + 1 &&
                page.snapshot_id === firstPage.snapshot_id &&
                page.through_activity_id === firstPage.through_activity_id &&
                page.total_threads === firstPage.total_threads,
            ),
            "Review transport pages disagree about their stable boundary.",
          );
          const threadIds = new Set<ReviewId>();
          for (const page of pages) {
            for (const thread of page.threads) {
              assert(
                !threadIds.has(thread.thread_id),
                "Review transport pages contain a duplicate Thread.",
              );
              threadIds.add(thread.thread_id);
            }
          }
          if (!lastPage.has_more) {
            assert(
              threadIds.size === lastPage.total_threads,
              "Review transport pages do not contain every Thread.",
            );
            return undefined;
          }
          return {
            page: lastPage.page + 1,
            throughActivityId: firstPage.through_activity_id,
          } satisfies ReviewThreadPageParameter;
        },
        meta: { errorTitle: "Failed to load review Threads" },
        ...snapshotQuery,
      });
    },

    thread: {
      create() {
        return mutationOptions({
          mutationKey: ["review", "thread", "create"] as const,
          mutationFn: requestCreateReviewThread,
          meta: { errorTitle: "Failed to create review Thread" },
        });
      },
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
      add() {
        return mutationOptions({
          mutationKey: ["review", "comment", "add"] as const,
          mutationFn: requestAddReviewComment,
          meta: { errorTitle: "Failed to add review Comment" },
        });
      },
      edit() {
        return mutationOptions({
          mutationKey: ["review", "comment", "edit"] as const,
          mutationFn: requestEditReviewComment,
          meta: { errorTitle: "Failed to edit review Comment" },
        });
      },
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
    list() {
      return queryOptions({
        queryKey: ["repos"] as const,
        queryFn: ({ signal: abortSignal }) => requestRepos(abortSignal),
        staleTime: 5_000,
        meta: { errorTitle: "Failed to load repositories" },
      });
    },

    refs(projectId: ProjectId) {
      return queryOptions({
        queryKey: ["repos", projectId, "refs"] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestRepoRefs(projectId, abortSignal),
        staleTime: 30_000,
        meta: { errorTitle: "Failed to load refs" },
      });
    },

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

    remove() {
      return mutationOptions({
        mutationKey: ["repos", "remove"] as const,
        mutationFn: requestRemoveRepo,
        meta: { errorTitle: "Failed to remove repository" },
      });
    },

    saveMainBranch() {
      return mutationOptions({
        mutationKey: ["repos", "save-main-branch"] as const,
        mutationFn: requestSaveMainBranch,
        meta: { errorTitle: "Failed to save main branch" },
      });
    },
  },

  presets: {
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
    preferences(profileId: number | null) {
      return queryOptions({
        queryKey: ["profile", profileId, "preferences"] as const,
        queryFn: ({ signal: abortSignal }) =>
          requestPreferences(profileId, abortSignal),
        staleTime: Infinity,
        gcTime: 0,
        meta: { errorTitle: "Failed to load preferences" },
      });
    },

    login() {
      return mutationOptions({
        mutationKey: ["profile", "login"] as const,
        mutationFn: requestLoginProfile,
        meta: { errorTitle: "Failed to log in" },
      });
    },

    register() {
      return mutationOptions({
        mutationKey: ["profile", "register"] as const,
        mutationFn: requestRegisterProfile,
        meta: { errorTitle: "Failed to register profile" },
      });
    },

    rename() {
      return mutationOptions({
        mutationKey: ["profile", "rename"] as const,
        mutationFn: requestRenameProfile,
        meta: { errorTitle: "Failed to rename profile" },
      });
    },

    savePreferences(queryClient: QueryClient) {
      return mutationOptions({
        mutationKey: ["profile", "save-preferences"] as const,
        mutationFn: requestSavePreferences,
        onSuccess(saved: Preferences) {
          const queryKey = api.profile.preferences(
            saved.user_profile_id,
          ).queryKey;
          // A completed write to a departed Profile still belongs to that
          // Profile.
          // It must not overwrite the current user's local cache.
          if (
            queryClient.getQueryCache().find({ queryKey })?.isActive() === true
          ) {
            window.localStorage.setItem(
              PREFERENCES_STORAGE_KEY,
              JSON.stringify({
                aggressive_folds: saved.aggressive_folds,
                git_algorithm: saved.git_algorithm,
                git_color_moved: saved.git_color_moved,
                git_color_moved_ws: saved.git_color_moved_ws,
                git_ignore_space_change: saved.git_ignore_space_change,
                git_word_diff: saved.git_word_diff,
                git_word_boundary: saved.git_word_boundary,
                git_word_diff_regex: saved.git_word_diff_regex,
                difftastic_ignore_comments: saved.difftastic_ignore_comments,
                gumtree_matcher: saved.gumtree_matcher,
              }),
            );
            queryClient.setQueryData(queryKey, saved);
          }
        },
        meta: { errorTitle: "Failed to save preferences" },
      });
    },
  },

  pullRequest: {
    prepare() {
      return mutationOptions({
        mutationKey: ["pull-request", "prepare"] as const,
        mutationFn: requestPreparePullRequest,
        meta: { errorTitle: "Failed to prepare pull request" },
      });
    },
  },
} as const;
