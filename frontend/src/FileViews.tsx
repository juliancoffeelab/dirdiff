import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  type JSX,
  onCleanup,
} from "solid-js";
import type {
  DiffRow,
  FileEntry,
  FileKind,
  FoldHint,
  LazyReason,
  LazyState,
} from "./api";
import {
  DiffGrid,
  markHunkAnchors,
  type DiffViewMode,
  type HunkDiffRow,
} from "./DiffGrid";
import { addFoldRows, isFoldRow, type RenderRow } from "./folds";
import { NotebookFile } from "./NotebookViews";
import {
  type FileTreeDirectoryNode,
  type FileTreeNode,
  type LinePin,
  type RenderedFileEntry,
  fileBodyAnchorElementId,
  fileDisplayName,
  fileElementId,
  fileEntryIsHydrated,
  fileKey,
  fileMatchesLinePin,
  fileRows,
} from "./fileUtils";

type ExpansionSetter = (
  updater: (current: Record<string, boolean>) => Record<string, boolean>,
) => void;
type BooleanMap = Record<string, boolean | undefined>;
type StringMap = Record<string, string | undefined>;
type HunkPosition = {
  current: number;
  total: number;
};
type LineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
  moved: number | null;
};

function expansionValue(
  current: BooleanMap,
  key: string,
  defaultValue: boolean,
): boolean {
  const value = current[key];
  if (value !== undefined) {
    return value;
  }
  return defaultValue;
}

function emptyLineStats(): LineStats {
  return { added: 0, modified: 0, removed: 0, moved: 0 };
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
    moved: addLineStat(left.moved, right.moved),
  };
}

function unknownLineStats(): LineStats {
  return { added: null, modified: null, removed: null, moved: null };
}

function fileLineStats(entry: FileEntry): LineStats {
  if (entry.summary !== undefined) {
    return {
      added: entry.summary.added_lines,
      modified: entry.summary.modified_lines,
      removed: entry.summary.removed_lines,
      moved: entry.summary.moved_lines,
    };
  }
  if (
    entry.lazy !== undefined &&
    typeof entry.added_lines === "number" &&
    typeof entry.removed_lines === "number"
  ) {
    let moved: number | null = null;
    if (typeof entry.moved_lines === "number") {
      moved = entry.moved_lines;
    }
    return {
      added: entry.added_lines,
      modified: 0,
      removed: entry.removed_lines,
      moved,
    };
  }
  return unknownLineStats();
}

function formatLineStat(value: number | null): string {
  return value === null ? "?" : String(value);
}

function defaultFileExpansion(entry: FileEntry): boolean {
  if (entry.default_expanded === undefined) {
    return false;
  }
  return entry.default_expanded;
}

function filesLineStats(files: FileEntry[]): LineStats {
  return files.reduce(
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
      <span class="moved">* {formatLineStat(props.stats.moved)}</span>
    </span>
  );
}

/**
 * Render the card stack for the current diff result.
 *
 * FileList owns list-level rendering only: the empty shell, stable file-card
 * keys, per-file expansion lookup, loading/error lookup, and the handoff from
 * each rendered entry to FileCard.  It does not decide which files belong in
 * the current diff, mutate file contents, resolve lazy entries, compute hunk
 * navigation, or own file-tree state; those decisions are made by the diff
 * resources and UI-state layers before entries reach this component.
 *
 * One pre-manifest state is intentional.  Repo controls can be active before
 * the first manifest response arrives, so ``files`` may be empty while
 * ``cacheId`` is null and the component renders only its empty shell.  Any
 * non-empty file list must come from a manifest payload, and therefore must
 * carry that manifest's cache id for file-card hydration and notebook section
 * fetches.
 */
