import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
} from "solid-js";
import { useQueryClient } from "@tanstack/solid-query";
import { isCancelledError } from "@tanstack/query-core";
import type {
  DiffRow,
  FileEntry,
  FileKind,
  FoldHint,
  LazyReason,
  LazyState,
  ManifestEntry,
} from "./api";
import { fetchFileDiff } from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import { NotebookFile } from "./NotebookViews";
import {
  type FileGroup,
  type LinePin,
  type LoadedDiff,
  addHydratedNotebookSummary,
  directoryElementId,
  fileBodyAnchorElementId,
  fileDiffQueryKey,
  fileDisplayName,
  fileElementId,
  fileKey,
  fileMatchesLinePin,
  fileRows,
  groupFilesByLabel,
  sortFilesByOrder,
} from "./fileUtils";

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type StringMapSetter = (
  updater: (current: Record<string, string>) => Record<string, string>,
) => void;
type LoadedDiffSetter = (updater: (current: LoadedDiff) => LoadedDiff) => void;
const MANUAL_FILE_DIFF_TIMEOUT_MS = 60_000;
type LineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
};

function expansionValue(
  current: Record<string, boolean>,
  key: string,
  defaultValue: boolean,
): boolean {
  if (Object.hasOwn(current, key)) {
    return current[key];
  }
  return defaultValue;
}

function emptyLineStats(): LineStats {
  return { added: 0, modified: 0, removed: 0 };
}

function addLineStat(left: number | null, right: number | null): number | null {
  if (left === null || right === null) {
    return null;
  }
  return left + right;
}

function addLineStats(left: LineStats, right: LineStats): LineStats {
  return {
    added: addLineStat(left.added, right.added),
    modified: addLineStat(left.modified, right.modified),
    removed: addLineStat(left.removed, right.removed),
  };
}

function unknownLineStats(): LineStats {
  return { added: null, modified: null, removed: null };
}

function fileLineStats(entry: FileEntry): LineStats {
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

function formatLineStat(value: number | null): string {
  return value === null ? "?" : String(value);
}

function fileEntryIsHydrated(entry: FileEntry): boolean {
  return entry.render_kind === "notebook" || entry.rows !== undefined;
}

function defaultFileExpansion(entry: FileEntry): boolean {
  if (entry.default_expanded === undefined) {
    return false;
  }
  return entry.default_expanded;
}

function groupLineStats(group: FileGroup): LineStats {
  return group.files.reduce(
    (total, file) => addLineStats(total, fileLineStats(file)),
    emptyLineStats(),
  );
}

function fileKindStatus(fileKind: FileKind): string {
  return fileKind.type === "git" ? fileKind.status : "untracked";
}

function lazyOriginalReason(
  lazy: LazyState | null | undefined,
): LazyReason | null {
  if (lazy === undefined || lazy === null) {
    return null;
  }
  if (typeof lazy === "string") {
    return lazy;
  }
  return lazy.original;
}

function lazyIsError(lazy: LazyState | null | undefined): boolean {
  return typeof lazy === "object" && lazy !== null && lazy.type === "error";
}

function fileTreeLazyReason(file: FileEntry): LazyReason | null {
  const reason = lazyOriginalReason(file.lazy);
  if (reason !== null) {
    return reason;
  }
  if (file.lazy_reason !== undefined) {
    return file.lazy_reason;
  }
  return null;
}

function VisibilityIndicator(props: {
  size: "small" | "large";
  visible: boolean;
  virtualized?: boolean;
}) {
  return (
    <span
      class="visibility-indicator"
      classList={{
        large: props.size === "large",
        small: props.size === "small",
        visible: props.visible,
        virtualized: props.virtualized === true,
      }}
      aria-hidden="true"
    >
      {props.virtualized === true ? "V" : ""}
    </span>
  );
}

function TreeLineStats(props: { stats: LineStats }) {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {formatLineStat(props.stats.added)}</span>
      <span class="changed">~ {formatLineStat(props.stats.modified)}</span>
      <span class="removed">- {formatLineStat(props.stats.removed)}</span>
    </span>
  );
}

