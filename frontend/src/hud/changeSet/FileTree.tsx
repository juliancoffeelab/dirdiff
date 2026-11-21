import {
  For,
  Show,
  createEffect,
  createMemo,
  type Accessor,
  type JSX,
} from "solid-js";
import type {
  ManifestDirectory,
  ManifestFile,
  ManifestNode,
} from "../../api/api";
import { useToasts } from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { DiffViewMode } from "../App";
import type { BayRenderMode } from "../fileCard/FileCard";
import {
  fileDisplayName,
  manifestEntryKey,
  manifestFilesInOrder,
  type FileState,
} from "./fileLane";
import { useNavigation } from "../navigation";

type TreeLineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
  moved: number | null;
};

export function calculateDirectoryExpansion(
  nodes: readonly ManifestNode[],
  stateForFile: (file: ManifestFile) => FileState,
  fileExpansion: Readonly<Record<string, boolean | undefined>>,
): ReadonlyMap<string, boolean> {
  const result = new Map<string, boolean>();

  function visit(children: readonly ManifestNode[]): boolean {
    let hasReachableFile = false;
    for (const child of children) {
      let childIsReachable: boolean;
      if (child.type === "file") {
        const explicit = fileExpansion[manifestEntryKey(child.entry)];
        if (explicit !== undefined) {
          childIsReachable = explicit;
        } else {
          const state = stateForFile(child);
          childIsReachable =
            state.state === "husk" ||
            state.state === "lazy" ||
            state.backend_data.default_expanded;
        }
      } else {
        childIsReachable = visit(child.entries);
        result.set(child.path, childIsReachable);
      }
      hasReachableFile = childIsReachable || hasReachableFile;
    }
    return hasReachableFile;
  }

  visit(nodes);
  return result;
}

type FileTreeProps = {
  tree: readonly ManifestNode[];
  states: Accessor<readonly FileState[]>;
  open: boolean;
  view: DiffViewMode;
  selectedFileIndex: Accessor<number | null>;
  directoryExpansion: Accessor<ReadonlyMap<string, boolean>>;
  fileExpansion: Accessor<Readonly<Record<string, boolean | undefined>>>;
  renderMode: (fileIndex: number) => BayRenderMode | null;
  onOpenChange: (open: boolean) => void;
  onDirectoryExpandedChange: (
    directory: ManifestDirectory,
    expanded: boolean,
  ) => void;
  onFileExpandedChange: (file: ManifestFile, expanded: boolean) => void;
};

