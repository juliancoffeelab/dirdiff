/**
 * Renders the ChangeSet FileTree from canonical file state and shared expansion.
 *
 * File and directory rows preserve manifest order. Directory reachability derives
 * from the same file expansion used by FileCards, while progressive statistics
 * come from current lane states and render-mode markers come from mounted cards.
 * The tree may scroll only its own groups container to reveal the selected file.
 *
 * Expansion remains in `ChangeSet`, backend data remains in the file lane, and
 * page movement remains an explicit Navigation operation.
 */
import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
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
import {
  fileDisplayName,
  manifestEntryKey,
  manifestFilesInOrder,
  type FileState,
} from "./fileLane";
import { useNavigation } from "../navigation";

/**
 * Describes the line statistics FileTree can progressively display.
 *
 * Null means the current file state genuinely lacks that statistic. The tree
 * renders this absence explicitly and never treats it as zero.
 */
type TreeLineStats = {
  /** Added-line total, or null while any contributing file lacks that fact. */
  added: number | null;
  /** Modified-line total, or null for Husk, failure, or deferred metadata. */
  modified: number | null;
  /** Removed-line total, or null while any contributing file lacks that fact. */
  removed: number | null;
  /** Moved-line total, or null for states that expose no move calculation. */
  moved: number | null;
};

/**
 * Calculates directory expansion from current descendant file reachability.
 *
 * Explicit file expansion is authoritative. Unresolved HuskFiles remain
 * reachable so sequential loading cannot collapse and reopen the directory hierarchy, while
 * LazyFiles remain reachable for their visible plank unless explicitly collapsed.
 * Every directory receives one result, including an empty directory.
 *
 * @param nodes Immutable manifest siblings traversed in backend order.
 * @param stateForFile Called once for every visited manifest leaf with that
 * exact `ManifestFile`. It returns the leaf's current canonical lane state;
 * the calculation reads expansion defaults from the result after the callback
 * completes. The callback must not mutate the manifest or expansion map.
 * @param fileExpansion Explicit ChangeSet values keyed by manifest File pair.
 *
 * # Returns
 *
 * - Each key is one manifest directory path, including nested and empty
 *   directories. File paths do not appear as keys.
 * - Each value reports whether that directory's subtree contains a reachable
 *   File after applying explicit File expansion and lane defaults. FileTree
 *   uses the value directly as the directory's expansion state.
 */