export function FileList(props: {
  files: FileEntry[];
  loadedDiff: LoadedDiff | null;
  currentParamsIdentity: () => string | null;
  diffViewMode: DiffViewMode;
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  aggressiveFolds: boolean;
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  updateLoadedDiff: LoadedDiffSetter;
}) {
  const groupsByLabel = createMemo(() => groupFilesByLabel(props.files));
  const groupLabels = createMemo(() => [...groupsByLabel().keys()]);
  const groupForLabel = (label: string) => {
    const group = groupsByLabel().get(label);
    if (group === undefined) {
      throw new Error(`Could not find directory group ${label}.`);
    }
    return group;
  };

  const setDirectoryExpanded = (label: string, expanded: boolean) => {
    const group = groupForLabel(label);
    props.setDirectoryExpansion((current) => ({
      ...current,
      [label]: expanded,
    }));
    props.setFileExpansion((current) => ({
      ...current,
      ...Object.fromEntries(
        group.files.map((file) => [fileKey(file), expanded]),
      ),
    }));
  };

  return (
    <section class="file-list" aria-label="Changed files">
      <Show
        when={props.files.length > 0}
        fallback={
          <div class="directory-groups">
            <section
              class="directory-group file-list-empty-shell"
              aria-label="No changed files"
            >
              <p class="empty file-list-empty">No files loaded yet.</p>
            </section>
          </div>
        }
      >
        <div class="directory-groups">
          <For each={groupLabels()}>
            {(label) => (
              <DirectoryGroup
                group={() => groupForLabel(label)}
                loadedDiff={props.loadedDiff}
                currentParamsIdentity={props.currentParamsIdentity}
                expanded={expansionValue(props.directoryExpansion, label, true)}
                fileExpansion={props.fileExpansion}
                loadingFiles={props.loadingFiles}
                fileErrors={props.fileErrors}
                linePin={props.linePin}
                forcedRichFileIds={props.forcedRichFileIds}
                aggressiveFolds={props.aggressiveFolds}
                onFileVirtualizedChange={props.onFileVirtualizedChange}
                diffViewMode={props.diffViewMode}
                setExpanded={(expanded) =>
                  setDirectoryExpanded(label, expanded)
                }
                setFileExpanded={(key, expanded) =>
                  props.setFileExpansion((current) => ({
                    ...current,
                    [key]: expanded,
                  }))
                }
                setLoadingFiles={props.setLoadingFiles}
                setFileErrors={props.setFileErrors}
                updateLoadedDiff={props.updateLoadedDiff}
              />
            )}
          </For>
        </div>
      </Show>
    </section>
  );
}