export function FileTree(props: FileTreeProps): JSX.Element {
  const navigation = useNavigation();
  const toast = useToasts();
  const files = manifestFilesInOrder(props.tree);
  const indexByKey = new Map(
    files.map((file, index) => [manifestEntryKey(file.entry), index]),
  );

  const indexForFile = (file: ManifestFile): number => {
    return expect(
      indexByKey.get(manifestEntryKey(file.entry)),
      `FileTree cannot index ${fileDisplayName(file.entry)}.`,
    );
  };

  function navigateToFile(file: ManifestFile): void {
    void navigation
      .navigate({ kind: "file", fileIndex: indexForFile(file) })
      .catch((error: unknown) =>
        toast.showError("File navigation failed", error),
      );
  }
  const stateForFile = (file: ManifestFile): FileState => {
    const index = indexForFile(file);
    return expect(
      props.states()[index],
      `FileTree is missing state for ${fileDisplayName(file.entry)}.`,
    );
  };

  const ancestorPathsByFileIndex = new Map<number, readonly string[]>();

  function indexAncestorPaths(
    nodes: readonly ManifestNode[],
    ancestors: readonly string[],
  ): void {
    for (const node of nodes) {
      if (node.type === "file") {
        ancestorPathsByFileIndex.set(indexForFile(node), ancestors);
        continue;
      }
      indexAncestorPaths(node.entries, [...ancestors, node.path]);
    }
  }
  indexAncestorPaths(props.tree, []);

  function FileTreeDirectory(rowProps: {
    directory: ManifestDirectory;
    depth: number;
  }): JSX.Element {
    const expanded = () => {
      const current = props.directoryExpansion().get(rowProps.directory.path);
      return expect(
        current,
        `FileTree is missing reachability for ${rowProps.directory.path}.`,
      );
    };
    const directoryFiles = manifestFilesInOrder(rowProps.directory.entries);
    const firstFile = expect(
      directoryFiles[0],
      `FileTree directory ${rowProps.directory.path} contains no files.`,
    );
    const statistics = createMemo(() =>
      sumTreeStatistics(directoryFiles.map(stateForFile)),
    );
    return (
      <section class="file-tree-group">
        <div
          class="file-tree-directory"
          style={{ "--file-tree-depth": String(rowProps.depth) }}
        >
          <button
            type="button"
            class="file-tree-visibility-control"
            aria-expanded={expanded()}
            aria-label={
              expanded()
                ? `Collapse ${rowProps.directory.path}`
                : `Expand ${rowProps.directory.path}`
            }
            onClick={() =>
              props.onDirectoryExpandedChange(rowProps.directory, !expanded())
            }
          >
            <TreeVisibilityIndicator visible={expanded()} virtualized={false} />
          </button>
          <button
            type="button"
            class="file-tree-directory-target"
            aria-label={`Go to first file in ${rowProps.directory.path}`}
            // Adjacent Husks have no stable layout to navigate into.
            disabled={stateForFile(firstFile).state === "husk"}
            onClick={() => navigateToFile(firstFile)}
          >
            {rowProps.directory.name}/
          </button>
          <TreeStatistics stats={statistics()} />
        </div>
        <Show when={expanded()}>
          <div
            class="file-tree-children"
            style={{ "--file-tree-depth": String(rowProps.depth) }}
          >
            <For each={rowProps.directory.entries}>
              {(child) => (
                <FileTreeNode node={child} depth={rowProps.depth + 1} />
              )}
            </For>
          </div>
        </Show>
      </section>
    );
  }

  function FileTreeFile(rowProps: {
    file: ManifestFile;
    depth: number;
  }): JSX.Element {
    const fileIndex = indexForFile(rowProps.file);
    const state = () => stateForFile(rowProps.file);
    const expanded = () => {
      const current = state();
      if (current.state !== "full") {
        return false;
      }
      return fileExpanded(rowProps.file, current, props.fileExpansion());
    };
    const virtualized = () =>
      expanded() && props.renderMode(fileIndex) === "virtual";
    const highlighted = createMemo(
      () => props.selectedFileIndex() === fileIndex,
    );
    const fileKind = rowProps.file.entry.file_kind;
    // This IIFE exists so TypeScript infers the exhaustive switch's result
    // union.
    const fileStatus = (() => {
      switch (fileKind.type) {
        case "tracked":
          return fileKind.status;
        case "untracked":
          return "untracked";
        default: {
          const unsupported: never = fileKind;
          throw new Error(
            `Unsupported file kind ${JSON.stringify(unsupported)}.`,
          );
        }
      }
    })();
    const isError = () => {
      const current = state();
      return current.state === "lazy" && current.file.kind === "error";
    };
    // Manifest-lazy files render as Husk while their explicit fetch runs but
    // keep Lazy styling.
    const styledAsLazy = () => {
      const current = state();
      return (
        current.state === "lazy" ||
        (current.state === "husk" && rowProps.file.entry.lazy !== null)
      );
    };
    const lazyReason = () => {
      const current = state();
      if (current.state === "lazy" && current.file.kind === "error") {
        return null;
      }
      if (current.state === "lazy" && current.file.kind === "deferred") {
        return current.file.info.lazy;
      }
      return rowProps.file.entry.lazy;
    };
    return (
      <div
        class="file-tree-file"
        data-file-tree-index={fileIndex}
        aria-current={highlighted() ? "true" : undefined}
        classList={{
          "active-hunk-file": highlighted(),
          added: fileStatus === "added",
          removed: fileStatus === "deleted",
          renamed: fileStatus === "renamed",
          untracked: fileStatus === "untracked",
          lazy: styledAsLazy(),
          "lazy-error": isError(),
          "lazy-generated": lazyReason() === "generated",
          "lazy-too-big": lazyReason() === "too_big",
        }}
        style={{ "--file-tree-depth": String(rowProps.depth) }}
        title={fileDisplayName(rowProps.file.entry)}
      >
        <Show
          when={state().state === "full"}
          fallback={
            <span class="file-tree-visibility-marker">
              <TreeVisibilityIndicator visible={false} virtualized={false} />
            </span>
          }
        >
          <button
            type="button"
            class="file-tree-visibility-control"
            aria-expanded={expanded()}
            aria-label={
              expanded()
                ? `Collapse ${fileDisplayName(rowProps.file.entry)}`
                : `Expand ${fileDisplayName(rowProps.file.entry)}`
            }
            onClick={() =>
              props.onFileExpandedChange(rowProps.file, !expanded())
            }
          >
            <TreeVisibilityIndicator
              visible={expanded() && !virtualized()}
              virtualized={virtualized()}
            />
          </button>
        </Show>
        <button
          type="button"
          class="file-tree-file-target"
          aria-label={`Go to ${fileDisplayName(rowProps.file.entry)}`}
          disabled={state().state === "husk"}
          onClick={() => navigateToFile(rowProps.file)}
        >
          <span class="file-tree-file-name">{rowProps.file.name}</span>
          <TreeStatistics stats={treeStatistics(state())} />
        </button>
      </div>
    );
  }

  function FileTreeNode(nodeProps: {
    node: ManifestNode;
    depth: number;
  }): JSX.Element {
    if (nodeProps.node.type === "directory") {
      return (
        <FileTreeDirectory directory={nodeProps.node} depth={nodeProps.depth} />
      );
    }
    return <FileTreeFile file={nodeProps.node} depth={nodeProps.depth} />;
  }

  function FileTreeContent(): JSX.Element {
    let groups!: HTMLDivElement;
    const highlightedFileIndex = createMemo(() => props.selectedFileIndex());

    const highlightedRowReachable = createMemo(() => {
      const fileIndex = highlightedFileIndex();
      if (fileIndex === null) {
        return false;
      }
      assert(
        Number.isInteger(fileIndex) && fileIndex >= 0,
        "Selected hunk display has an invalid file index.",
      );
      const ancestorPaths = expect(
        ancestorPathsByFileIndex.get(fileIndex),
        "FileTree is missing the selected manifest file.",
      );
      const expansion = props.directoryExpansion();
      for (const path of ancestorPaths) {
        const expanded = expect(
          expansion.get(path),
          `FileTree is missing reachability for ${path}.`,
        );
        if (!expanded) {
          return false;
        }
      }
      return true;
    });

    // The memos keep same-file hunk and unrelated directory-state changes from
    // rerunning this, which would overwrite the user's manual scroll.
    createEffect(() => {
      if (!props.open) {
        return;
      }
      const fileIndex = highlightedFileIndex();
      if (fileIndex === null) {
        return;
      }
      if (!highlightedRowReachable()) {
        return;
      }
      const row = expect(
        groups.querySelector<HTMLElement>(
          `[data-file-tree-index="${fileIndex}"]`,
        ),
        "FileTree did not render the selected manifest file.",
      );
      const containerRect = groups.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      if (rowRect.top < containerRect.top) {
        groups.scrollTop -= containerRect.top - rowRect.top;
      } else if (rowRect.bottom > containerRect.bottom) {
        groups.scrollTop += rowRect.bottom - containerRect.bottom;
      }
    });

    return (
      <aside
        id="fileTreeSidebar"
        class="file-tree-sidebar"
        aria-label="Changed file tree"
      >
        <div ref={groups} class="file-tree-groups">
          <For each={props.tree}>
            {(node) => <FileTreeNode node={node} depth={0} />}
          </For>
        </div>
      </aside>
    );
  }

  return (
    <Show when={files.length > 0 || props.view === "inline"}>
      <div
        class="file-tree-shell"
        classList={{
          open: props.open,
          "file-tree-shell-inline": props.view === "inline",
        }}
      >
        <Show when={props.open}>
          <FileTreeContent />
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
          <span class="file-tree-label">Files</span>
          <Show when={props.open}>
            <TreeStatistics stats={sumTreeStatistics(props.states())} />
          </Show>
          <kbd>t</kbd>
        </button>
      </div>
    </Show>
  );
}

