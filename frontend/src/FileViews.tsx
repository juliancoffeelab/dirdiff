import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
} from "solid-js";
import { useQueryClient } from "@tanstack/solid-query";
import type { DiffRequest, DiffRow, FileEntry, Summary } from "./api";
import { fetchFileDiff } from "./api";
import { DiffGrid, type DiffViewMode } from "./DiffGrid";
import { NotebookFile } from "./NotebookViews";
import {
  type FileGroup,
  type LinePin,
  addHydratedNotebookSummary,
  directoryElementId,
  expansionValue,
  fileBasename,
  fileBodyAnchorElementId,
  fileDiffQueryKey,
  fileDisplayName,
  fileElementId,
  fileEntryIsHydrated,
  fileKey,
  fileKindStatus,
  fileLineStats,
  fileMatchesLinePin,
  formatLineStat,
  groupFilesByLabel,
  groupLineStats,
  sortFilesByOrder,
} from "./model";

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type FilesSetter = (updater: (current: FileEntry[]) => FileEntry[]) => void;
type SummarySetter = (updater: (current: Summary) => Summary) => void;
type StringMapSetter = (
  updater: (current: Record<string, string>) => Record<string, string>,
) => void;

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
        virtualized: props.virtualized ?? false,
      }}
      aria-hidden="true"
    >
      {props.virtualized ? "V" : ""}
    </span>
  );
}