function DirectoryGroup(props: {
  group: () => FileGroup;
  loadedDiff: LoadedDiff | null;
  currentParamsIdentity: () => string | null;
  diffViewMode: DiffViewMode;
  expanded: boolean;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  aggressiveFolds: boolean;
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setFileExpanded: (key: string, expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  updateLoadedDiff: LoadedDiffSetter;
}) {
  const group = () => props.group();

  return (
    <section
      id={directoryElementId(group().label)}
      class="directory-group"
      classList={{ "is-collapsed": !props.expanded }}
    >
      <button
        type="button"
        class="directory-group-header"
        onClick={() => props.setExpanded(!props.expanded)}
      >
        <span class="directory-group-heading">
          <VisibilityIndicator size="large" visible={props.expanded} />
          <span class="directory-group-title">{group().label}</span>
        </span>
        <span class="badge badge-neutral">
          {group().files.length} file
          {group().files.length === 1 ? "" : "s"}
        </span>
      </button>
      <Show when={props.expanded}>
        <div class="directory-group-body">
          <For each={group().files}>
            {(file) => {
              const key = fileKey(file);
              return (
                <FileCard
                  file={file}
                  loadedDiff={props.loadedDiff}
                  currentParamsIdentity={props.currentParamsIdentity}
                  expanded={expansionValue(
                    props.fileExpansion,
                    key,
                    defaultFileExpansion(file),
                  )}
                  loading={fileIsLoading(props.loadingFiles, key)}
                  error={fileError(props.fileErrors, key)}
                  linePin={props.linePin}
                  forcedRichFileIds={props.forcedRichFileIds}
                  aggressiveFolds={props.aggressiveFolds}
                  onFileVirtualizedChange={props.onFileVirtualizedChange}
                  diffViewMode={props.diffViewMode}
                  setExpanded={(expanded) =>
                    props.setFileExpanded(key, expanded)
                  }
                  setLoadingFiles={props.setLoadingFiles}
                  setFileErrors={props.setFileErrors}
                  updateLoadedDiff={props.updateLoadedDiff}
                />
              );
            }}
          </For>
        </div>
      </Show>
    </section>
  );
}

export function FileTreeSidebar(props: {
  files: FileEntry[];
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  activeHunkFileId: string | null;
  virtualizedFileIds: string[];
  viewMode: DiffViewMode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  onScrollToDirectory: (group: FileGroup) => void;
  onScrollToFile: (file: FileEntry) => void;
}) {
  const groups = createMemo(() => [...groupFilesByLabel(props.files).values()]);
  const lineStats = createMemo(() =>
    groupLineStats({ label: "", files: props.files }),
  );
  const directoryExpanded = (group: FileGroup) =>
    expansionValue(props.directoryExpansion, group.label, true);
  const fileExpanded = (file: FileEntry) =>
    expansionValue(
      props.fileExpansion,
      fileKey(file),
      defaultFileExpansion(file),
    );

  const setDirectoryExpanded = (group: FileGroup, expanded: boolean) => {
    props.setDirectoryExpansion((current) => ({
      ...current,
      [group.label]: expanded,
    }));
    props.setFileExpansion((current) => ({
      ...current,
      ...Object.fromEntries(
        group.files.map((file) => [fileKey(file), expanded]),
      ),
    }));
  };

  const setFileExpanded = (file: FileEntry, expanded: boolean) => {
    props.setFileExpansion((current) => ({
      ...current,
      [fileKey(file)]: expanded,
    }));
  };
  const fileIsActiveHunkFile = (file: FileEntry) =>
    props.activeHunkFileId === fileElementId(fileKey(file));
  const fileIsVirtualized = (file: FileEntry) =>
    props.virtualizedFileIds.includes(fileElementId(fileKey(file)));

  createEffect(() => {
    if (!props.open || props.activeHunkFileId === null) {
      return;
    }
    requestAnimationFrame(() => {
      const activeRow = document.querySelector<HTMLElement>(
        `[data-file-tree-file-id="${props.activeHunkFileId}"]`,
      );
      activeRow?.scrollIntoView({ block: "nearest", behavior: "instant" });
    });
  });

  const shouldRenderTree = () =>
    props.files.length > 0 ? true : props.viewMode === "inline";

  return (
    <Show when={shouldRenderTree()}>
      <div
        class="file-tree-shell"
        classList={{
          open: props.open,
          "file-tree-shell-inline": props.viewMode === "inline",
        }}
      >
        <Show when={props.open}>
          <aside
            id="fileTreeSidebar"
            class="file-tree-sidebar"
            aria-label="Changed file tree"
          >
            <div class="file-tree-groups">
              <Show
                when={groups().length > 0}
                fallback={<p class="file-tree-empty">No files loaded yet.</p>}
              >
                <For each={groups()}>
                  {(group) => (
                    <section class="file-tree-group">
                      <div class="file-tree-directory">
                        <button
                          type="button"
                          class="file-tree-visibility-toggle"
                          onClick={() =>
                            setDirectoryExpanded(
                              group,
                              !directoryExpanded(group),
                            )
                          }
                          aria-label={
                            directoryExpanded(group)
                              ? `Fold ${group.label}`
                              : `Show ${group.label}`
                          }
                        >
                          <VisibilityIndicator
                            size="small"
                            visible={directoryExpanded(group)}
                          />
                        </button>
                        <button
                          type="button"
                          class="file-tree-directory-target"
                          onClick={() => props.onScrollToDirectory(group)}
                        >
                          {group.label}
                        </button>
                        <TreeLineStats stats={groupLineStats(group)} />
                      </div>
                      <For each={group.files}>
                        {(file) => {
                          const virtualized = () => fileIsVirtualized(file);
                          const lazyReason = () => fileTreeLazyReason(file);
                          return (
                            <div
                              class="file-tree-file"
                              data-file-tree-file-id={fileElementId(
                                fileKey(file),
                              )}
                              classList={{
                                added:
                                  fileKindStatus(file.file_kind) === "added",
                                removed:
                                  fileKindStatus(file.file_kind) === "deleted",
                                renamed:
                                  fileKindStatus(file.file_kind) === "renamed",
                                untracked:
                                  fileKindStatus(file.file_kind) ===
                                  "untracked",
                                lazy: lazyReason() !== null,
                                "lazy-error": lazyIsError(file.lazy),
                                "lazy-generated": lazyReason() === "generated",
                                "lazy-too-big": lazyReason() === "too_big",
                                "active-hunk-file": fileIsActiveHunkFile(file),
                              }}
                              aria-current={
                                fileIsActiveHunkFile(file) ? "true" : undefined
                              }
                              title={fileDisplayName(file)}
                            >
                              <button
                                type="button"
                                class="file-tree-visibility-toggle"
                                onClick={() =>
                                  setFileExpanded(file, !fileExpanded(file))
                                }
                                aria-label={
                                  fileExpanded(file)
                                    ? `Fold ${fileDisplayName(file)}`
                                    : `Show ${fileDisplayName(file)}`
                                }
                              >
                                <VisibilityIndicator
                                  size="small"
                                  visible={fileExpanded(file)}
                                  virtualized={virtualized()}
                                />
                              </button>
                              <button
                                type="button"
                                class="file-tree-file-target"
                                aria-current={
                                  fileIsActiveHunkFile(file)
                                    ? "true"
                                    : undefined
                                }
                                onClick={() => props.onScrollToFile(file)}
                              >
                                <span class="file-tree-file-name">
                                  {fileDisplayName(file)}
                                </span>
                                <TreeLineStats stats={fileLineStats(file)} />
                              </button>
                            </div>
                          );
                        }}
                      </For>
                    </section>
                  )}
                </For>
              </Show>
            </div>
          </aside>
        </Show>
        <button
          type="button"
          class="file-tree-toggle"
          onClick={() => props.onOpenChange(!props.open)}
          aria-expanded={props.open}
          aria-controls="fileTreeSidebar"
          aria-label={props.open ? "Close file tree" : "Open file tree"}
        >
          <span class="file-tree-icon" aria-hidden="true">
            ▦
          </span>
          <Show when={props.open}>
            <span class="file-tree-label">Files</span>
            <TreeLineStats stats={lineStats()} />
          </Show>
          <kbd>t</kbd>
        </button>
      </div>
    </Show>
  );
}

function FileCard(props: {
  file: FileEntry;
  loadedDiff: LoadedDiff | null;
  currentParamsIdentity: () => string | null;
  diffViewMode: DiffViewMode;
  expanded: boolean;
  loading: boolean;
  error: string;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  aggressiveFolds: boolean;
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  updateLoadedDiff: LoadedDiffSetter;
}) {
  const queryClient = useQueryClient();
  let bodyViewport: HTMLDivElement | undefined;
  const [nearViewport, setNearViewport] = createSignal(false);
  const key = () => fileKey(props.file);
  const lineStats = () => fileLineStats(props.file);
  const displayName = () => fileDisplayName(props.file);
  const needsHydration = () => !fileEntryIsHydrated(props.file);
  const isPinnedFile = () =>
    props.linePin !== null && fileMatchesLinePin(props.file, props.linePin);
  const isForcedRichFile = () =>
    props.forcedRichFileIds.includes(fileElementId(key()));
  const canVirtualizeBody = () =>
    props.file.render_kind !== "notebook" && canRenderRows();
  const shouldRenderRichBody = () =>
    !canVirtualizeBody() ||
    nearViewport() ||
    isPinnedFile() ||
    isForcedRichFile();
  const isVirtualizedBody = () =>
    props.expanded && canRenderRows() && !shouldRenderRichBody();
  createEffect(() => {
    const fileId = fileElementId(key());
    props.onFileVirtualizedChange(fileId, isVirtualizedBody());
    onCleanup(() => props.onFileVirtualizedChange(fileId, false));
  });
  const lazyTitle = () => {
    if (lazyIsError(props.file.lazy)) {
      return "Retry file diff";
    }
    switch (lazyOriginalReason(props.file.lazy)) {
      case "deleted":
        return "Load deleted file diff";
      case "generated":
        return "Load generated diff";
      case "too_big":
        return "Load large diff";
      case "untracked":
        return "Load untracked file";
      case "pure_renamed":
        return "Load renamed file diff";
      default:
        return unsupportedLazyReason(props.file.lazy);
    }
  };
  const lazyMeta = () => {
    if (lazyIsError(props.file.lazy)) {
      return `${displayName()} failed to load. Click to retry.`;
    }
    switch (lazyOriginalReason(props.file.lazy)) {
      case "deleted":
        return `${displayName()} is deleted. Click to fetch and open it.`;
      case "generated":
        return `${displayName()} looks generated. Click to fetch and open it.`;
      case "too_big":
        return `${displayName()} is large. Click to fetch and open it.`;
      case "untracked":
        return `${displayName()} is untracked. Click to fetch and open it.`;
      case "pure_renamed":
        return `${displayName()} was renamed without content changes. Click to fetch and open it.`;
      default:
        return unsupportedLazyReason(props.file.lazy);
    }
  };
  const canRenderRows = () =>
    fileEntryIsHydrated(props.file) &&
    props.file.render_kind !== "notebook" &&
    fileRows(props.file).length > 0;
  const hasEngineWarning = () =>
    props.file.engine_warning !== null &&
    props.file.engine_warning !== undefined;

  createEffect(() => {
    props.expanded;
    props.file;
    if (bodyViewport === undefined || !props.expanded || !canVirtualizeBody()) {
      setNearViewport(false);
      return;
    }
    if (!("IntersectionObserver" in window)) {
      setNearViewport(true);
      return;
    }

    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (entry === undefined) {
          throw new Error("Intersection observer did not provide an entry.");
        }
        setNearViewport(entry.isIntersecting);
      },
      { rootMargin: "1500px 0px" },
    );
    observer.observe(bodyViewport);
    onCleanup(() => observer.disconnect());
  });

  const expand = async () => {
    props.setExpanded(true);
    const activeDiff = props.loadedDiff;
    const paramsIdentity = props.currentParamsIdentity();
    const activeKey = key();
    if (
      !needsHydration() ||
      activeDiff === null ||
      paramsIdentity === null ||
      props.loading
    ) {
      return;
    }
    props.setLoadingFiles((current) => ({
      ...current,
      [activeKey]: true,
    }));
    props.setFileErrors((current) => ({ ...current, [activeKey]: "" }));
    try {
      if (props.file.lazy === undefined || props.file.lazy === null) {
        throw new Error("Hydrated file did not have a lazy reason.");
      }
      const originalReason = lazyOriginalReason(props.file.lazy);
      const lazyFetchEntry: ManifestEntry = {
        file_kind: props.file.file_kind,
        left_path: props.file.left_path,
        right_path: props.file.right_path,
        lazy: originalReason,
      };
      const queryKey = fileDiffQueryKey(activeDiff.params, lazyFetchEntry);
      queryClient.removeQueries({ queryKey });
      const hydrated = await queryClient.fetchQuery({
        queryKey,
        queryFn: ({ signal }) =>
          fetchFileDiff(
            activeDiff.params,
            lazyFetchEntry,
            signal,
            MANUAL_FILE_DIFF_TIMEOUT_MS,
          ),
        retry: false,
        staleTime: 0,
      });
      if (props.currentParamsIdentity() !== paramsIdentity) {
        return;
      }
      // The rendered FileEntry comes from /api/file-diff. The only extra field
      // is client-only history for preserving the lazy marker after hydration.
      const nextEntry =
        originalReason === null
          ? hydrated
          : {
              ...hydrated,
              lazy_reason: originalReason,
            };
      const nextKey = fileKey(nextEntry);
      props.updateLoadedDiff((current) => {
        const withoutCurrent = current.files.filter(
          (entry) => fileKey(entry) !== nextKey,
        );
        return {
          ...current,
          // Insert only the /api/file-diff FileEntry into the rendered file
          // list; lazy placeholders come from /api/lazy-info.
          files: sortFilesByOrder(
            [...withoutCurrent, nextEntry],
            current.fileOrder,
          ),
          lazyFiles: current.lazyFiles.filter(
            (entry) => fileKey(entry) !== activeKey,
          ),
          summary: addHydratedNotebookSummary(current.summary, nextEntry),
        };
      });
    } catch (error) {
      if (props.currentParamsIdentity() !== paramsIdentity) {
        return;
      }
      if (isCancelledError(error)) {
        return;
      }
      props.setFileErrors((current) => ({
        ...current,
        [activeKey]:
          error instanceof Error ? error.message : "Failed to load file diff.",
      }));
    } finally {
      if (props.currentParamsIdentity() !== paramsIdentity) {
        return;
      }
      props.setLoadingFiles((current) => ({
        ...current,
        [activeKey]: false,
      }));
    }
  };

  const toggle = () => {
    if (props.expanded) {
      props.setExpanded(false);
      return;
    }
    void expand();
  };

  return (
    <article
      id={fileElementId(key())}
      class="file-card"
      classList={{
        "is-collapsed": !props.expanded,
        "file-card-lazy-generated": Boolean(props.file.lazy),
      }}
    >
      <button
        type="button"
        class="file-card-header"
        onClick={toggle}
        aria-expanded={props.expanded}
      >
        <span class="file-card-heading">
          <VisibilityIndicator
            size="large"
            visible={props.expanded && !isVirtualizedBody()}
            virtualized={isVirtualizedBody()}
          />
          <span class="file-card-title-row">
            <h2>{displayName()}</h2>
            <span class="file-card-status">
              {fileKindStatus(props.file.file_kind)}
            </span>
            <Show when={hasEngineWarning()}>
              <span
                class="file-card-engine-warning"
                title={engineWarningMessage(props.file)}
              >
                Difftastic failed: git fallback
              </span>
            </Show>
          </span>
        </span>
        <span class="file-stats">
          <span class="delta added">+ {formatLineStat(lineStats().added)}</span>
          <span class="delta changed">
            ~ {formatLineStat(lineStats().modified)}
          </span>
          <span class="delta removed">
            - {formatLineStat(lineStats().removed)}
          </span>
        </span>
      </button>
      <div
        id={fileBodyAnchorElementId(key())}
        class="file-card-scroll-target"
        aria-hidden="true"
      />
      <Show when={!props.expanded && canRenderRows()}>
        <HunkSkipAnchors file={props.file} />
      </Show>
      <Show
        when={
          props.expanded &&
          (!needsHydration() || props.loading || props.error !== "")
        }
      >
        <div ref={bodyViewport} class="file-card-body">
          <Show when={props.loading}>
            <p class="file-placeholder">Loading file diff...</p>
          </Show>
          <Show when={props.error !== ""}>
            <p class="file-placeholder error-text">{props.error}</p>
          </Show>
          <Show when={!props.loading && props.error === ""}>
            <Show when={props.file.render_kind === "notebook"}>
              <NotebookFile
                file={props.file}
                diffParams={loadedParams(props.loadedDiff)}
                diffViewMode={props.diffViewMode}
                aggressiveFolds={props.aggressiveFolds}
              />
            </Show>
            <Show when={props.file.render_kind !== "notebook"}>
              <Show when={canRenderRows()}>
                <Show
                  when={shouldRenderRichBody()}
                  fallback={<PlainSplitFileDiff file={props.file} />}
                >
                  <DiffGrid
                    displayName={displayName()}
                    leftLabel={requiredSideLabel(props.file, "left")}
                    rightLabel={requiredSideLabel(props.file, "right")}
                    rows={fileRows(props.file)}
                    foldHints={fileFoldHints(props.file)}
                    viewMode={props.diffViewMode}
                    aggressiveFolds={props.aggressiveFolds}
                    semanticReplaceRows={loadedEngineIsDifftastic(
                      props.loadedDiff,
                    )}
                  />
                </Show>
              </Show>
            </Show>
          </Show>
        </div>
      </Show>
      <Show
        when={
          needsHydration() &&
          props.file.lazy !== null &&
          props.file.lazy !== undefined &&
          !props.loading
        }
      >
        <button
          type="button"
          class="file-lazy-load-toggle"
          classList={{
            "is-error": lazyIsError(props.file.lazy),
            "is-untracked": lazyOriginalReason(props.file.lazy) === "untracked",
            "is-generated": lazyOriginalReason(props.file.lazy) === "generated",
            "is-deleted": lazyOriginalReason(props.file.lazy) === "deleted",
            "is-too-big": lazyOriginalReason(props.file.lazy) === "too_big",
            "is-pure-renamed":
              lazyOriginalReason(props.file.lazy) === "pure_renamed",
          }}
          onClick={expand}
        >
          <span class="file-lazy-load-toggle-title">{lazyTitle()}</span>
          <span class="file-lazy-load-toggle-meta">{lazyMeta()}</span>
        </button>
      </Show>
    </article>
  );
}