export function FileList(props: {
  files: RenderedFileEntry[];
  cacheId: string | null;
  hunkPosition: HunkPosition;
  diffViewMode: DiffViewMode;
  fileExpansion: BooleanMap;
  loadingFiles: BooleanMap;
  fileErrors: StringMap;
  linePin: LinePin | null;
  isForcedRichFileId: (fileId: string) => boolean;
  aggressiveFolds: boolean;
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  onHydrateFile: (file: RenderedFileEntry) => void;
  setFileExpansion: ExpansionSetter;
}) {
  if (props.files.length > 0 && props.cacheId === null) {
    throw new Error("FileList with files requires a cache id.");
  }
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
          <For each={props.files}>
            {(file) => {
              const key = fileKey(file);
              return (
                <FileCard
                  file={file}
                  cacheId={props.cacheId}
                  hunkPosition={props.hunkPosition}
                  expanded={expansionValue(
                    props.fileExpansion,
                    key,
                    defaultFileExpansion(file),
                  )}
                  loading={fileIsLoading(props.loadingFiles, key)}
                  error={fileError(props.fileErrors, key)}
                  linePin={props.linePin}
                  isForcedRichFileId={props.isForcedRichFileId}
                  aggressiveFolds={props.aggressiveFolds}
                  onFileVirtualizedChange={props.onFileVirtualizedChange}
                  onHydrateFile={props.onHydrateFile}
                  diffViewMode={props.diffViewMode}
                  setExpanded={(expanded) =>
                    props.setFileExpansion((current) => ({
                      ...current,
                      [key]: expanded,
                    }))
                  }
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
  files: RenderedFileEntry[];
  tree: FileTreeNode[];
  directoryExpansion: BooleanMap;
  fileExpansion: BooleanMap;
  activeHunkFileId: string | null;
  isActiveHunkFileId: (fileId: string) => boolean;
  isFileVirtualized: (fileId: string) => boolean;
  viewMode: DiffViewMode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  setDirectoryExpansion: ExpansionSetter;
  setFileExpansion: ExpansionSetter;
  onScrollToDirectory: (directory: FileTreeDirectoryNode) => void;
  onScrollToFile: (file: FileEntry) => void;
}) {
  const lineStats = createMemo(() => filesLineStats(props.files));
  const directoryExpanded = (directory: FileTreeDirectoryNode) =>
    expansionValue(props.directoryExpansion, directory.label, true);
  const fileExpanded = (file: FileEntry) =>
    expansionValue(
      props.fileExpansion,
      fileKey(file),
      defaultFileExpansion(file),
    );

  const setDirectoryExpanded = (
    directory: FileTreeDirectoryNode,
    expanded: boolean,
  ) => {
    props.setDirectoryExpansion((current) => ({
      ...current,
      [directory.label]: expanded,
    }));
    props.setFileExpansion((current) => ({
      ...current,
      ...Object.fromEntries(
        directory.files.map((file) => [fileKey(file), expanded]),
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
    props.isActiveHunkFileId(fileElementId(fileKey(file)));
  const fileIsVirtualized = (file: FileEntry) =>
    props.isFileVirtualized(fileElementId(fileKey(file)));

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

  const renderTreeNode = (node: FileTreeNode, depth: number) => {
    if (node.type === "directory") {
      return (
        <FileTreeDirectory
          directory={node}
          depth={depth}
          expanded={directoryExpanded(node)}
          setExpanded={(expanded) => setDirectoryExpanded(node, expanded)}
          onScrollToDirectory={() => props.onScrollToDirectory(node)}
          renderNode={renderTreeNode}
        />
      );
    }
    return (
      <FileTreeFile
        file={node.file}
        name={node.name}
        depth={depth}
        expanded={fileExpanded(node.file)}
        virtualized={fileIsVirtualized(node.file)}
        active={fileIsActiveHunkFile(node.file)}
        setExpanded={(expanded) => setFileExpanded(node.file, expanded)}
        onScrollToFile={() => props.onScrollToFile(node.file)}
      />
    );
  };

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
                when={props.tree.length > 0}
                fallback={<p class="file-tree-empty">No files loaded yet.</p>}
              >
                <For each={props.tree}>{(node) => renderTreeNode(node, 0)}</For>
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

function FileTreeDirectory(props: {
  directory: FileTreeDirectoryNode;
  depth: number;
  expanded: boolean;
  setExpanded: (expanded: boolean) => void;
  onScrollToDirectory: () => void;
  renderNode: (node: FileTreeNode, depth: number) => JSX.Element;
}) {
  return (
    <section class="file-tree-group">
      <div
        class="file-tree-directory"
        style={{ "--file-tree-depth": String(props.depth) }}
      >
        <button
          type="button"
          class="file-tree-visibility-toggle"
          onClick={() => props.setExpanded(!props.expanded)}
          aria-label={
            props.expanded
              ? `Fold ${props.directory.label}`
              : `Show ${props.directory.label}`
          }
        >
          <VisibilityIndicator size="small" visible={props.expanded} />
        </button>
        <button
          type="button"
          class="file-tree-directory-target"
          onClick={props.onScrollToDirectory}
        >
          {props.directory.name}
        </button>
        <TreeLineStats stats={filesLineStats(props.directory.files)} />
      </div>
      <Show when={props.expanded}>
        <div
          class="file-tree-children"
          style={{ "--file-tree-depth": String(props.depth) }}
        >
          <For each={props.directory.entries}>
            {(node) => props.renderNode(node, props.depth + 1)}
          </For>
        </div>
      </Show>
    </section>
  );
}

function FileTreeFile(props: {
  file: RenderedFileEntry;
  name: string;
  depth: number;
  expanded: boolean;
  virtualized: boolean;
  active: boolean;
  setExpanded: (expanded: boolean) => void;
  onScrollToFile: () => void;
}) {
  const lazyReason = () => fileTreeLazyReason(props.file);
  return (
    <div
      class="file-tree-file"
      data-file-tree-file-id={fileElementId(fileKey(props.file))}
      classList={{
        added: fileKindStatus(props.file.file_kind) === "added",
        removed: fileKindStatus(props.file.file_kind) === "deleted",
        renamed: fileKindStatus(props.file.file_kind) === "renamed",
        untracked: fileKindStatus(props.file.file_kind) === "untracked",
        lazy: lazyReason() !== null,
        "lazy-error": lazyIsError(props.file.lazy),
        "lazy-generated": lazyReason() === "generated",
        "lazy-too-big": lazyReason() === "too_big",
        "active-hunk-file": props.active,
      }}
      style={{ "--file-tree-depth": String(props.depth) }}
      aria-current={props.active ? "true" : undefined}
      title={fileDisplayName(props.file)}
    >
      <button
        type="button"
        class="file-tree-visibility-toggle"
        onClick={() => props.setExpanded(!props.expanded)}
        aria-label={
          props.expanded
            ? `Fold ${fileDisplayName(props.file)}`
            : `Show ${fileDisplayName(props.file)}`
        }
      >
        <VisibilityIndicator
          size="small"
          visible={props.expanded}
          virtualized={props.virtualized}
        />
      </button>
      <button
        type="button"
        class="file-tree-file-target"
        aria-current={props.active ? "true" : undefined}
        onClick={props.onScrollToFile}
      >
        <span class="file-tree-file-name">{props.name}</span>
        <TreeLineStats stats={fileLineStats(props.file)} />
      </button>
    </div>
  );
}

function FileCard(props: {
  file: RenderedFileEntry;
  cacheId: string | null;
  hunkPosition: HunkPosition;
  diffViewMode: DiffViewMode;
  expanded: boolean;
  loading: boolean;
  error: string;
  linePin: LinePin | null;
  isForcedRichFileId: (fileId: string) => boolean;
  aggressiveFolds: boolean;
  onFileVirtualizedChange: (fileId: string, virtualized: boolean) => void;
  onHydrateFile: (file: RenderedFileEntry) => void;
  setExpanded: (expanded: boolean) => void;
}) {
  let bodyViewport: HTMLDivElement | undefined;
  const [nearViewport, setNearViewport] = createSignal(false);
  const key = () => fileKey(props.file);
  const lineStats = () => fileLineStats(props.file);
  const displayName = () => fileDisplayName(props.file);
  const hunkText = () => hunkPositionText(props.hunkPosition);
  const needsHydration = () => !fileEntryIsHydrated(props.file);
  const isPinnedFile = () =>
    props.linePin !== null && fileMatchesLinePin(props.file, props.linePin);
  const isForcedRichFile = () => props.isForcedRichFileId(fileElementId(key()));
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

  const expand = () => {
    props.setExpanded(true);
    if (!needsHydration()) {
      return;
    }
    if (props.loading) {
      return;
    }
    props.onHydrateFile(props.file);
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
                {engineWarningLabel(props.file)}
              </span>
            </Show>
            <Show when={hunkText() !== null}>
              <span class="file-card-hunks">{hunkText()}</span>
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
          <span class="delta moved">* {formatLineStat(lineStats().moved)}</span>
        </span>
      </button>
      <div
        id={fileBodyAnchorElementId(key())}
        class="file-card-scroll-target"
        aria-hidden="true"
      />
      <Show when={!props.expanded && canRenderRows()}>
        <HunkSkipAnchors
          file={props.file}
          aggressiveFolds={props.aggressiveFolds}
        />
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
                diffParams={props.file.sourceParams}
                cacheId={loadedCacheId(props.cacheId)}
                diffViewMode={props.diffViewMode}
                aggressiveFolds={props.aggressiveFolds}
              />
            </Show>
            <Show when={props.file.render_kind !== "notebook"}>
              <Show when={canRenderRows()}>
                <Show
                  when={shouldRenderRichBody()}
                  fallback={
                    <PlainSplitFileDiff
                      file={props.file}
                      aggressiveFolds={props.aggressiveFolds}
                    />
                  }
                >
                  <DiffGrid
                    displayName={displayName()}
                    leftLabel={requiredSideLabel(props.file, "left")}
                    rightLabel={requiredSideLabel(props.file, "right")}
                    rows={fileRows(props.file)}
                    foldHints={fileFoldHints(props.file)}
                    viewMode={props.diffViewMode}
                    aggressiveFolds={props.aggressiveFolds}
                    semanticReplaceRows={
                      props.file.sourceEngine === "difftastic"
                    }
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

function PlainSplitFileDiff(props: {
  file: FileEntry;
  aggressiveFolds: boolean;
}) {
  const text = () => plainSplitText(fileRows(props.file));
  const hunkAnchors = () =>
    virtualHunkAnchors(hunkRenderRows(props.file, props.aggressiveFolds));

  return (
    <div class="plain-split-diff" aria-label="Virtualized plain split diff">
      <For each={hunkAnchors()}>
        {(anchor) => (
          <span
            classList={{
              "diff-row": true,
              "hunk-anchor": true,
              "virtual-hunk-anchor": !anchor.skipped,
              "hunk-skip": anchor.skipped,
            }}
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

function HunkSkipAnchors(props: { file: FileEntry; aggressiveFolds: boolean }) {
  const hunkAnchors = () =>
    virtualHunkAnchors(hunkRenderRows(props.file, props.aggressiveFolds));

  return (
    <div class="hunk-skip-anchors" aria-hidden="true">
      <For each={hunkAnchors()}>
        {() => <span class="diff-row hunk-anchor hunk-skip" />}
      </For>
    </div>
  );
}

type VirtualHunkAnchor = {
  rowIndex: number;
  skipped: boolean;
};

function hunkRenderRows(
  file: FileEntry,
  aggressiveFolds: boolean,
): RenderRow[] {
  return addFoldRows(
    markHunkAnchors(fileRows(file)),
    fileFoldHints(file),
    aggressiveFolds,
  );
}

function virtualHunkAnchors(
  rows: RenderRow[],
  startRow = 0,
): VirtualHunkAnchor[] {
  const anchors: VirtualHunkAnchor[] = [];
  let cursor = startRow;
  for (const row of rows) {
    if (isFoldRow(row)) {
      anchors.push(
        ...virtualHunkAnchors(row.foldedRows, row.startRow).map((anchor) => ({
          ...anchor,
          skipped: true,
        })),
      );
      cursor += row.count;
      continue;
    }
    if ((row as HunkDiffRow).isHunkAnchor === true) {
      anchors.push({ rowIndex: cursor, skipped: false });
    }
    cursor += 1;
  }
  return anchors;
}

const VIRTUAL_HUNK_TOP_OFFSET_PX = 10;
const VIRTUAL_HUNK_ROW_HEIGHT_PX = 17.4;

function virtualHunkAnchorTop(rowIndex: number): number {
  // Mirrors the approximate line box used by the plain virtualized fallback.
  return VIRTUAL_HUNK_TOP_OFFSET_PX + rowIndex * VIRTUAL_HUNK_ROW_HEIGHT_PX;
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

function fileIsLoading(loadingFiles: BooleanMap, key: string) {
  const loading = loadingFiles[key];
  if (loading === undefined) {
    return false;
  }
  return loading;
}

function fileError(fileErrors: StringMap, key: string) {
  const error = fileErrors[key];
  if (error === undefined) {
    return "";
  }
  return error;
}

function loadedCacheId(cacheId: string | null): string {
  if (cacheId === null) {
    throw new Error("Notebook file rendering requires a cache id.");
  }
  return cacheId;
}

function hunkPositionText(position: HunkPosition): string | null {
  if (position.total === 0) {
    return null;
  }
  return `${position.current}/${position.total} hunks`;
}

function engineWarningMessage(file: FileEntry): string {
  const warning = file.engine_warning;
  if (warning === null || warning === undefined) {
    throw new Error(`${fileDisplayName(file)} is missing engine warning.`);
  }
  return warning.message;
}

function engineWarningLabel(file: FileEntry): string {
  const warning = file.engine_warning;
  if (warning === null || warning === undefined) {
    throw new Error(`${fileDisplayName(file)} is missing engine warning.`);
  }
  switch (warning.type) {
    case "difftastic_graph_limit":
    case "difftastic_empty_rows":
      return "Difftastic failed: unified fallback";
    case "gumtree_invalid_json":
      return "GumTree failed: unified fallback";
    default:
      return warning.type satisfies never;
  }
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
