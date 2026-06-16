import type {
  DiffEngine,
  DiffMode,
  DiffRequest,
  FileEntry,
  FileKind,
  NotebookSummary,
  RefChoices,
  RepoRefs,
  Summary,
} from "./api";
import type { DiffViewMode } from "./DiffGrid";

export type LoadState = "idle" | "loading" | "done" | "error";
export type BranchSource = "local" | "remote";
export type ControlsState = {
  mode: DiffMode;
  left: string;
  right: string;
  baseSource: BranchSource;
  baseRemote: string;
  baseBranch: string;
  branchSource: BranchSource;
  branchRemote: string;
  reviewBranch: string;
};

export type LoadedDiff = {
  request: DiffRequest;
  files: FileEntry[];
  lazyFiles: FileEntry[];
  fileOrder: Record<string, number>;
  summary: Summary;
};

export type AutocompleteGroup = [string, string[]];
export type LinePin = {
  file: string;
  side: "left" | "right";
  line: string;
};
export type FileGroup = {
  label: string;
  files: FileEntry[];
};

const modeSides: Record<
  Exclude<DiffMode, "refs" | "branch-review" | "preset">,
  [string, string]
> = {
  files: ["index", "worktree"],
  staged: ["head", "index"],
  "against-head": ["head", "worktree"],
};

export const modeLabels: Record<DiffMode, string> = {
  files: "Diff files",
  staged: "Diff staged",
  "against-head": "Diff against HEAD",
  refs: "Compare refs",
  "branch-review": "Branch review",
  preset: "Preset",
};
export const topLevelModes: DiffMode[] = [
  "against-head",
  "refs",
  "branch-review",
  "preset",
];
export const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
  difftastic: "Difftastic",
};
export const diffViewLabels: Record<DiffViewMode, string> = {
  split: "Split",
  inline: "Inline",
};

const builtinSides = new Set(["head", "index", "worktree"]);
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
  skipped_files: 0,
};

function inferMode(
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
): DiffMode {
  if (baseBranch || reviewBranch) {
    return "branch-review";
  }
  return left === "head" && right === "worktree" ? "against-head" : "refs";
}

function normalizeTopLevelMode(
  mode: DiffMode | null,
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
): DiffMode {
  if (
    mode === "refs" ||
    mode === "branch-review" ||
    mode === "against-head" ||
    mode === "preset"
  ) {
    return mode;
  }
  if (mode === "files" || mode === "staged") {
    return "against-head";
  }
  return inferMode(left, right, baseBranch, reviewBranch);
}

export function initialControls(repoRefs: RepoRefs): ControlsState {
  const search = new URLSearchParams(window.location.search);
  const remoteNames = repoRefs.ref_choices.remote_names;
  const left = searchValue(search, "left", "head");
  const right = searchValue(search, "right", "worktree");
  const baseBranchRef = searchValue(
    search,
    "base_branch",
    nullableStringValue(repoRefs.default_base_branch, ""),
  );
  const reviewBranchRef = searchValue(
    search,
    "review_branch",
    nullableStringValue(repoRefs.preferred_review_branch, ""),
  );
  const baseBranchParts = splitRemoteQualifiedRef(baseBranchRef, remoteNames);
  const reviewBranchParts = splitRemoteQualifiedRef(
    reviewBranchRef,
    remoteNames,
  );
  const requestedMode = search.get("mode") as DiffMode | null;
  const mode =
    requestedMode === null
      ? "against-head"
      : normalizeTopLevelMode(
          requestedMode,
          left,
          right,
          baseBranchParts.value,
          reviewBranchParts.value,
        );

  if (mode in modeSides) {
    const [modeLeft, modeRight] = modeSides[mode as keyof typeof modeSides];
    return {
      mode,
      left: modeLeft,
      right: modeRight,
      baseSource: baseBranchParts.remote ? "remote" : "local",
      baseRemote: baseBranchParts.remote,
      baseBranch: baseBranchParts.value,
      branchSource: reviewBranchParts.remote ? "remote" : "local",
      branchRemote: reviewBranchParts.remote,
      reviewBranch: reviewBranchParts.value,
    };
  }

  return {
    mode,
    left,
    right,
    baseSource: baseBranchParts.remote ? "remote" : "local",
    baseRemote: baseBranchParts.remote,
    baseBranch: baseBranchParts.value,
    branchSource: reviewBranchParts.remote ? "remote" : "local",
    branchRemote: reviewBranchParts.remote,
    reviewBranch: reviewBranchParts.value,
  };
}