function PlainSplitFileDiff(props: { file: FileEntry }) {
  const text = () => plainSplitText(fileRows(props.file));
  const hunkAnchors = () => virtualHunkAnchors(fileRows(props.file));

  return (
    <div class="plain-split-diff" aria-label="Virtualized plain split diff">
      <For each={hunkAnchors()}>
        {(anchor) => (
          <span
            class="diff-row hunk-anchor virtual-hunk-anchor"
            style={{ top: `${virtualHunkAnchorTop(anchor.rowIndex)}px` }}
            aria-hidden="true"
          />
        )}
      </For>
      <pre>{text().left}</pre>
      <pre>{text().right}</pre>
    </div>
  );
}

function fileFoldHints(file: FileEntry): FoldHint[] {
  if (file.fold_hints === undefined) {
    throw new Error(`${fileDisplayName(file)} is missing fold hints.`);
  }
  return file.fold_hints;
}

function requiredSideLabel(file: FileEntry, side: "left" | "right"): string {
  const value = side === "left" ? file.left_label : file.right_label;
  if (value !== undefined && value.length > 0) {
    return value;
  }
  throw new Error(`${fileDisplayName(file)} is missing ${side} label.`);
}

function HunkSkipAnchors(props: { file: FileEntry }) {
  const hunkAnchors = () => virtualHunkAnchors(fileRows(props.file));

  return (
    <div class="hunk-skip-anchors" aria-hidden="true">
      <For each={hunkAnchors()}>
        {() => <span class="diff-row hunk-anchor hunk-skip" />}
      </For>
    </div>
  );
}

