/**
 * Implements one selected ChangeSet's backend observation and presentation.
 *
 * The module exports ChangeSet. The lightweight outer ChangeSet stores file
 * expansion, local Help state, and local History visibility while receiving
 * workspace-wide FileTree and DebugHud visibility. Active ChangeSetContent
 * observes the manifest, while ChangeSetSnapshot owns the profile-preference
 * observer, resolves the URL line pin, and creates its one file lane
 * (`fileLane.ts`), which owns the lazy-info and file queries and every
 * canonical file state. The mounted frame — root DOM, hotkeys, display
 * mirror, and overlays — is `Shell.tsx`'s ChangeSetShell; the sidebar is
 * `FileTree.tsx`. This module renders Portals, title, and FileCards inside
 * that frame. It must not copy backend results into Solid state, start
 * file-diff requests outside the lane, store workspace or Tab selections, or
 * follow user scrolling.
 * Line-pin URL identity and decoration remain in LinePins; the file lane
 * accepts only its resolved target index and restoration gate.
 */
import {
  For,
  Show,
  batch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  type Accessor,
  type JSX,
} from "solid-js";
import { createStore, type SetStoreFunction } from "solid-js/store";
import { Portal } from "solid-js/web";
import {
  createQueries,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { CircleAlert, Clock3, LoaderCircle } from "lucide-solid";
import {
  api,
  type DiffEngine,
  type DiffParams,
  type Manifest,
  type ManifestFile,
  type ManifestSummary,
  type ThreadCodeLocation,
} from "../../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
  useToasts,
} from "../../comp/Toasts";
import { assert, expect } from "../../utils";
import type { DiffViewMode } from "../App";
import type { AppHeaderOutlets } from "../AppHeader";
import { FileCard } from "../FileCard";
import {
  createFileLane,
  fileDisplayName,
  manifestEntryKey,
  manifestFilesInOrder,
  type FileLaneActivity,
  type FileLaneLineTarget,
  type FileState,
} from "../fileLane";
import { linePins } from "../linePins";
import { useNavigation } from "../navigation";
import type { StoredProfile } from "../Profile";
import { ReviewProvider, type ReviewCodeAnchor } from "../Review";
import {
  calculateDirectoryExpansion,
  fileExpanded,
  FileTree,
} from "./FileTree";
import { ChangeSetShell, type HunkDisplay } from "./Shell";

/**
 * Defines every complete input needed to identify and activate one ChangeSet.
 *
 * `params` is one immutable selected Tab value. `engine` selects file rendering
 * without participating in manifest identity. View, FileTree visibility, and
 * DebugHud visibility are global reactive workspace inputs; `profile` is genuine
 * nullable profile identity; and `active` controls expensive observation. Required
 * callbacks report direct workspace actions. No field represents live control input.
 */
type ChangeSetProps = {
  active: boolean;
  params: DiffParams;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
};

/**
 * Contains ChangeSet-local client state that survives inactive Tab periods.
 *
 * Expansion keys are manifest paths. Workspace-wide FileTree and DebugHud
 * visibility, backend files, query state, progress, hunk selection, and renderer
 * rows are deliberately excluded from this store.
 */
type ChangeSetState = {
  fileExpansion: Record<string, boolean | undefined>;
};

/**
 * Describes the complete compact state of one ChangeSet file lane.
 *
 * Processed and total count only automatic non-lazy entries. Failure count is
 * derived from current file presentations so a successful explicit retry clears
 * it without maintaining another error store.
 */
type FileSequenceState =
  | {
      state: "loading";
      processed: number;
      automaticTotal: number;
      failed: number;
      active: FileLaneActivity;
    }
  | {
      state: "ready";
      processed: number;
      automaticTotal: number;
      failed: number;
    };

/**
 * Establishes one stable ChangeSet lifetime for a Tab's selected parameters.
 *
 * Callers keep this boundary mounted across Tab switches and global view/engine
 * changes. Only active content observes queries and renders expensive file DOM;
 * file expansion, local Help state, and History visibility survive inactive
 * periods. Workspace-wide FileTree and DebugHud visibility survive switching
 * Tabs or selected ChangeSets.
 */