export function calculateDirectoryExpansion(
  nodes: readonly ManifestNode[],
  stateForFile: (file: ManifestFile) => FileState,
  fileExpansion: Readonly<Record<string, boolean | undefined>>,
): ReadonlyMap<string, boolean> {
  const result = new Map<string, boolean>();

  /**
   * Visits one ordered sibling collection and reports subtree reachability.
   *
   * The traversal evaluates every child even after finding a reachable file so
   * nested directory entries are always complete in the returned map.
   */
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

/**
 * Describes FileCard-local render modes calculated solely for FileTree markers.
 *
 * Keys are immutable manifest file indices and values mirror the current
 * `data-file-render` attributes. The map is disposable presentation data: it
 * must not control virtualization, navigation, selection, or ChangeSet state.
 */
type FileTreeRenderModes = ReadonlyMap<number, "rich" | "virtual">;

/**
 * Defines all reactive presentation and expansion inputs for the private FileTree.
 *
 * The tree receives one immutable manifest, current FileCard states, the stable
 * ChangeSet DOM root, calculated directory reachability, workspace FileTree
 * visibility, and ChangeSet-owned file expansion. Its callbacks may change only
 * tree visibility or file expansion. FileTree stores no query, backend data,
 * hunk selection, navigation, or independent expansion authority.
 */
type FileTreeProps = {
  /**
   * Returns the connected ChangeSet root whose FileCards expose render modes.
   * FileTree reads it once when open content mounts; the root must remain valid
   * until that content unmounts and must contain this snapshot's stable cards.
   */
  changeSetRoot: Accessor<HTMLElement>;
  /** Immutable manifest hierarchy rendered without sorting or filtering. */
  tree: readonly ManifestNode[];
  /**
   * Returns current canonical lane states in the same manifest order as `tree`.
   * Rows call it reactively after query transitions. Callers must not mutate the
   * returned array or retain it as separate FileTree state.
   */
  states: Accessor<readonly FileState[]>;
  /** Current workspace sidebar visibility; false unmounts FileTreeContent. */
  open: boolean;
  /** Layout mode controlling whether the closed sidebar shell remains present. */
  view: DiffViewMode;
  /**
   * Returns the DOM-mirrored selected file index, or null before selection.
   * The open tree reads it for highlighting and private sidebar scrolling only;
   * hunk changes inside the same file must not create a different result.
   */
  selectedFileIndex: Accessor<number | null>;
  /**
   * Returns descendant reachability for every manifest directory.
   * FileTree calls it during rendering and highlighted-row reachability checks;
   * the map is recalculated by ChangeSet from shared canonical file state.
   */
  directoryExpansion: Accessor<ReadonlyMap<string, boolean>>;
  /**
   * Returns ChangeSet's explicit per-file expansion map without copying it.
   * File rows consult it after FullFile transitions and callbacks; absence at a
   * key delegates to `fileExpanded` rather than authoring a tree default.
   */
  fileExpansion: Accessor<Readonly<Record<string, boolean | undefined>>>;
  /**
   * Replaces workspace sidebar visibility with `open` after the tree toggle.
   * The caller stores the accepted value and returns it through `open`; private
   * row scrolling and file-name navigation never invoke this callback.
   */
  onOpenChange: (open: boolean) => void;
  /**
   * Applies one expansion value to every descendant file of `directory`.
   *
   * FileTree invokes it only from that directory's square, before Solid reruns
   * reachability. The caller writes the descendant set into its shared
   * expansion store and returns the result through `fileExpansion`.
   *
   * @param directory Exact immutable manifest directory whose descendants change.
   * @param expanded Complete desired value for every descendant file.
   */
  onDirectoryExpandedChange: (
    directory: ManifestDirectory,
    expanded: boolean,
  ) => void;
  /**
   * Replaces one FullFile's explicit expansion after its square is activated.
   *
   * The caller stores `expanded` under `file`'s pair key and returns the result
   * through `fileExpansion`. Husk and Lazy markers never invoke this callback.
   *
   * @param file Exact immutable manifest leaf selected in the tree.
   * @param expanded Complete desired FileCard expansion value.
   */
  onFileExpandedChange: (file: ManifestFile, expanded: boolean) => void;
};

/**
 * Renders the manifest tree, current shared expansion, and private highlighted-row scroll.
 *
 * Directory squares bulk-update descendant file expansion and FullFile squares
 * update one file. Name buttons invoke the enclosing scroll-only FileTree
 * Navigation operation. The component may calculate current FileCard render
 * modes and scroll its own
 * `.file-tree-groups`, but it never changes hunk selection, loads files, expands
 * a row for visibility, or moves the main page.
 */
export function FileTree(props: FileTreeProps): JSX.Element {
  const navigation = useNavigation();
  const toast = useToasts();
  const files = manifestFilesInOrder(props.tree);
  const indexByKey = new Map(
    files.map((file, index) => [manifestEntryKey(file.entry), index]),
  );

  /**
   * Resolves one manifest file to its required manifest-order file index.
   *
   * FileTree highlighting and progressive state lookup share this exact index.
   * Missing identity violates the immutable manifest ordering and throws.
   */
  const indexForFile = (file: ManifestFile): number => {
    return expect(
      indexByKey.get(manifestEntryKey(file.entry)),
      `FileTree cannot index ${fileDisplayName(file.entry)}.`,
    );
  };

  /**
   * Sends one manifest file to the enclosing scroll-only Navigation operation.
   *
   * File and directory name buttons share this path. Rejection becomes the
   * ordinary dramatic Toast while Navigation remains the only code that moves
   * the main page; this function never selects, expands, collapses, or fetches.
   */
  function navigateToFile(file: ManifestFile): void {
    void navigation
      .navigate({ kind: "file", fileIndex: indexForFile(file) })
      .catch((error: unknown) =>
        toast.showError("File navigation failed", error),
      );
  }
  /**
   * Resolves one manifest file to the exact shared ChangeSet file state.
   *
   * Missing indices or states violate the required parallel manifest/state
   * ordering and throw rather than producing an incomplete tree row.
   */
  const stateForFile = (file: ManifestFile): FileState => {
    const index = indexForFile(file);
    return expect(
      props.states()[index],
      `FileTree is missing state for ${fileDisplayName(file.entry)}.`,
    );
  };

  const ancestorPathsByFileIndex = new Map<number, readonly string[]>();

  /**
   * Indexes the immutable directory chain containing every manifest file.
   *
   * Paths retain outermost-to-innermost order. The private sidebar-scroll effect
   * uses them only to distinguish a legitimately absent collapsed row from a
   * missing row that violates the manifest-rendering contract.
   *
   * @param nodes Immutable sibling collection at the current directory depth.
   * @param ancestors Outer-to-inner paths already traversed before `nodes`.
   */
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

  /**
   * Renders one reactive directory row and its currently expanded descendants.
   *
   * The square is the sole expansion button and invokes the shared ChangeSet
   * bulk file action. The separate name button navigates to the directory's
   * first manifest file without selecting, loading, or changing expansion, and
   * remains disabled while that first file is a Husk because a Husk (and most
   * importantly adjacent Husks) does not have stable layout.
   */
  function FileTreeDirectory(rowProps: {
    /** Immutable directory rendered by this recursive row instance. */
    directory: ManifestDirectory;
    /** Zero-based nesting level written into the row's CSS depth variable. */
    depth: number;
    /**
     * Reads the current disposable FileCard DOM-mode map for descendant markers.
     * The directory forwards the same accessor unchanged; it never samples or
     * stores a second map and never uses it to choose renderer state.
     */
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    /**
     * Reads the one shared directory-expansion value used by tree and FileCards.
     *
     * An absent entry means initially expanded. The accessor never writes a
     * default into ChangeSet state or retains a second directory authority.
     */
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
                <FileTreeNode
                  node={child}
                  depth={rowProps.depth + 1}
                  renderModes={rowProps.renderModes}
                />
              )}
            </For>
          </div>
        </Show>
      </section>
    );
  }

  /**
   * Renders one file row from current FileCard and ChangeSet presentation.
   *
   * The row exposes selected-file highlighting and current statistics. A
   * FullFile square invokes the shared file-expansion action; Husk and Lazy
   * markers remain inert. The separate name button invokes scroll-only file
   * navigation and remains disabled while this file is a Husk because a Husk
   * (and most importantly adjacent Husks) does not have stable layout. An
   * expanded FullFile in virtual DOM render mode must display `V` instead of the
   * filled visibility marker.
   */
  function FileTreeFile(rowProps: {
    /** Immutable manifest leaf whose canonical state this row reads. */
    file: ManifestFile;
    /** Zero-based nesting level written into the row's CSS depth variable. */
    depth: number;
    /**
     * Reads current FileCard DOM modes when calculating this row's V marker.
     * Attribute observation replaces the map after renderer transitions; the
     * row must not write it or use it to navigate or expand the FileCard.
     */
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    const fileIndex = indexForFile(rowProps.file);
    /**
     * Reads the current FileCard presentation at this immutable manifest index.
     *
     * The accessor preserves ChangeSet's canonical state ordering and must not
     * cache a Husk, Lazy, or Full result across reactive query transitions.
     */
    const state = () => stateForFile(rowProps.file);
    /**
     * Calculates the marker's current rich-body visibility.
     *
     * Husk and Lazy rows always display an empty FileTree marker. FullFile reads
     * the shared expansion authority; this calculation never changes FileCard.
     */
    const expanded = () => {
      const current = state();
      if (current.state !== "full") {
        return false;
      }
      return fileExpanded(rowProps.file, current, props.fileExpansion());
    };
    /**
     * Reports whether an expanded FullFile currently exposes virtual DOM.
     *
     * The value comes only from FileTree's disposable DOM calculation and must
     * not be used to choose or change the owning FileCard's render mode.
     */
    const virtualized = () =>
      expanded() && rowProps.renderModes().get(fileIndex) === "virtual";
    const highlighted = createMemo(
      () => props.selectedFileIndex() === fileIndex,
    );
    const fileKind = rowProps.file.entry.file_kind;
    // This IIFE exists so TypeScript infers the exhaustive switch's result union.
    const fileStatus = (() => {
      switch (fileKind.type) {
        case "git":
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
    /**
     * Reports the localized error presentation that must override reason colors.
     *
     * Ordinary Husk and Full states are never error-flavoured by this accessor.
     */
    const isError = () => {
      const current = state();
      return current.state === "lazy" && current.file.kind === "error";
    };
    /**
     * Preserves LazyFile's established border until explicit fetching completes.
     *
     * Manifest-lazy files temporarily render as Husk while their query fetches;
     * they remain visually Lazy in FileTree. A hydrated FullFile deliberately
     * drops the Lazy border while retaining only its approved reason color.
     */
    const styledAsLazy = () => {
      const current = state();
      return (
        current.state === "lazy" ||
        (current.state === "husk" && rowProps.file.entry.lazy !== null)
      );
    };
    /**
     * Resolves the non-error Lazy reason whose color survives FullFile hydration.
     *
     * Error-flavoured LazyFile deliberately returns null so critical error color
     * wins. Full and fetching states fall back to immutable manifest metadata.
     */
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

  /**
   * Dispatches one immutable manifest node to its reactive row component.
   *
   * Recursion preserves exact backend order and directory depth. The dispatcher
   * stores no state and exists only as the structural boundary shared by the root
   * and nested directory lists.
   */
  function FileTreeNode(nodeProps: {
    /** Immutable manifest node dispatched without changing backend order. */
    node: ManifestNode;
    /** Current recursive nesting level passed unchanged to the row component. */
    depth: number;
    /**
     * Reads the shared DOM mode map used by every recursive row in this mount.
     * The dispatcher forwards the accessor without calling it, preserving one
     * reactive observation point in the concrete file row.
     */
    renderModes: Accessor<FileTreeRenderModes>;
  }): JSX.Element {
    if (nodeProps.node.type === "directory") {
      return (
        <FileTreeDirectory
          directory={nodeProps.node}
          depth={nodeProps.depth}
          renderModes={nodeProps.renderModes}
        />
      );
    }
    return (
      <FileTreeFile
        file={nodeProps.node}
        depth={nodeProps.depth}
        renderModes={nodeProps.renderModes}
      />
    );
  }

  /**
   * Maintains the open FileTree DOM calculation and private highlighted-row scroll.
   *
   * Mounting scans the authoritative stable FileCards and observes only local
   * render-mode attribute changes. Disposal disconnects the observer and drops
   * the map. The selection effect changes only this component's scroll container
   * and treats rows below collapsed ancestors as legitimately absent.
   */
  function FileTreeContent(): JSX.Element {
    let groups!: HTMLDivElement;
    const [renderModes, setRenderModes] = createSignal<FileTreeRenderModes>(
      new Map(),
    );
    const highlightedFileIndex = createMemo(() => props.selectedFileIndex());

    /**
     * Reports whether the highlighted row currently has every ancestor mounted.
     *
     * This memo deliberately reduces the complete directory-expansion map to
     * one boolean for the highlighted file. Unrelated Husk-to-Full state changes
     * may replace that map, but an unchanged boolean must not make the private
     * scrolling effect overwrite the user's manual FileTree scroll position.
     */
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

    // Render mode is exposed by stable FileCard DOM rather than Solid state, so
    // the open FileTree needs one MutationObserver to mirror only
    // `data-file-render` for its V markers. It scans once after mount, then
    // replaces the disposable map after matching attribute changes. The
    // observer lives only while FileTreeContent is mounted and cleanup always
    // disconnects it; a malformed DOM value also disconnects before Toasting.
    onMount(() => {
      const root = props.changeSetRoot();
      assert(root.isConnected, "FileTree requires a mounted ChangeSet root.");

      /**
       * Reads current FullFile render modes from authoritative stable FileCards.
       *
       * Missing mode attributes are valid for Husk, Lazy, and failed renderer
       * states. Present attributes and indices must be exact; malformed or
       * duplicate values are DOM-contract violations and throw immediately.
       */
      function readRenderModes(): FileTreeRenderModes {
        const modes = new Map<number, "rich" | "virtual">();
        for (const card of root.querySelectorAll<HTMLElement>(
          "[data-file-card][data-file-index]",
        )) {
          const mode = card.dataset.fileRender;
          if (mode === undefined) {
            continue;
          }
          assert(
            mode === "rich" || mode === "virtual",
            `FileCard exposed invalid render mode ${mode}.`,
          );
          const indexText = card.dataset.fileIndex;
          assert(
            indexText !== undefined && /^\d+$/.test(indexText),
            "FileCard exposed an invalid manifest index.",
          );
          const fileIndex = Number(indexText);
          assert(
            fileIndex < files.length,
            "FileCard render mode is outside the manifest.",
          );
          assert(
            !modes.has(fileIndex),
            "FileTree found duplicate stable FileCards.",
          );
          modes.set(fileIndex, mode);
        }
        return modes;
      }

      setRenderModes(readRenderModes());
      const observer = new MutationObserver(() => {
        try {
          setRenderModes(readRenderModes());
        } catch (error) {
          observer.disconnect();
          toast.showError(
            "Could not update file tree render modes",
            error instanceof Error
              ? error
              : new Error(
                  "FileTree render-mode calculation threw a non-Error value.",
                ),
          );
        }
      });
      observer.observe(root, {
        subtree: true,
        attributes: true,
        attributeFilter: ["data-file-render"],
      });
      onCleanup(() => observer.disconnect());
    });

    // This effect exists only for mounted, open FileTreeContent. Its memos keep
    // same-file hunk and unrelated directory-state changes from rerunning it;
    // selected-ancestor reachability changing to true reveals the remounted row.
    // It changes only `.file-tree-groups.scrollTop` and dies when the open
    // content unmounts.
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
            {(node) => (
              <FileTreeNode node={node} depth={0} renderModes={renderModes} />
            )}
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

/**
 * Resolves one file's expansion from explicit ChangeSet state or FullFile data.
 *
 * Explicit user state always wins. LazyFiles begin expanded because their body
 * contains the only explicit-load affordance. A first FullFile result supplies
 * its backend default expansion; queued HuskFiles remain collapsed.
 *
 * @param file Immutable manifest leaf providing the expansion key.
 * @param state Current canonical lane state for that same leaf.
 * @param expansion ChangeSet's explicit choices, where absence delegates.
 */
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
    return true;
  }
  if (state.state === "full") {
    return state.backend_data.default_expanded;
  }
  return false;
}

/**
 * Derives FileTree line statistics from one shared FileCard state.
 *
 * Full and deferred values expose only their actual backend fields. Husk and
 * error states remain unknown rather than manufacturing zeros.
 */
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

/**
 * Adds progressive FileTree statistics without converting unknowns to numbers.
 *
 * Each metric is null when any participating file lacks it; otherwise exact
 * values are summed. The aggregate is presentation-only and never cached.
 */
function sumTreeStatistics(states: readonly FileState[]): TreeLineStats {
  const stats = states.map(treeStatistics);
  /**
   * Sums one statistic only when every contributing file knows its value.
   *
   * Null propagates as unknown instead of becoming zero, preserving progressive
   * FileTree semantics for queued and failed files.
   *
   * # Returns
   *
   * - The sum when every participating File supplies this statistic.
   * - `null`: At least one File lacks the statistic. The directory row must
   *   display the aggregate as unknown rather than as a partial total.
   */
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

/**
 * Renders the four established progressive statistics in one FileTree row.
 *
 * Unknown values remain question marks in the tree only. File headers use their
 * own stricter omission rules and do not call this component.
 */
function TreeStatistics(props: {
  /** Final progressive values for this exact file or directory row. */
  stats: TreeLineStats;
}): JSX.Element {
  return (
    <span class="file-tree-line-stats">
      <span class="added">+ {props.stats.added ?? "?"}</span>
      <span class="changed">~ {props.stats.modified ?? "?"}</span>
      <span class="removed">- {props.stats.removed ?? "?"}</span>
      <span class="moved">* {props.stats.moved ?? "?"}</span>
    </span>
  );
}

/**
 * Renders one inert FileTree expansion or local virtualization marker.
 *
 * `visible` produces the established filled square and `virtualized` produces
 * `V`. Callers must not set both. The marker has no interaction, accessible
 * name, expansion state, or virtualization decision.
 */
function TreeVisibilityIndicator(props: {
  /** True paints the rich expanded marker; `virtualized` must then be false. */
  visible: boolean;
  /** True paints V for expanded virtual DOM; `visible` must then be false. */
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