function virtualHunkAnchors(rows: DiffRow[]): { rowIndex: number }[] {
  let previousChanged = false;
  const anchors: { rowIndex: number }[] = [];
  rows.forEach((row, rowIndex) => {
    if (row.status === "fold") {
      previousChanged = false;
      return;
    }

    const changed = isChangedDiffRowStatus(row.status);
    if (changed && !previousChanged) {
      anchors.push({ rowIndex });
    }
    previousChanged = changed;
  });
  return anchors;
}

const VIRTUAL_HUNK_TOP_OFFSET_PX = 10;
const VIRTUAL_HUNK_ROW_HEIGHT_PX = 17.4;

function virtualHunkAnchorTop(rowIndex: number): number {
  // Mirrors the approximate line box used by the plain virtualized fallback.
  return VIRTUAL_HUNK_TOP_OFFSET_PX + rowIndex * VIRTUAL_HUNK_ROW_HEIGHT_PX;
}

function isChangedDiffRowStatus(status: DiffRow["status"]): boolean {
  return status === "replace" || status === "insert" || status === "delete";
}

function plainSplitText(rows: DiffRow[]): { left: string; right: string } {
  const left: string[] = [];
  const right: string[] = [];

  for (const row of rows) {
    left.push(plainSideText(row, "left"));
    right.push(plainSideText(row, "right"));
  }

  return {
    left: left.join("\n"),
    right: right.join("\n"),
  };
}