export function fileExpanded(
  file: ManifestFile,
  state: FileState,
  expansion: Readonly<Record<string, boolean | undefined>>,
): boolean {
  const selected = expansion[manifestEntryKey(file.entry)];
  if (selected !== undefined) {
    return selected;
  }
  if (state.state === "lazy") {
    // Its body holds the only explicit-load affordance.
    return true;
  }
  if (state.state === "full") {
    return state.backend_data.default_expanded;
  }
  return false;
}

function treeStatistics(state: FileState): TreeLineStats {
  if (state.state === "full") {
    return {
      added: state.backend_data.summary.added_lines,
      modified: state.backend_data.summary.modified_lines,
      removed: state.backend_data.summary.removed_lines,
      moved: state.backend_data.summary.moved_lines,
    };
  }
  if (state.state === "lazy" && state.file.kind === "deferred") {
    return {
      added: state.file.info.added_lines,
      modified: null,
      removed: state.file.info.removed_lines,
      moved: null,
    };
  }
  return { added: null, modified: null, removed: null, moved: null };
}

function sumTreeStatistics(states: readonly FileState[]): TreeLineStats {
  const stats = states.map(treeStatistics);
  const sum = (values: (number | null)[]): number | null => {
    let total = 0;
    for (const value of values) {
      if (value === null) {
        return null;
      }
      total += value;
    }
    return total;
  };
  return {
    added: sum(stats.map((value) => value.added)),
    modified: sum(stats.map((value) => value.modified)),
    removed: sum(stats.map((value) => value.removed)),
    moved: sum(stats.map((value) => value.moved)),
  };
}

function TreeStatistics(props: { stats: TreeLineStats }): JSX.Element {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {props.stats.added ?? "?"}</span>
      <span class="changed">~ {props.stats.modified ?? "?"}</span>
      <span class="removed">- {props.stats.removed ?? "?"}</span>
      <span class="moved">* {props.stats.moved ?? "?"}</span>
    </span>
  );
}

function TreeVisibilityIndicator(props: {
  visible: boolean;
  virtualized: boolean;
}): JSX.Element {
  const virtualized = createMemo(() => {
    assert(
      !(props.visible && props.virtualized),
      "FileTree marker cannot be both rich and virtual.",
    );
    return props.virtualized;
  });
  return (
    <span
      class="visibility-indicator small"
      classList={{
        visible: props.visible,
        virtualized: virtualized(),
      }}
      aria-hidden="true"
    >
      {virtualized() ? "V" : ""}
    </span>
  );
}