export function ChangeSet(props: ChangeSetProps): JSX.Element {
  const [helpOpen, setHelpOpen] = createSignal(false);
  const [historyOpen, setHistoryOpen] = createSignal(false);
  const [state, setState] = createStore<ChangeSetState>({
    fileExpansion: {},
  });
  return (
    <Show when={props.active ? props.params : null} keyed>
      {(activeParams) => (
        <UnexpectedErrorBoundary
          title="Could not render ChangeSet"
          retryOnR={true}
        >
          <ChangeSetContent
            params={activeParams}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.profile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            historyOpen={historyOpen()}
            onHistoryOpenChange={setHistoryOpen}
            helpOpen={helpOpen()}
            onHelpOpenChange={setHelpOpen}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
            state={state}
            setState={setState}
          />
        </UnexpectedErrorBoundary>
      )}
    </Show>
  );
}

/**
 * Defines the complete inputs for one active ChangeSet content lifetime.
 *
 * ChangeSetContent observes the manifest, mounts direct hotkeys and HUD presentation,
 * and performs external async work. It is disposed whenever the Tab hides; durable
 * client and HUD state remain in the outer ChangeSet.
 */
type ChangeSetContentProps = {
  params: DiffParams;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  onToggleView: () => void;
  historyOpen: boolean;
  onHistoryOpenChange: (open: boolean) => void;
  helpOpen: boolean;
  onHelpOpenChange: (open: boolean) => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
};

/**
 * Observes one active manifest and replaces its complete rendered snapshot.
 *
 * The component exists only for one immutable DiffParams value. It mounts no
 * manifest-dependent observer before manifest success, disposes the current
 * snapshot before reload and never retains an old manifest merely because
 * TanStack still exposes its previous data.
 */