function searchValue(
  search: URLSearchParams,
  name: string,
  fallback: string,
): string {
  const value = search.get(name);
  if (value !== null && value.length > 0) {
    return value;
  }
  return fallback;
}

function nullableStringValue(
  value: string | null | undefined,
  fallback: string,
): string {
  if (value !== null && value !== undefined && value.length > 0) {
    return value;
  }
  return fallback;
}

export function initialEngine(): DiffEngine {
  const engine = new URLSearchParams(window.location.search).get("engine");
  if (engine === "git" || engine === "dirdiff" || engine === "difftastic") {
    return engine;
  }
  return "dirdiff";
}

export function initialDiffViewMode(): DiffViewMode {
  const view = new URLSearchParams(window.location.search).get("view");
  if (view === "split" || view === "inline") {
    return view;
  }
  return "inline";
}

function requestQuery(request: DiffRequest): URLSearchParams {
  const params = new URLSearchParams();
  params.set("repo_id", String(request.repo_id));
  params.set("engine", request.engine);
  params.set("mode", request.mode);
  if (request.left.length > 0) {
    params.set("left", request.left);
  }
  if (request.right.length > 0) {
    params.set("right", request.right);
  }
  if (request.base_branch !== null && request.base_branch.length > 0) {
    params.set("base_branch", request.base_branch);
  }
  if (request.review_branch !== null && request.review_branch.length > 0) {
    params.set("review_branch", request.review_branch);
  }
  if (request.show_untracked) {
    params.set("show_untracked", "true");
  }
  return params;
}

export function appQuery(
  request: DiffRequest,
  viewMode: DiffViewMode,
): URLSearchParams {
  const params = requestQuery(request);
  params.set("view", viewMode);
  return params;
}

export function statusLabel(
  request: DiffRequest,
  leftLabel?: string,
  rightLabel?: string,
): string {
  if (request.mode === "files") {
    return "Unstaged changes in working tree";
  }
  if (request.mode === "staged") {
    return "Staged changes ready to commit";
  }
  if (request.mode === "against-head") {
    return "Working tree vs HEAD";
  }
  if (request.mode === "branch-review") {
    return `${request.review_branch} vs ${request.base_branch}`;
  }
  if (request.mode === "preset") {
    return "Preset diffs";
  }
  return `${nullableStringValue(leftLabel, request.left)} vs ${nullableStringValue(rightLabel, request.right)}`;
}

export function loadedStatusLabel(
  baseStatus: string,
  loadedFiles: number,
  failedDetailFiles: number,
): string {
  const fileWord = loadedFiles === 1 ? "file" : "files";
  const failureText =
    failedDetailFiles > 0 ? `, failed details ${failedDetailFiles}` : "";
  return `${baseStatus} · loaded ${loadedFiles} ${fileWord}${failureText}`;
}

export function expansionValue(
  current: Record<string, boolean>,
  key: string,
  defaultValue: boolean,
): boolean {
  if (Object.hasOwn(current, key)) {
    return current[key];
  }
  return defaultValue;
}

export function nextFileExpansion(
  current: Record<string, boolean>,
  newFile: FileEntry,
  newFileKey: string,
): Record<string, boolean> {
  if (Object.hasOwn(current, newFileKey)) {
    return current;
  }
  return {
    ...current,
    [newFileKey]: newFile.default_expanded ?? false,
  };
}

function entryDirectoryPath(entry: FileEntry): string {
  const path = fileTreePath(entry);
  const lastSlash = path.lastIndexOf("/");
  return lastSlash >= 0 ? path.slice(0, lastSlash) : "";
}

export function fileDisplayName(entry: FileEntry): string {
  return entry.display_name ?? fileTreePath(entry);
}

export function fileBasename(entry: FileEntry): string {
  const path = fileTreePath(entry);
  const basename = path.split("/").at(-1);
  if (basename === undefined || basename.length === 0) {
    throw new Error(`Could not derive file basename from ${path}.`);
  }
  return basename;
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

export type LineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
};

function emptyLineStats(): LineStats {
  return { added: 0, modified: 0, removed: 0 };
}

function addLineStats(left: LineStats, right: LineStats): LineStats {
  return {
    added: addLineStat(left.added, right.added),
    modified: addLineStat(left.modified, right.modified),
    removed: addLineStat(left.removed, right.removed),
  };
}

