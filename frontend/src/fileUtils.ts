import type {
  DiffEngine,
  DiffMode,
  DiffParams,
  DiffRow,
  FileEntry,
  FileKind,
  FoldHint,
  LazyReason,
  ManifestEntry,
  NotebookSummary,
  PresetType,
  Summary,
} from "./api";
import type { DiffViewMode } from "./DiffGrid";

export type LoadState = "idle" | "loading" | "done" | "error";
export type BranchSource = "local" | "remote";
export type ControlsState = {
  mode: DiffMode;
  left: string;
  right: string;
  presetType: PresetType;
  preset: string;
  baseSource: BranchSource;
  baseRemote: string;
  baseBranch: string;
  branchSource: BranchSource;
  branchRemote: string;
  reviewBranch: string;
};

export type LoadedDiff = {
  params: DiffParams;
  // Rendered file cards are built from FileEntry values only.
  files: RenderedFileEntry[];
  // ManifestEntry values are only handles for fetching enough file data later.
  lazyFiles: ManifestEntry[];
  fileOrder: Record<string, number>;
  summary: Summary;
};
export type RenderedFileEntry = FileEntry & {
  renderedKey: string;
  sourceParams: DiffParams;
  sourceParamsIdentity: string;
  sourceEngine: DiffEngine;
  sourceLoadId: number;
  originalLazyReason: LazyReason | null;
};

export type AutocompleteGroup = [string, string[]];
export type LinePin = {
  file: string;
  side: "left" | "right";
  line: string;
};
export type FileGroup = {
  label: string;
  files: RenderedFileEntry[];
};

export const modeLabels: Record<DiffMode, string> = {
  files: "Diff files",
  staged: "Diff staged",
  head: "Diff against HEAD",
  refs: "Compare refs",
  "branch-review": "Branch review",
  preset: "Preset",
};
export const presetTypeLabels: Record<PresetType, string> = {
  diff: "Diff Presets",
  fold: "Fold Presets",
  gumtree: "GumTree Presets",
};
export const presetTypes: PresetType[] = ["diff", "fold", "gumtree"];
export const topLevelModes: DiffMode[] = [
  "head",
  "refs",
  "branch-review",
  "preset",
];
export const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
  difftastic: "Difftastic",
  gumtree: "GumTree",
};
export const diffViewLabels: Record<DiffViewMode, string> = {
  split: "Split",
  inline: "Inline",
};

export const refSectionLabels: Record<string, string> = {
  builtins: "Built-ins",
  locals: "Local branches",
  remotes: "Remote refs",
  remote_names: "Remotes",
  remote_branches: "Remote branches",
};
export const emptySummary: Summary = {
  changed_files: 0,
  added_files: 0,
  removed_files: 0,
  updated_files: 0,
  changed_lines: 0,
  modified_lines: 0,
  added_lines: 0,
  removed_lines: 0,
  moved_lines: 0,
  skipped_files: 0,
};

function nullableStringValue(
  value: string | null | undefined,
  fallback: string,
): string {
  if (value !== null && value !== undefined && value.length > 0) {
    return value;
  }
  return fallback;
}

function entryDirectoryPath(entry: FileEntry): string {
  const path = fileTreePath(entry);
  const lastSlash = path.lastIndexOf("/");
  return lastSlash >= 0 ? path.slice(0, lastSlash) : "";
}

export function fileDisplayName(entry: FileEntry): string {
  if (entry.display_name !== undefined && entry.display_name.length > 0) {
    return entry.display_name;
  }
  throw new Error("File entry is missing display_name.");
}

function fileTreePath(entry: FileEntry): string {
  if (entry.right_path !== null && entry.right_path.length > 0) {
    return entry.right_path;
  }
  if (entry.left_path !== null && entry.left_path.length > 0) {
    return entry.left_path;
  }
  throw new Error("File entry is missing paths.");
}

export function fileRows(entry: FileEntry): DiffRow[] {
  if (entry.rows === undefined) {
    throw new Error(`${fileDisplayName(entry)} is missing diff rows.`);
  }
  return entry.rows;
}

export function fileEntryIsHydrated(entry: FileEntry): boolean {
  if (entry.render_kind === "notebook") {
    return true;
  }
  return entry.rows !== undefined;
}