function ChangeSetContent(props: ChangeSetContentProps): JSX.Element {
  const [replacingSnapshot, setReplacingSnapshot] = createSignal(false);
  const manifest = createQuery(() => api.changeSet.manifest(props.params));
  const visibleManifest = createMemo(() => {
    if (replacingSnapshot() || manifest.isError) {
      return undefined;
    }
    return manifest.data;
  });
  let stopFileSequence: (() => Promise<void>) | null = null;
  let replacement: Promise<void> | null = null;

  /**
   * Replaces the complete manifest-dependent lifetime through one refetch.
   *
   * Calling the registered stop operation synchronously closes the current lane.
   * The keyed snapshot is then disposed before cancellation settles and before
   * the manifest refetch begins. Concurrent reload actions share this exact
   * operation and cannot start parallel replacement manifests.
   */
  async function reloadSnapshot(): Promise<void> {
    if (replacement !== null) {
      await replacement;
      return;
    }
    const stopPromise =
      stopFileSequence === null ? Promise.resolve() : stopFileSequence();
    setReplacingSnapshot(true);
    props.setState("fileExpansion", {});
    const currentReplacement = (async () => {
      await stopPromise;
      await manifest.refetch({ cancelRefetch: false });
    })();
    replacement = currentReplacement;
    try {
      await currentReplacement;
    } finally {
      if (replacement === currentReplacement) {
        replacement = null;
        setReplacingSnapshot(false);
      }
    }
  }

  return (
    <>
      <Show
        when={visibleManifest()}
        keyed
        fallback={
          <ChangeSetShell
            helpOpen={props.helpOpen}
            debugOpen={props.debugHudOpen}
            onToggleTree={() => props.onFileTreeOpenChange(!props.fileTreeOpen)}
            onToggleView={props.onToggleView}
            onToggleHistory={() =>
              props.onHistoryOpenChange(!props.historyOpen)
            }
            onReload={() => {
              // Reload replaces the snapshot and resets only its file expansion.
              void reloadSnapshot();
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() =>
              props.onDebugHudOpenChange(!props.debugHudOpen)
            }
          >
            {() => (
              <>
                <Show when={manifest.isPending || replacingSnapshot()}>
                  <p class="status change-set-title">Loading ChangeSet...</p>
                </Show>
                <Show when={!replacingSnapshot() && manifest.error} keyed>
                  {(error) => (
                    <div class="change-set-error">
                      <ErrorPanel
                        title="Failed to load ChangeSet"
                        error={error}
                      >
                        <RetryButton onRetry={reloadSnapshot} />
                      </ErrorPanel>
                    </div>
                  )}
                </Show>
              </>
            )}
          </ChangeSetShell>
        }
      >
        {(snapshot) => (
          <ChangeSetShell
            helpOpen={props.helpOpen}
            debugOpen={props.debugHudOpen}
            onToggleTree={() => props.onFileTreeOpenChange(!props.fileTreeOpen)}
            onToggleView={props.onToggleView}
            onToggleHistory={() =>
              props.onHistoryOpenChange(!props.historyOpen)
            }
            onReload={() => {
              void reloadSnapshot();
            }}
            onToggleHelp={() => props.onHelpOpenChange(!props.helpOpen)}
            onHelpOpenChange={props.onHelpOpenChange}
            onToggleDebug={() =>
              props.onDebugHudOpenChange(!props.debugHudOpen)
            }
          >
            {(hunkDisplay) => (
              <UnexpectedErrorBoundary
                title="Could not render ChangeSet snapshot"
                retryOnR={false}
              >
                <ReviewSnapshotBoundary
                  params={props.params}
                  engine={props.engine}
                  manifest={snapshot}
                  view={props.view}
                  historyOpen={props.historyOpen}
                  onHistoryOpenChange={props.onHistoryOpenChange}
                  fileTreeOpen={props.fileTreeOpen}
                  profile={props.profile}
                  appHeaderOutlets={props.appHeaderOutlets}
                  hunkDisplay={hunkDisplay}
                  state={props.state}
                  setState={props.setState}
                  onFileTreeOpenChange={props.onFileTreeOpenChange}
                  onFileSequenceChange={(stop) => {
                    stopFileSequence = stop;
                  }}
                />
              </UnexpectedErrorBoundary>
            )}
          </ChangeSetShell>
        )}
      </Show>
    </>
  );
}

/**
 * Defines every immutable backend input and reactive presentation input for one snapshot.
 *
 * Params and manifest never change during this component lifetime. View, profile,
 * and workspace presentation state remain reactive without retargeting backend work.
 * The callbacks expose only the two lifecycle actions performed by ChangeSetContent.
 */
type ChangeSetSnapshotProps = {
  params: DiffParams;
  engine: DiffEngine;
  manifest: Manifest;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  hunkDisplay: Accessor<HunkDisplay | null>;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
  onFileTreeOpenChange(open: boolean): void;
  onFileSequenceChange(stop: (() => Promise<void>) | null): void;
};

/**
 * Supplies ChangeSet-owned History visibility to one Snapshot review boundary.
 *
 * `historyOpen` is the complete current presentation state shared by the global
 * hotkey and History controls. The callback replaces only that state and never
 * performs review transport, navigation, or File-lane work.
 */
type ReviewSnapshotBoundaryProps = ChangeSetSnapshotProps & {
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
};

/**
 * Supplies the engine-bound File lane's current scroll-only destinations and
 * inline History grid position.
 *
 * The callback publishes manifest indexes whose current state is a loaded FullFile. It
 * must not load, expand, select, or navigate a File. The History callback
 * publishes only the concrete third grid child mounted beside the File lane.
 */
type ChangeSetFileLaneProps = ChangeSetSnapshotProps & {
  onFileNavigabilityChange(indexes: ReadonlySet<number>): void;
  onInlineHistoryTargetChange(target: HTMLElement): void;
};

/**
 * Keeps Snapshot review state mounted while engine changes replace the file lane.
 *
 * The manifest and Snapshot identity are immutable for this lifetime. History's
 * explicit View action uses the exact File pair and ordinary FileTree navigation;
 * the nested keyed branch replaces only engine-dependent File rendering.
 */
function ReviewSnapshotBoundary(
  props: ReviewSnapshotBoundaryProps,
): JSX.Element {
  const navigation = useNavigation();
  // History navigation shares this exact Snapshot boundary lifetime; disposal
  // cancels any line preparation before it can scroll or return a detached row.
  const reviewNavigationAbort = new AbortController();
  onCleanup(() => reviewNavigationAbort.abort());
  const files = manifestFilesInOrder(props.manifest.tree);
  const reviewFileIndexes = new Map<string, number>();
  files.forEach((file, fileIndex) => {
    const key = JSON.stringify([file.entry.left_path, file.entry.right_path]);
    assert(
      !reviewFileIndexes.has(key),
      "Review navigation requires unique manifest File pairs.",
    );
    reviewFileIndexes.set(key, fileIndex);
  });
  const [navigableFileIndexes, setNavigableFileIndexes] = createSignal<
    ReadonlySet<number>
  >(new Set());
  // Inline History is rendered by the surrounding review boundary into this
  // exact third ChangeSet grid child; Split History keeps its fixed host.
  const [inlineHistoryTarget, setInlineHistoryTarget] =
    createSignal<HTMLElement | null>(null);

  /** Resolves one located Thread to its unique immutable manifest index. */
  function reviewFileIndex(location: ThreadCodeLocation): number {
    const fileIndex = reviewFileIndexes.get(
      JSON.stringify([location.file.left_path, location.file.right_path]),
    );
    if (fileIndex === undefined) {
      throw new Error(
        "A located review Thread requires one exact manifest File.",
      );
    }
    return fileIndex;
  }

  /** Reports whether exact line navigation currently accepts the Thread. */
  function canViewReviewThread(location: ThreadCodeLocation): boolean {
    return navigableFileIndexes().has(reviewFileIndex(location));
  }

  /** Navigates to one loaded Thread line and returns its mounted review anchor. */
  async function viewReviewThread(
    location: ThreadCodeLocation,
  ): Promise<ReviewCodeAnchor | null> {
    const fileIndex = reviewFileIndex(location);
    if (!navigableFileIndexes().has(fileIndex)) {
      throw new Error("Review navigation requires a loaded File.");
    }
    const selectedPath = expect(
      location.side === "left"
        ? location.file.left_path
        : location.file.right_path,
      "Located review Thread requires its selected-side File path.",
    );
    const line = location.kind === "range" ? location.range.start_line : 1;
    const result = await navigation.navigate({
      kind: "line",
      fileIndex,
      target: {
        file: selectedPath,
        region:
          location.kind === "range" &&
          location.region.kind === "notebook-cell-source"
            ? location.region.cell_key
            : null,
        side: location.side,
        line: String(line),
      },
      abortSignal: reviewNavigationAbort.signal,
    });
    if (result.state === "stopped") return null;
    if (result.state === "missing") {
      throw new Error(
        "The exact reviewed line is absent from the loaded File.",
      );
    }
    assert(
      result.state === "complete",
      "Review line navigation did not finish.",
    );
    const changeSetRoot = inlineHistoryTarget()?.closest<HTMLElement>(
      "[data-change-set-root]",
    );
    assert(
      changeSetRoot !== null && changeSetRoot !== undefined,
      "Review navigation requires its mounted ChangeSet.",
    );
    const card = changeSetRoot.querySelector<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(card !== null, "Review navigation lost its loaded FileCard.");
    const region =
      location.kind === "range" &&
      location.region.kind === "notebook-cell-source"
        ? location.region.cell_key
        : "";
    const matchingGrids = [
      ...card.querySelectorAll<HTMLElement>(".diff-grid"),
    ].filter((grid) => grid.dataset.reviewRegion === region);
    assert(
      matchingGrids.length === 1,
      "Review navigation requires one exact rendered region.",
    );
    const matchingLines = expect(
      matchingGrids[0],
      "Reviewed region disappeared after navigation.",
    ).querySelectorAll<HTMLElement>(
      `.line-no[data-line-pin-side="${location.side}"][data-line-pin-line="${line}"]`,
    );
    assert(
      matchingLines.length === 1,
      "Review navigation requires one exact line in its prepared row.",
    );
    const lineNumber = expect(
      matchingLines[0],
      "Reviewed line disappeared after navigation.",
    );
    const rowPart = lineNumber.parentElement;
    const codeCell = rowPart?.querySelector<HTMLElement>(":scope > .line-code");
    const trigger = lineNumber.querySelector<HTMLButtonElement>(
      ".line-comment-trigger:not([hidden])",
    );
    assert(
      codeCell !== null && codeCell !== undefined && trigger !== null,
      "Reviewed line requires its rendered code and marker.",
    );
    return { codeCell, trigger };
  }

  return (
    <ReviewProvider
      snapshotId={props.manifest.snapshot_id}
      view={props.view}
      historyOpen={props.historyOpen}
      onHistoryOpenChange={props.onHistoryOpenChange}
      profile={props.profile}
      inlineHistoryTarget={inlineHistoryTarget}
      canViewThread={canViewReviewThread}
      viewThread={viewReviewThread}
    >
      <Show when={props.engine} keyed>
        {(engine) => (
          <ChangeSetSnapshot
            params={props.params}
            engine={engine}
            manifest={props.manifest}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            profile={props.profile}
            appHeaderOutlets={props.appHeaderOutlets}
            hunkDisplay={props.hunkDisplay}
            state={props.state}
            setState={props.setState}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onFileSequenceChange={props.onFileSequenceChange}
            onFileNavigabilityChange={setNavigableFileIndexes}
            onInlineHistoryTargetChange={setInlineHistoryTarget}
          />
        )}
      </Show>
    </ReviewProvider>
  );
}

/**
 * Owns every observer, lane value, derivation, and rendered node for one manifest.
 *
 * All query definitions use the same immutable params and opaque `snapshot_id`.
 * File failures remain localized. Disposal cancels the lane and releases every
 * snapshot query.
 */
function ChangeSetSnapshot(props: ChangeSetFileLaneProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  const pins = linePins();
  let changeSetRoot!: HTMLDivElement;
  const orderedFiles = manifestFilesInOrder(props.manifest.tree);
  const fileIndexByKey = new Map<string, number>();
  for (const [fileIndex, file] of orderedFiles.entries()) {
    const key = manifestEntryKey(file.entry);
    if (fileIndexByKey.has(key)) {
      throw new Error(
        `Manifest returned duplicate file ${fileDisplayName(file.entry)}.`,
      );
    }
    fileIndexByKey.set(key, fileIndex);
  }

  // The exact FileDiff `display_name` per manifest position: repository
  // snapshots use the backend's path-pair label, while preset backends
  // deliberately name their old/new fixture pair by its new-side path. The
  // lane asserts file responses against these names, and line-pin identity
  // resolves through them.
  const canonicalNames = orderedFiles.map((file) =>
    props.params.tab !== "preset"
      ? fileDisplayName(file.entry)
      : expect(
          file.entry.right_path,
          "Preset manifest file requires its canonical new-side path.",
        ),
  );

  const parsedLinePin = pins.parseUrl();
  let lineTarget: FileLaneLineTarget | null = null;
  if (parsedLinePin.state === "invalid") {
    toast.showTransient(
      "Line pin unavailable",
      "The URL contains an invalid line pin.",
      2_000,
    );
  } else if (parsedLinePin.state === "valid") {
    const matches = canonicalNames.flatMap((name, fileIndex) =>
      name === parsedLinePin.target.file ? [fileIndex] : [],
    );
    if (matches.length > 1) {
      throw new Error(
        `Line target path ${parsedLinePin.target.file} is ambiguous.`,
      );
    }
    const match = matches[0];
    if (match === undefined) {
      toast.showTransient(
        "Line pin unavailable",
        `${parsedLinePin.target.file} is not present in this ChangeSet.`,
        2_000,
      );
      const toggleResult = pins.toggleUrlState(parsedLinePin.target);
      if (toggleResult !== "unpinned") {
        throw new Error("Missing line-pin file changed its current target.");
      }
    } else {
      lineTarget = {
        fileIndex: match,
        restore: (signal) => pins.restore(parsedLinePin.target, match, signal),
      };
    }
  }

  const lane = createFileLane({
    engine: props.engine,
    snapshotId: props.manifest.snapshot_id,
    files: orderedFiles,
    canonicalNames,
    queryClient,
    lineTarget,
    onExplicitLoad: (fileIndex) => {
      const file = expect(
        orderedFiles[fileIndex],
        "Explicit load reported an invalid manifest index.",
      );
      const key = manifestEntryKey(file.entry);
      // The query result has replaced LazyFile with FullFile. Expand it
      // now, but never override a collapse performed while it loaded.
      if (props.state.fileExpansion[key] !== false) {
        props.setState("fileExpansion", key, true);
      }
    },
  });
  props.onFileSequenceChange(lane.stop);
  onCleanup(() => props.onFileSequenceChange(null));

  const preferenceQueries = createQueries<
    Array<ReturnType<typeof api.profile.preferences>>
  >(() => ({
    queries:
      props.profile === null ? [] : [api.profile.preferences(props.profile.id)],
  }));
  const aggressiveFolds = createMemo(() => {
    const query = preferenceQueries[0];
    if (query === undefined || query.data === undefined) {
      return true;
    }
    return query.data.aggressive_folds;
  });

  createEffect(() => {
    const navigable = new Set<number>();
    for (const [fileIndex, state] of lane.fileStates().entries()) {
      if (state.state === "full") {
        navigable.add(fileIndex);
      }
    }
    props.onFileNavigabilityChange(navigable);
  });
  onCleanup(() => props.onFileNavigabilityChange(new Set()));

  /**
   * Resolves one manifest file to its current canonical presentation state.
   *
   * Directory reachability consumes the same strict manifest/state ordering as
   * FileTree. Missing or duplicate identities violate the immutable snapshot
   * contract and throw instead of inventing a directory-expansion default.
   */
  function stateForManifestFile(file: ManifestFile): FileState {
    const fileIndex = fileIndexByKey.get(manifestEntryKey(file.entry));
    if (fileIndex === undefined) {
      throw new Error(
        `ChangeSet cannot index ${fileDisplayName(file.entry)} for directory reachability.`,
      );
    }
    const state = lane.fileStates()[fileIndex];
    if (state === undefined) {
      throw new Error(
        `ChangeSet is missing state for ${fileDisplayName(file.entry)}.`,
      );
    }
    return state;
  }

  const directoryExpansion = createMemo(() =>
    calculateDirectoryExpansion(
      props.manifest.tree,
      stateForManifestFile,
      props.state.fileExpansion,
    ),
  );

  const failedFiles = createMemo(
    () =>
      lane
        .fileStates()
        .filter(
          (state) => state.state === "lazy" && state.file.kind === "error",
        ).length,
  );
  const sequenceState = createMemo<FileSequenceState>(() => {
    const active = lane.activity();
    if (active !== null) {
      return {
        state: "loading",
        processed: lane.processed(),
        automaticTotal: lane.automaticTotal,
        failed: failedFiles(),
        active,
      };
    }
    return {
      state: "ready",
      processed: lane.processed(),
      automaticTotal: lane.automaticTotal,
      failed: failedFiles(),
    };
  });

  return (
    <>
      <Show when={lane.error()} keyed>
        {(error) => {
          throw error;
        }}
      </Show>
      <Portal mount={props.appHeaderOutlets.status()}>
        <AppHeaderFileStatus state={sequenceState()} />
      </Portal>
      <Portal mount={props.appHeaderOutlets.summary()}>
        <ManifestStatistics summary={props.manifest.summary} />
      </Portal>
      <p class="status change-set-title">
        {changeSetTitle(props.params, props.manifest)}
      </p>
      <div
        ref={changeSetRoot}
        class="diff-workspace"
        classList={{
          "diff-workspace-inline": props.view === "inline",
          "diff-workspace-tree-open": props.fileTreeOpen,
        }}
      >
        <UnexpectedErrorBoundary
          title="Could not render file tree"
          retryOnR={false}
        >
          <FileTree
            changeSetRoot={() => changeSetRoot}
            tree={props.manifest.tree}
            states={lane.fileStates}
            open={props.fileTreeOpen}
            view={props.view}
            selectedFileIndex={() =>
              props.hunkDisplay()?.selectedFileIndex ?? null
            }
            directoryExpansion={directoryExpansion}
            fileExpansion={() => props.state.fileExpansion}
            onOpenChange={props.onFileTreeOpenChange}
            onDirectoryExpandedChange={(directory, expanded) =>
              batch(() => {
                for (const file of manifestFilesInOrder(directory.entries)) {
                  props.setState(
                    "fileExpansion",
                    manifestEntryKey(file.entry),
                    expanded,
                  );
                }
              })
            }
            onFileExpandedChange={(file, expanded) =>
              props.setState(
                "fileExpansion",
                manifestEntryKey(file.entry),
                expanded,
              )
            }
          />
        </UnexpectedErrorBoundary>
        <section class="file-list" aria-label="Changed files">
          <Show
            when={orderedFiles.length > 0}
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
              <For each={orderedFiles}>
                {(file, fileIndex) => {
                  return (
                    <FileCard
                      reviewFile={{
                        left_path: file.entry.left_path,
                        right_path: file.entry.right_path,
                      }}
                      file_state={lane.fileState(fileIndex())}
                      expanded={fileExpanded(
                        file,
                        lane.fileState(fileIndex()),
                        props.state.fileExpansion,
                      )}
                      explicitlyCollapsed={
                        props.state.fileExpansion[
                          manifestEntryKey(file.entry)
                        ] === false
                      }
                      admitted={lane.admitted(fileIndex())}
                      engine={props.engine}
                      view={props.view}
                      aggressiveFolds={aggressiveFolds()}
                      linePins={pins}
                      globalSelectedHunk={() =>
                        props.hunkDisplay()?.globalSelectedHunk ?? null
                      }
                      fileSelectedHunk={() => {
                        const display = props.hunkDisplay();
                        if (display === null) {
                          return null;
                        }
                        return expect(
                          display.fileSelectedHunks.get(fileIndex()),
                          `Missing hunk position for file ${fileIndex()}.`,
                        );
                      }}
                      onExpandedChange={(expanded) =>
                        props.setState(
                          "fileExpansion",
                          manifestEntryKey(file.entry),
                          expanded,
                        )
                      }
                      onLoad={() => {
                        lane.enqueue(fileIndex(), "bounded");
                      }}
                      onRetry={() => {
                        lane.enqueue(fileIndex(), "unbounded");
                      }}
                    />
                  );
                }}
              </For>
            </div>
          </Show>
        </section>
        <Show when={props.view === "inline"}>
          <div
            ref={props.onInlineHistoryTargetChange}
            class="review-history-slot"
          />
        </Show>
      </div>
    </>
  );
}

/**
 * Renders current automatic progress, current failures, and one slow indicator.
 *
 * The compact region contributes no title or long path to AppHeader layout. Once
 * automatic work succeeds with no failures it renders nothing and relinquishes
 * its physical space.
 */
function AppHeaderFileStatus(props: { state: FileSequenceState }): JSX.Element {
  /**
   * Reports whether compact file-lane status currently has visible information.
   *
   * Visible automatic progress, localized failures, or an active slow marker
   * keep the region mounted. Activity without one of those children must not
   * create an empty bordered status group.
   */
  const visible = () => {
    if (props.state.processed < props.state.automaticTotal) {
      return true;
    }
    if (props.state.failed > 0) {
      return true;
    }
    return props.state.state === "loading" && props.state.active.slow;
  };
  return (
    <Show when={visible()}>
      <div
        class="summary-group summary-group-loaded-files change-set-file-status"
        aria-live="polite"
      >
        <Show when={props.state.processed < props.state.automaticTotal}>
          <span class="file-progress">
            <LoaderCircle
              class="file-state-spinner is-spinning"
              aria-hidden="true"
            />
            {props.state.processed}/{props.state.automaticTotal}
          </span>
        </Show>
        <Show when={props.state.failed > 0}>
          <span
            class="file-failure-count"
            aria-label={`${props.state.failed} files failed to load`}
            title={`${props.state.failed} files failed to load`}
          >
            <CircleAlert aria-hidden="true" /> {props.state.failed}
          </span>
        </Show>
        <Show
          when={
            props.state.state === "loading" && props.state.active.slow
              ? props.state.active
              : null
          }
          keyed
        >
          {(slowFile) => (
            <button
              type="button"
              class="app-header-slow-file"
              aria-label={`${slowFile.path} is taking longer than expected`}
              title={`${slowFile.path} is taking longer than expected`}
            >
              <Clock3 aria-hidden="true" />
            </button>
          )}
        </Show>
      </div>
    </Show>
  );
}

/**
 * Renders immutable manifest-level file, line, and optional cell aggregates.
 *
 * The component reads only ManifestSummary. It never accumulates loaded
 * FullFile summaries or announces itself as changing sequence progress.
 */
function ManifestStatistics(props: { summary: ManifestSummary }): JSX.Element {
  /**
   * Returns one required cell aggregate when the cell summary is present.
   *
   * The backend schema permits each field to be independently null, but a
   * visible Cells group requires all three metrics. Missing data is therefore a
   * response-contract error rather than an empty label.
   */
  function cellMetric(
    key: "added_cells" | "modified_cells" | "removed_cells",
  ): number {
    const value = props.summary[key];
    if (value === null) {
      throw new Error(`Manifest summary is missing ${key}.`);
    }
    return value;
  }

  return (
    <>
      <div class="summary-group summary-group-files">
        <strong>Files</strong>
        <span class="delta added">+ {props.summary.added_files}</span>
        <span class="delta changed">~ {props.summary.updated_files}</span>
        <span class="delta removed">- {props.summary.removed_files}</span>
      </div>
      <div class="summary-group summary-group-lines">
        <strong>Lines</strong>
        <span class="delta added">
          +{" "}
          {props.summary.added_lines === null ? "?" : props.summary.added_lines}
        </span>
        <span class="delta removed">
          -{" "}
          {props.summary.removed_lines === null
            ? "?"
            : props.summary.removed_lines}
        </span>
      </div>
      <Show when={props.summary.changed_cells !== null}>
        <div class="summary-group summary-group-cells">
          <strong>Cells</strong>
          <span class="delta added">+ {cellMetric("added_cells")}</span>
          <span class="delta changed">~ {cellMetric("modified_cells")}</span>
          <span class="delta removed">- {cellMetric("removed_cells")}</span>
        </div>
      </Show>
    </>
  );
}

/**
 * Derives the exact ChangeSet title used by the established frontend.
 *
 * Complete selected DiffParams choose product wording while manifest labels
 * provide authoritative ref display. The function never mutates URL or requests.
 */
function changeSetTitle(params: DiffParams, manifest: Manifest): string {
  switch (params.tab) {
    case "head":
      return "Working tree vs HEAD";
    case "refs":
      return `${manifest.left_label} vs ${manifest.right_label}`;
    case "branch-review": {
      const base =
        params.base_selection.source === "remote"
          ? `${params.base_selection.remote}/${params.base_selection.branch}`
          : params.base_selection.branch;
      const review =
        params.review_selection.source === "remote"
          ? `${params.review_selection.remote}/${params.review_selection.branch}`
          : params.review_selection.branch;
      return `${review} vs ${base}`;
    }
    case "pull-request":
      return `${manifest.right_label} vs ${manifest.left_label}`;
    case "preset": {
      const kind =
        params.project_id === "gumtree"
          ? "GumTree"
          : `${params.project_id.charAt(0).toUpperCase()}${params.project_id.slice(1)}`;
      return `${kind} preset ${params.preset_subset}`;
    }
  }
}