function addLineStat(left: number | null, right: number | null): number | null {
  if (left === null || right === null) {
    return null;
  }
  return left + right;
}

function unknownLineStats(): LineStats {
  return { added: null, modified: null, removed: null };
}

export function fileLineStats(entry: FileEntry): LineStats {
  if (entry.summary !== undefined) {
    return {
      added: entry.summary.added_lines,
      modified: entry.summary.modified_lines,
      removed: entry.summary.removed_lines,
    };
  }
  if (
    entry.lazy !== undefined &&
    typeof entry.added_lines === "number" &&
    typeof entry.removed_lines === "number"
  ) {
    return {
      added: entry.added_lines,
      modified: 0,
      removed: entry.removed_lines,
    };
  }
  return unknownLineStats();
}

export function formatLineStat(value: number | null): string {
  return value === null ? "?" : String(value);
}

export function fileEntryIsHydrated(entry: FileEntry): boolean {
  return entry.render_kind === "notebook" || entry.rows !== undefined;
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
    changed_cells: (current.changed_cells ?? 0) + notebookSummary.changed_cells,
    added_cells: (current.added_cells ?? 0) + notebookSummary.added_cells,
    modified_cells:
      (current.modified_cells ?? 0) + notebookSummary.modified_cells,
    removed_cells: (current.removed_cells ?? 0) + notebookSummary.removed_cells,
  };
}

export function groupLineStats(group: FileGroup): LineStats {
  return group.files.reduce(
    (total, file) => addLineStats(total, fileLineStats(file)),
    emptyLineStats(),
  );
}

export function fileKindStatus(fileKind: FileKind): string {
  return fileKind.type === "git" ? fileKind.status : "untracked";
}

function fileKindKey(fileKind: FileKind): string {
  if (fileKind.type === "untracked") {
    return "untracked";
  }
  return `git:${fileKind.status}`;
}

export function isNotebookSummary(
  summary: FileEntry["summary"],
): summary is NotebookSummary {
  return summary !== undefined && "changed_cells" in summary;
}

function splitRemoteQualifiedRef(
  ref: string,
  remoteNames: string[],
): { remote: string; value: string } {
  const normalizedRef = (ref || "").trim();
  for (const remoteName of [...remoteNames].sort(
    (left, right) => right.length - left.length,
  )) {
    const prefix = `${remoteName}/`;
    if (normalizedRef.startsWith(prefix)) {
      return {
        remote: remoteName,
        value: normalizedRef.slice(prefix.length),
      };
    }
  }
  return {
    remote: "",
    value: normalizedRef,
  };
}

function qualifyRemoteRef(
  remote: string,
  ref: string,
  remoteNames: string[],
): string {
  const normalizedRemote = (remote || "").trim();
  const normalizedRef = (ref || "").trim();
  if (!normalizedRemote || !normalizedRef) {
    return normalizedRef;
  }
  if (
    normalizedRef.startsWith("refs/") ||
    builtinSides.has(normalizedRef) ||
    /^[0-9a-f]{7,40}$/i.test(normalizedRef) ||
    normalizedRef.includes(":") ||
    normalizedRef.includes("^") ||
    normalizedRef.includes("~") ||
    remoteNames.some(
      (name) => normalizedRef === name || normalizedRef.startsWith(`${name}/`),
    )
  ) {
    return normalizedRef;
  }
  return `${normalizedRemote}/${normalizedRef}`;
}

export function branchReviewRef(
  source: BranchSource,
  remote: string,
  branch: string,
  remoteNames: string[],
): string {
  if (source === "local") {
    return branch.trim();
  }
  return qualifyRemoteRef(remote, branch, remoteNames);
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
      (order[fileKey(leftFile)] ?? 0) - (order[fileKey(rightFile)] ?? 0),
  );
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

export function fileDiffQueryKey(request: DiffRequest, entry: FileEntry) {
  return [
    "file-diff",
    request.repo_id,
    request.engine,
    request.mode,
    request.left,
    request.right,
    request.base_branch,
    request.review_branch,
    request.show_untracked,
    entry.left_path,
    entry.right_path,
    entry.display_name,
    fileKindKey(entry.file_kind),
  ] as const;
}

export function groupFilesByLabel(files: FileEntry[]): Map<string, FileGroup> {
  const groups = new Map<string, FileEntry[]>();
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