function TreeLineStats(props: { stats: import("./model").LineStats }) {
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
  request: DiffRequest | null;
  requestVersion: () => number;
  fileOrder: Record<string, number>;
  diffViewMode: DiffViewMode;
  directoryExpansion: Record<string, boolean>;
  fileExpansion: Record<string, boolean>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
  onSetAllExpanded: (expanded: boolean) => void;
}) {
  const groupsByLabel = createMemo(() => groupFilesByLabel(props.files));
  const groupLabels = createMemo(() => [...groupsByLabel().keys()]);
  const groupForLabel = (label: string) => {
    const group = groupsByLabel().get(label);
    if (!group) {
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
        fallback={<p class="empty">No files loaded yet.</p>}
      >
        <div class="repo-fold-controls">
          <button type="button" onClick={() => props.onSetAllExpanded(false)}>
            Fold all
          </button>
          <button type="button" onClick={() => props.onSetAllExpanded(true)}>
            Show all
          </button>
        </div>
        <div class="directory-groups">
          <For each={groupLabels()}>
            {(label) => (
              <DirectoryGroup
                group={() => groupForLabel(label)}
                request={props.request}
                requestVersion={props.requestVersion}
                expanded={props.directoryExpansion[label] ?? true}
                fileExpansion={props.fileExpansion}
                fileOrder={props.fileOrder}
                loadingFiles={props.loadingFiles}
                fileErrors={props.fileErrors}
                linePin={props.linePin}
                forcedRichFileIds={props.forcedRichFileIds}
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
                setFiles={props.setFiles}
                setLazyFiles={props.setLazyFiles}
                setSummary={props.setSummary}
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
  request: DiffRequest | null;
  requestVersion: () => number;
  diffViewMode: DiffViewMode;
  expanded: boolean;
  fileExpansion: Record<string, boolean>;
  fileOrder: Record<string, number>;
  loadingFiles: Record<string, boolean>;
  fileErrors: Record<string, string>;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setFileExpanded: (key: string, expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
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
                  request={props.request}
                  requestVersion={props.requestVersion}
                  expanded={
                    props.fileExpansion[key] ?? file.default_expanded ?? false
                  }
                  loading={props.loadingFiles[key] ?? false}
                  error={props.fileErrors[key] ?? ""}
                  linePin={props.linePin}
                  forcedRichFileIds={props.forcedRichFileIds}
                  onFileVirtualizedChange={props.onFileVirtualizedChange}
                  diffViewMode={props.diffViewMode}
                  fileOrder={props.fileOrder}
                  setExpanded={(expanded) =>
                    props.setFileExpanded(key, expanded)
                  }
                  setLoadingFiles={props.setLoadingFiles}
                  setFileErrors={props.setFileErrors}
                  setFiles={props.setFiles}
                  setLazyFiles={props.setLazyFiles}
                  setSummary={props.setSummary}
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
      file.default_expanded ?? false,
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

  return (
    <Show when={props.files.length > 0}>
      <div class="file-tree-shell" classList={{ open: props.open }}>
        <Show when={props.open}>
          <aside
            id="fileTreeSidebar"
            class="file-tree-sidebar"
            aria-label="Changed file tree"
          >
            <div class="file-tree-groups">
              <For each={groups()}>
                {(group) => (
                  <section class="file-tree-group">
                    <div class="file-tree-directory">
                      <button
                        type="button"
                        class="file-tree-visibility-toggle"
                        onClick={() =>
                          setDirectoryExpanded(group, !directoryExpanded(group))
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
                        return (
                          <div
                            class="file-tree-file"
                            data-file-tree-file-id={fileElementId(
                              fileKey(file),
                            )}
                            classList={{
                              added: fileKindStatus(file.file_kind) === "added",
                              removed:
                                fileKindStatus(file.file_kind) === "deleted",
                              lazy: Boolean(file.lazy),
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
                                fileIsActiveHunkFile(file) ? "true" : undefined
                              }
                              onClick={() => props.onScrollToFile(file)}
                            >
                              <span class="file-tree-file-name">
                                {fileBasename(file)}
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
  request: DiffRequest | null;
  requestVersion: () => number;
  diffViewMode: DiffViewMode;
  fileOrder: Record<string, number>;
  expanded: boolean;
  loading: boolean;
  error: string;
  linePin: LinePin | null;
  forcedRichFileIds: string[];
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  setExpanded: (expanded: boolean) => void;
  setLoadingFiles: ExpansionSetter;
  setFileErrors: StringMapSetter;
  setFiles: FilesSetter;
  setLazyFiles: FilesSetter;
  setSummary: SummarySetter;
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
    switch (props.file.lazy) {
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
        return "Load diff";
    }
  };
  const lazyMeta = () => {
    switch (props.file.lazy) {
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
        return `${displayName()} is folded by default. Click to fetch and open it.`;
    }
  };
  const canRenderRows = () =>
    fileEntryIsHydrated(props.file) &&
    props.file.render_kind !== "notebook" &&
    (props.file.rows?.length ?? 0) > 0;

  createEffect(() => {
    props.expanded;
    props.file;
    if (!bodyViewport || !props.expanded || !canVirtualizeBody()) {
      setNearViewport(false);
      return;
    }
    if (!("IntersectionObserver" in window)) {
      setNearViewport(true);
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => setNearViewport(entry?.isIntersecting ?? false),
      { rootMargin: "1500px 0px" },
    );
    observer.observe(bodyViewport);
    onCleanup(() => observer.disconnect());
  });

  const expand = async () => {
    props.setExpanded(true);
    const activeRequest = props.request;
    const activeVersion = props.requestVersion();
    const activeKey = key();
    if (!needsHydration() || !activeRequest || props.loading) {
      return;
    }
    props.setLoadingFiles((current) => ({
      ...current,
      [activeKey]: true,
    }));
    props.setFileErrors((current) => ({ ...current, [activeKey]: "" }));
    try {
      const hydrated = await queryClient.fetchQuery({
        queryKey: fileDiffQueryKey(activeRequest, props.file),
        queryFn: () => fetchFileDiff(activeRequest, props.file),
        staleTime: 0,
      });
      if (props.requestVersion() !== activeVersion) {
        return;
      }
      const nextEntry = { ...props.file, ...hydrated, lazy: null };
      const nextKey = fileKey(nextEntry);
      props.setFiles((current) => {
        const withoutCurrent = current.filter(
          (entry) => fileKey(entry) !== nextKey,
        );
        return sortFilesByOrder(
          [...withoutCurrent, nextEntry],
          props.fileOrder,
        );
      });
      props.setLazyFiles((current) =>
        current.filter((entry) => fileKey(entry) !== activeKey),
      );
      props.setSummary((current) =>
        addHydratedNotebookSummary(current, nextEntry),
      );
    } catch (error) {
      if (props.requestVersion() !== activeVersion) {
        return;
      }
      props.setFileErrors((current) => ({
        ...current,
        [activeKey]:
          error instanceof Error ? error.message : "Failed to load file diff.",
      }));
    } finally {
      if (props.requestVersion() !== activeVersion) {
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
            <Show when={props.file.engine_warning}>
              {(warning) => (
                <span
                  class="file-card-engine-warning"
                  title={warning().message}
                >
                  Difftastic failed: text fallback
                </span>
              )}
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
          props.expanded && (!needsHydration() || props.loading || props.error)
        }
      >
        <div ref={bodyViewport} class="file-card-body">
          <Show when={props.loading}>
            <p class="file-placeholder">Loading file diff...</p>
          </Show>
          <Show when={props.error}>
            <p class="file-placeholder error-text">{props.error}</p>
          </Show>
          <Show when={!props.loading && !props.error}>
            <Show when={props.file.render_kind === "notebook"}>
              <NotebookFile
                file={props.file}
                request={props.request}
                diffViewMode={props.diffViewMode}
              />
            </Show>
            <Show when={props.file.render_kind !== "notebook"}>
              <Show when={canRenderRows()}>
                <Show
                  when={shouldRenderRichBody()}
                  fallback={<PlainSplitFileDiff file={props.file} />}
                >
                  <DiffGrid
                    file={props.file}
                    viewMode={props.diffViewMode}
                    semanticReplaceRows={props.request?.engine === "difftastic"}
                  />
                </Show>
              </Show>
            </Show>
          </Show>
        </div>
      </Show>
      <Show when={needsHydration() && props.file.lazy && !props.loading}>
        <button
          type="button"
          class="file-lazy-load-toggle"
          classList={{
            "is-untracked": props.file.lazy === "untracked",
            "is-generated": props.file.lazy === "generated",
            "is-deleted": props.file.lazy === "deleted",
            "is-too-big": props.file.lazy === "too_big",
            "is-pure-renamed": props.file.lazy === "pure_renamed",
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
  const text = () => plainSplitText(props.file.rows ?? []);
  const hunkAnchors = () => virtualHunkAnchors(props.file.rows ?? []);

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

function HunkSkipAnchors(props: { file: FileEntry }) {
  const hunkAnchors = () => virtualHunkAnchors(props.file.rows ?? []);

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

function fileCanHaveDomHunks(file: FileEntry): boolean {
  return (
    fileEntryIsHydrated(file) &&
    file.render_kind !== "notebook" &&
    (file.rows?.length ?? 0) > 0 &&
    (file.rows ?? []).some((row) => isChangedDiffRowStatus(row.status))
  );
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
    return row.label ?? `... ${row.count ?? 0} lines`;
  }
  return (side === "left" ? row.left_text : row.right_text) ?? "";
}