export function addHydratedNotebookSummary(
  current: Summary,
  entry: FileEntry,
): Summary {
  const entrySummary = entry.summary;
  if (entrySummary === undefined || !("changed_cells" in entrySummary)) {
    return current;
  }
  const notebookSummary = entrySummary as NotebookSummary;
  return {
    ...current,
    changed_cells:
      requiredNotebookSummaryCount(current, "changed_cells") +
      notebookSummary.changed_cells,
    added_cells:
      requiredNotebookSummaryCount(current, "added_cells") +
      notebookSummary.added_cells,
    modified_cells:
      requiredNotebookSummaryCount(current, "modified_cells") +
      notebookSummary.modified_cells,
    removed_cells:
      requiredNotebookSummaryCount(current, "removed_cells") +
      notebookSummary.removed_cells,
  };
}

function requiredNotebookSummaryCount(
  summary: Summary,
  key: "changed_cells" | "added_cells" | "modified_cells" | "removed_cells",
): number {
  const value = summary[key];
  if (typeof value !== "number") {
    throw new Error(`Summary is missing ${key}.`);
  }
  return value;
}

function fileKindKey(fileKind: FileKind): string {
  if (fileKind.type === "untracked") {
    return "untracked";
  }
  return `git:${fileKind.status}`;
}

export function entryDirectoryLabel(entry: FileEntry): string {
  const directory = entryDirectoryPath(entry);
  if (directory.length > 0) {
    return directory;
  }
  return "root files";
}

export function fileKey(entry: FileEntry): string {
  const leftPath = nullableStringValue(entry.left_path, "");
  const rightPath = nullableStringValue(entry.right_path, "");
  const displayName =
    leftPath.length > 0 || rightPath.length > 0
      ? ""
      : nullableStringValue(entry.display_name, "");
  return `${leftPath}\u0000${rightPath}\u0000${displayName}\u0000${fileKindKey(entry.file_kind)}`;
}

export function sortFilesByOrder(
  files: FileEntry[],
  order: Record<string, number>,
): FileEntry[] {
  return [...files].sort(
    (leftFile, rightFile) =>
      fileOrderIndex(order, leftFile) - fileOrderIndex(order, rightFile),
  );
}

function fileOrderIndex(
  order: Record<string, number>,
  file: FileEntry,
): number {
  const index = order[fileKey(file)];
  if (index === undefined) {
    throw new Error(`Missing file order for ${fileKey(file)}.`);
  }
  return index;
}

export function fileElementId(key: string): string {
  return hashedElementId("file", key);
}

export function fileBodyAnchorElementId(key: string): string {
  return hashedElementId("file-body", key);
}

export function directoryElementId(label: string): string {
  return hashedElementId("directory", label);
}

function hashedElementId(prefix: string, value: string): string {
  let hash = 5381;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33) ^ value.charCodeAt(index);
  }
  return `${prefix}-${(hash >>> 0).toString(36)}`;
}

export function fileDiffQueryKey(
  diffParams: DiffParams,
  entry: FileEntry | ManifestEntry,
) {
  let displayName: string | undefined;
  if ("display_name" in entry) {
    displayName = entry.display_name;
  }
  const diffIdentityParts =
    diffParams.mode === "preset"
      ? [diffParams.mode, diffParams.preset_type, diffParams.preset]
      : diffParams.mode === "branch-review"
        ? [diffParams.mode, diffParams.base_branch, diffParams.review_branch]
        : [
            diffParams.mode,
            diffParams.left,
            diffParams.right,
            diffParams.mode === "head",
          ];
  return [
    "file-diff",
    diffParams.repo_id,
    diffParams.engine,
    ...diffIdentityParts,
    entry.left_path,
    entry.right_path,
    displayName,
    fileKindKey(entry.file_kind),
  ] as const;
}

export function groupFilesByLabel(
  files: RenderedFileEntry[],
): Map<string, FileGroup> {
  const groups = new Map<string, RenderedFileEntry[]>();
  for (const file of files) {
    const label = entryDirectoryLabel(file);
    const groupFiles = groups.get(label);
    if (groupFiles !== undefined) {
      groupFiles.push(file);
    } else {
      groups.set(label, [file]);
    }
  }
  return new Map(
    [...groups].map(([label, groupFiles]) => [
      label,
      { label, files: groupFiles },
    ]),
  );
}

export function fileMatchesLinePin(file: FileEntry, pin: LinePin): boolean {
  return fileDisplayName(file) === pin.file;
}
