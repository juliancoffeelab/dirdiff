import type {
  DiffEngine,
  DiffMode,
  DiffParams,
  DiffRow,
  FileEntry,
  FileKind,
  FoldHint,
  LazyReason,
  ManifestTreeEntry,
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
export type FileTreeNode = FileTreeFileNode | FileTreeDirectoryNode;
export type FileTreeFileNode = {
  type: "file";
  name: string;
  file: RenderedFileEntry;
};
export type FileTreeDirectoryNode = {
  type: "directory";
  label: string;
  name: string;
  files: RenderedFileEntry[];
  entries: FileTreeNode[];
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
  added_lines: 0,
  removed_lines: 0,
  skipped_files: 0,
};
export const ROOT_FILES_LABEL = "root files";

function nullableStringValue(
  value: string | null | undefined,
  fallback: string,
): string {
  if (value !== null && value !== undefined && value.length > 0) {
    return value;
  }
  return fallback;
}

export function fileDisplayName(entry: FileEntry): string {
  if (entry.display_name !== undefined && entry.display_name.length > 0) {
    return entry.display_name;
  }
  throw new Error("File entry is missing display_name.");
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

export function fileKey(entry: FileEntry): string {
  const leftPath = nullableStringValue(entry.left_path, "");
  const rightPath = nullableStringValue(entry.right_path, "");
  const displayName =
    leftPath.length > 0 || rightPath.length > 0
      ? ""
      : nullableStringValue(entry.display_name, "");
  return `${leftPath}\u0000${rightPath}\u0000${displayName}\u0000${fileKindKey(entry.file_kind)}`;
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

type FilesByKey = Record<string, RenderedFileEntry | undefined>;

function loadedFilesForNodes(
  entries: ManifestTreeEntry[],
  filesByKey: FilesByKey,
): RenderedFileEntry[] {
  return entries.flatMap((entry) => {
    if (entry.type === "directory") {
      return [];
    }
    const file = filesByKey[fileKey(entry.entry)];
    return file === undefined ? [] : [file];
  });
}

function directoryGroupsFromTree(
  entries: ManifestTreeEntry[],
  filesByKey: FilesByKey,
): FileGroup[] {
  return entries.flatMap((entry) => {
    if (entry.type === "file") {
      return [];
    }
    const directFiles = loadedFilesForNodes(entry.entries, filesByKey);
    const directGroup =
      directFiles.length === 0
        ? []
        : [{ label: entry.path, files: directFiles }];
    return [
      ...directoryGroupsFromTree(entry.entries, filesByKey),
      ...directGroup,
    ];
  });
}

export function groupFilesByManifestTree(
  tree: ManifestTreeEntry[],
  filesByKey: FilesByKey,
): FileGroup[] {
  const rootFiles = loadedFilesForNodes(tree, filesByKey);
  const directoryGroups = directoryGroupsFromTree(tree, filesByKey);
  if (rootFiles.length === 0) {
    return directoryGroups;
  }
  return [...directoryGroups, { label: ROOT_FILES_LABEL, files: rootFiles }];
}

function fileTreeFilesForNodes(nodes: FileTreeNode[]): RenderedFileEntry[] {
  return nodes.flatMap((node) => {
    if (node.type === "file") {
      return [node.file];
    }
    return node.files;
  });
}

function fileTreeNodeFromManifestEntry(
  entry: ManifestTreeEntry,
  filesByKey: FilesByKey,
): FileTreeNode | null {
  if (entry.type === "file") {
    const file = filesByKey[fileKey(entry.entry)];
    return file === undefined ? null : { type: "file", name: entry.name, file };
  }

  const entries = entry.entries.flatMap((child) => {
    const node = fileTreeNodeFromManifestEntry(child, filesByKey);
    return node === null ? [] : [node];
  });
  const files = fileTreeFilesForNodes(entries);
  if (files.length === 0) {
    return null;
  }
  return {
    type: "directory",
    label: entry.path,
    name: entry.name,
    files,
    entries,
  };
}

export function fileTreeFromManifestTree(
  tree: ManifestTreeEntry[],
  filesByKey: FilesByKey,
): FileTreeNode[] {
  return tree.flatMap((entry) => {
    const node = fileTreeNodeFromManifestEntry(entry, filesByKey);
    return node === null ? [] : [node];
  });
}

export function manifestFileEntriesFromTree(
  tree: ManifestTreeEntry[],
): ManifestEntry[] {
  return tree.flatMap((entry) => {
    if (entry.type === "file") {
      return [entry.entry];
    }
    return manifestFileEntriesFromTree(entry.entries);
  });
}

function collectDirectoryLabelsByFileKey(
  entries: ManifestTreeEntry[],
  label: string,
  labels: Record<string, string>,
): void {
  for (const entry of entries) {
    if (entry.type === "file") {
      labels[fileKey(entry.entry)] = label;
      continue;
    }
    collectDirectoryLabelsByFileKey(entry.entries, entry.path, labels);
  }
}

export function manifestDirectoryLabelsByFileKey(
  tree: ManifestTreeEntry[],
): Record<string, string> {
  const labels: Record<string, string> = {};
  collectDirectoryLabelsByFileKey(tree, ROOT_FILES_LABEL, labels);
  return labels;
}

export function fileMatchesLinePin(file: FileEntry, pin: LinePin): boolean {
  return fileDisplayName(file) === pin.file;
}
