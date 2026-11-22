import {
  For,
  Show,
  batch,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
  type Accessor,
  type JSX,
} from "solid-js";
import { createStore, type SetStoreFunction } from "solid-js/store";
import { Portal } from "solid-js/web";
import { createQuery, useQueryClient } from "@tanstack/solid-query";
import { CircleAlert, Clock3, LoaderCircle } from "lucide-solid";
import {
  api,
  type DiffEngine,
  type EngineConfig,
  type DiffParams,
  type Manifest,
  type ManifestFile,
  type ManifestSummary,
  type ReviewFilePair,
  type ThreadCodePoint,
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
import {
  FileCard,
  type BayRenderMode,
  type BayRenderModes,
  type LineCell,
} from "../fileCard/FileCard";
import {
  createFileLane,
  fileDisplayName,
  manifestEntryKey,
  manifestFilesInOrder,
  type FileLaneActivity,
  type FileLaneLineTarget,
  type FileState,
} from "./fileLane";
import { linePins } from "../linePins";
import { useNavigation, writeInitialHunkSelection } from "../navigation";
import type { StoredProfile } from "../Profile";
import { ReviewProvider } from "../review/Review";
import {
  calculateDirectoryExpansion,
  fileExpanded,
  FileTree,
} from "./FileTree";
import { ChangeSetShell, type HunkDisplay } from "./Shell";

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

type ChangeSetState = {
  // Keyed by manifest path pair. `undefined` delegates to the file state's
  // initial policy; entries survive Tab inactivity but reload clears them all.
  fileExpansion: Record<string, boolean | undefined>;
};

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
            // No Snapshot is mounted here, so no History covers this shell;
            // its loading and error panels stay interactive.
            historyOpen={false}
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
            onToggleOptions={props.appHeaderOutlets.toggleEngineOptions}
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
            historyOpen={props.historyOpen}
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
            onToggleOptions={props.appHeaderOutlets.toggleEngineOptions}
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

type ChangeSetSnapshotProps = {
  params: DiffParams;
  engine: EngineConfig;
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

type ReviewSnapshotBoundaryProps = Omit<ChangeSetSnapshotProps, "engine"> & {
  engine: DiffEngine;
  historyOpen: boolean;
  onHistoryOpenChange(open: boolean): void;
};

type ChangeSetFileLaneProps = ChangeSetSnapshotProps & {
  onFileNavigabilityChange(indexes: ReadonlySet<number>): void;
  onInlineHistoryTargetChange(target: HTMLElement): void;
};

function ReviewSnapshotBoundary(
  props: ReviewSnapshotBoundaryProps,
): JSX.Element {
  const preferences = createQuery(() =>
    api.profile.preferences(props.profile?.id ?? null),
  );
  // Structural equality below keeps unrelated preference updates from
  // replacing the File lane.
  const configuration = createMemo<EngineConfig | null>(
    () => {
      if (preferences.isPending || preferences.error !== null) {
        return null;
      }
      const current = expect(
        preferences.data,
        "Loaded preferences require data.",
      );
      switch (props.engine) {
        case "difftastic":
          return {
            engine: "difftastic",
            ignore_comments: current.difftastic_ignore_comments,
          };
        case "gumtree":
          return {
            engine: "gumtree",
            matcher: current.gumtree_matcher,
          };
        case "git":
          return {
            engine: "git",
            algorithm: current.git_algorithm,
            color_moved: current.git_color_moved,
            color_moved_ws: current.git_color_moved_ws,
            ignore_space_change: current.git_ignore_space_change,
            word_diff: current.git_word_diff,
            word_boundary:
              current.git_word_boundary === "custom"
                ? { mode: "custom", regex: current.git_word_diff_regex }
                : { mode: current.git_word_boundary },
          };
        case "diffsitter":
        case "dirdiff":
        case "tokendiff":
          return { engine: props.engine };
      }
    },
    null,
    { equals: (left, right) => JSON.stringify(left) === JSON.stringify(right) },
  );
  const navigation = useNavigation();
  // Disposal cancels line preparation so it cannot scroll or return a detached
  // row. Line navigation resolves null when aborted this way.
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
  // The inline History toggle portals into the third ChangeSet grid child; the
  // Split toggle and the panel live in History's own fixed host.
  const [inlineHistoryTarget, setInlineHistoryTarget] =
    createSignal<HTMLElement | null>(null);

  function reviewFileIndex(file: ReviewFilePair): number {
    return expect(
      reviewFileIndexes.get(JSON.stringify([file.left_path, file.right_path])),
      "A located review Thread requires one exact manifest File.",
    );
  }

  function canViewReviewThread(point: ThreadCodePoint): boolean {
    return navigableFileIndexes().has(reviewFileIndex(point.file));
  }

  function canViewReviewFile(file: ReviewFilePair): boolean {
    return navigableFileIndexes().has(reviewFileIndex(file));
  }

  async function viewReviewThread(
    point: ThreadCodePoint,
  ): Promise<LineCell | null> {
    const fileIndex = reviewFileIndex(point.file);
    if (!navigableFileIndexes().has(fileIndex)) {
      throw new Error("Review navigation requires a loaded File.");
    }
    const bay = point.bay;
    const result = await navigation.navigate({
      kind: "line",
      fileIndex,
      target: {
        file: point.file,
        bay,
        side: point.side,
        line: String(point.line),
      },
      abortSignal: reviewNavigationAbort.signal,
    });
    if (result.state === "stopped") {
      return null;
    }
    if (result.state === "missing") {
      throw new Error(
        "The exact reviewed line is absent from the loaded File.",
      );
    }
    return result.cell;
  }

  async function viewReviewFileStart(file: ReviewFilePair): Promise<void> {
    const fileIndex = reviewFileIndex(file);
    if (!navigableFileIndexes().has(fileIndex)) {
      throw new Error("Review navigation requires a loaded File.");
    }
    await navigation.navigate({ kind: "file-start", fileIndex });
  }

  async function viewReviewOrigin(point: ThreadCodePoint): Promise<void> {
    const fileIndex = reviewFileIndex(point.file);
    if (!navigableFileIndexes().has(fileIndex)) {
      throw new Error("Review navigation requires a loaded File.");
    }
    const result = await navigation.navigate({
      kind: "line",
      fileIndex,
      target: {
        file: point.file,
        bay: point.bay,
        side: point.side,
        line: String(point.line),
      },
      abortSignal: reviewNavigationAbort.signal,
    });
    if (result.state === "missing") {
      throw new Error(
        "The unchanged reviewed line is absent from the loaded File.",
      );
    }
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
      canViewFile={canViewReviewFile}
      viewFileStart={viewReviewFileStart}
      viewOriginThread={viewReviewOrigin}
    >
      <Show when={configuration()} keyed>
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

function ChangeSetSnapshot(props: ChangeSetFileLaneProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  const pins = linePins();
  let changeSetRoot!: HTMLDivElement;
  const orderedFiles = manifestFilesInOrder(props.manifest.tree);
  // Indexed like `orderedFiles`. Each mounted text bay registers its mode under
  // its File for exactly its mounted lifetime, so an entry never outlives its
  // bay and a File whose body is unmounted holds none.
  const [bayModes, setBayModes] = createStore<
    Record<string, BayRenderMode | undefined>[]
  >(orderedFiles.map(() => ({})));
  const fileRenderModes = orderedFiles.map((_, fileIndex) => {
    const bayRenderModes: BayRenderModes = {
      mode: (bayKey) =>
        expect(
          expect(bayModes[fileIndex], "Bay modes are indexed by File.")[bayKey],
          `Bay ${bayKey} read its render mode unregistered.`,
        ),
      setMode: (bayKey, mode) => setBayModes(fileIndex, bayKey, mode),
      clearMode: (bayKey) => setBayModes(fileIndex, bayKey, undefined),
    };
    const renderMode = createMemo((): BayRenderMode | null => {
      const modes = Object.values(
        expect(bayModes[fileIndex], "Bay modes are indexed by File."),
      ).filter((mode): mode is BayRenderMode => mode !== undefined);
      if (modes.length === 0) {
        return null;
      }
      return modes.every((mode) => mode === "virtual") ? "virtual" : "rich";
    });
    return { bayRenderModes, renderMode };
  });
  const fileIndexByKey = new Map<string, number>();
  for (const [fileIndex, file] of orderedFiles.entries()) {
    const key = manifestEntryKey(file.entry);
    assert(
      !fileIndexByKey.has(key),
      `Manifest returned duplicate file ${fileDisplayName(file.entry)}.`,
    );
    fileIndexByKey.set(key, fileIndex);
  }

  // The exact FileDiff `display_name` per manifest position: repository
  // snapshots use the backend's path-pair label, while preset backends
  // deliberately name their fixture by its new-side path, falling to the
  // old side for a fixture that only deletes. The lane asserts file
  // responses against these names.
  const canonicalNames = orderedFiles.map((file) =>
    props.params.tab !== "preset"
      ? fileDisplayName(file.entry)
      : expect(
          file.entry.right_path ?? file.entry.left_path,
          "Preset manifest file requires an old- or new-side path.",
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
    // The pin's File pair identifies its manifest entry directly, on every
    // Tab; a pin never resolves through display names.
    const pinnedPair = parsedLinePin.target.file;
    const pinnedSidePath = expect(
      parsedLinePin.target.side === "left"
        ? pinnedPair.left_path
        : pinnedPair.right_path,
      "A line pin names a side its File pair does not have.",
    );
    const matches = orderedFiles.flatMap((file, fileIndex) =>
      file.entry.left_path === pinnedPair.left_path &&
      file.entry.right_path === pinnedPair.right_path
        ? [fileIndex]
        : [],
    );
    assert(
      matches.length <= 1,
      `Line target path ${pinnedSidePath} is ambiguous.`,
    );
    const match = matches[0];
    if (match === undefined) {
      toast.showTransient(
        "Line pin unavailable",
        `${pinnedSidePath} is not present in this ChangeSet.`,
        2_000,
      );
      const toggleResult = pins.toggleUrlState(parsedLinePin.target);
      assert(
        toggleResult === "unpinned",
        "Missing line-pin file changed its current target.",
      );
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
  // Reload awaits this stop; cleared on unmount so reload cannot stop a
  // disposed lane.
  props.onFileSequenceChange(lane.stop);
  onCleanup(() => props.onFileSequenceChange(null));

  // The NavigationProvider survives engine-keyed snapshot replacement, so it
  // cannot select in a replacement snapshot's fresh DOM; the snapshot does.
  onMount(() => writeInitialHunkSelection(changeSetRoot));

  const preferences = createQuery(() =>
    api.profile.preferences(props.profile?.id ?? null),
  );
  const aggressiveFolds = createMemo(
    () =>
      expect(preferences.data, "File folding requires loaded preferences.")
        .aggressive_folds,
  );

  // The lane knows nothing about review, so this effect hands the set of Full
  // indexes across. Cleanup publishes the empty set because the review
  // boundary outlives this engine-keyed snapshot.
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

  function stateForManifestFile(file: ManifestFile): FileState {
    const fileIndex = expect(
      fileIndexByKey.get(manifestEntryKey(file.entry)),
      `ChangeSet cannot index ${fileDisplayName(file.entry)} for directory reachability.`,
    );
    return expect(
      lane.fileStates()[fileIndex],
      `ChangeSet is missing state for ${fileDisplayName(file.entry)}.`,
    );
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
            tree={props.manifest.tree}
            states={lane.fileStates}
            open={props.fileTreeOpen}
            view={props.view}
            selectedFileIndex={() =>
              props.hunkDisplay()?.selectedFileIndex ?? null
            }
            directoryExpansion={directoryExpansion}
            fileExpansion={() => props.state.fileExpansion}
            renderMode={(fileIndex) =>
              expect(
                fileRenderModes[fileIndex],
                "FileTree asked for a File outside the manifest.",
              ).renderMode()
            }
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
                  const modes = expect(
                    fileRenderModes[fileIndex()],
                    "Bay modes are indexed by File.",
                  );
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
                      view={props.view}
                      aggressiveFolds={aggressiveFolds()}
                      linePins={pins}
                      bayRenderModes={modes.bayRenderModes}
                      renderMode={modes.renderMode()}
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

function AppHeaderFileStatus(props: { state: FileSequenceState }): JSX.Element {
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

function ManifestStatistics(props: { summary: ManifestSummary }): JSX.Element {
  function cellMetric(
    key: "added_cells" | "modified_cells" | "removed_cells",
  ): number {
    const value = props.summary[key];
    return expect(value, `Manifest summary is missing ${key}.`);
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

function changeSetTitle(params: DiffParams, manifest: Manifest): string {
  switch (params.tab) {
    case "head":
      return "Working tree vs HEAD";
    case "refs":
      return `${manifest.left_label} vs ${manifest.right_label}`;
    case "branch-review": {
      const base =
        params.base.source === "remote"
          ? `${params.base.remote}/${params.base.branch}`
          : params.base.branch;
      const review =
        params.review.source === "remote"
          ? `${params.review.remote}/${params.review.branch}`
          : params.review.branch;
      return `${review} vs ${base}`;
    }
    case "pull-request":
      return `${manifest.right_label} vs ${manifest.left_label}`;
    case "preset": {
      const kind =
        params.catalog === "gumtree"
          ? "GumTree"
          : `${params.catalog.charAt(0).toUpperCase()}${params.catalog.slice(1)}`;
      return `${kind} preset ${params.subset}`;
    }
  }
}