function plainSideText(row: DiffRow, side: "left" | "right"): string {
  if (row.status === "fold") {
    return foldRowLabel(row);
  }
  if (row.status === "elided") {
    return elidedRowLabel(row);
  }
  const text = side === "left" ? row.left_text : row.right_text;
  if (text === null) {
    return "";
  }
  return text;
}

function fileIsLoading(loadingFiles: Record<string, boolean>, key: string) {
  const loading = loadingFiles[key];
  if (loading === undefined) {
    return false;
  }
  return loading;
}

function fileError(fileErrors: Record<string, string>, key: string) {
  const error = fileErrors[key];
  if (error === undefined) {
    return "";
  }
  return error;
}

function engineWarningMessage(file: FileEntry): string {
  const warning = file.engine_warning;
  if (warning === null || warning === undefined) {
    throw new Error(`${fileDisplayName(file)} is missing engine warning.`);
  }
  return warning.message;
}

function loadedParams(loadedDiff: LoadedDiff | null): LoadedDiff["params"] {
  if (loadedDiff === null) {
    throw new Error("Loaded diff is required to render a notebook file.");
  }
  return loadedDiff.params;
}

function loadedEngineIsDifftastic(loadedDiff: LoadedDiff | null): boolean {
  if (loadedDiff === null) {
    throw new Error("Loaded diff is required to render a text file.");
  }
  return loadedDiff.params.engine === "difftastic";
}

function foldRowLabel(row: DiffRow): string {
  if (typeof row.label === "string" && row.label.length > 0) {
    return row.label;
  }
  if (typeof row.count !== "number") {
    throw new Error("Fold row is missing count.");
  }
  return `... ${row.count} lines`;
}

function elidedRowLabel(row: DiffRow): string {
  if (typeof row.label !== "string" || row.label.length === 0) {
    throw new Error("Elided row is missing label.");
  }
  return row.label;
}

function unsupportedLazyReason(value: unknown): never {
  throw new Error(`Unsupported lazy reason: ${String(value)}.`);
}
