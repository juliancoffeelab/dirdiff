/**
 * Owns one selected ChangeSet's backend observers, file lane, and presentation.
 *
 * The module exports ChangeSet. Its lightweight outer lifetime owns FileTree and
 * file-expansion state; its active inner lifetime owns the manifest, lazy-info,
 * ordered file observers, strict sequential file-fetch lane, compact AppHeader
 * Portals, ChangeSet title, FileTree, and FileCards. It must not copy backend
 * results into Solid state, start concurrent file-diff requests, own workspace or
 * Tab selections, or implement navigation and virtualization in this boundary.
 */
import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  requestCallback,
  type JSX,
} from "solid-js";
import { createStore, type SetStoreFunction } from "solid-js/store";
import { Portal } from "solid-js/web";
import {
  createQueries,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { isCancelledError, type QueryKey } from "@tanstack/query-core";
import { CircleAlert, Clock3, LoaderCircle } from "lucide-solid";
import {
  api,
  type DiffParams,
  type FileDiff,
  type LazyInfoFile,
  type Manifest,
  type ManifestDirectory,
  type ManifestFile,
  type ManifestNode,
  type ManifestSummary,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets } from "./AppHeader";
import { FileCard } from "./FileCard";
import type { StoredProfile } from "./Profile";

const SLOW_FILE_THRESHOLD_MS = 8_000;

/**
 * Yields one async continuation through Solid's cooperative scheduler.
 *
 * Callers await the returned Promise to end the current rendering loop turn and
 * let the main thread process browser work before continuing. The scheduled
 * callback has no side effects and requires no cancellation lifecycle.
 */
function schedulerYield(): Promise<void> {
  return new Promise((resolve) => {
    requestCallback(resolve);
  });
}

/**
 * Defines every complete input needed to identify and activate one ChangeSet.
 *
 * `params` is a selected complete DiffParams value, `view` is the global reactive
 * renderer input, `profile` is genuine nullable profile identity, and `active`
 * controls expensive observation. No field may represent live control input.
 */
type ChangeSetProps = {
  active: boolean;
  params: DiffParams;
  view: DiffViewMode;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
};

/**
 * Contains all lightweight client state that survives inactive Tab periods.
 *
 * Expansion keys are manifest paths. Backend files, query state, progress, hunk
 * selection, and renderer rows are deliberately excluded from this store.
 */
type ChangeSetState = {
  treeOpen: boolean;
  directoryExpansion: Record<string, boolean | undefined>;
  fileExpansion: Record<string, boolean | undefined>;
};

/**
 * Describes the active work presented by the single file lane.
 *
 * `kind` distinguishes ordinary manifest progress from an explicit LazyFile
 * selection. Slow is a one-shot threshold flag rather than elapsed-time state.
 */
type FileLaneActivity = {
  kind: "sequence" | "selected";
  fileIndex: number;
  path: string;
  slow: boolean;
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
 * Describes the line statistics FileTree can progressively project.
 *
 * Null means the current file state genuinely lacks that statistic. The tree
 * renders this absence explicitly and never treats it as zero.
 */
type TreeLineStats = {
  added: number | null;
  modified: number | null;
  removed: number | null;
  moved: number | null;
};

/**
 * Establishes one stable ChangeSet lifetime for a Tab's selected parameters.
 *
 * Callers keep this boundary mounted across Tab switches and global view/engine
 * changes. Only active content observes queries and renders expensive file DOM;
 * the outer expansion store survives inactive periods and is destroyed with the
 * selected Tab value or workspace reset.
 */
export function ChangeSet(props: ChangeSetProps): JSX.Element {
  const [state, setState] = createStore<ChangeSetState>({
    treeOpen: false,
    directoryExpansion: {},
    fileExpansion: {},
  });

  return (
    <Show when={props.active}>
      <UnexpectedErrorBoundary title="Could not render ChangeSet">
        <ChangeSetContent
          params={props.params}
          view={props.view}
          profile={props.profile}
          appHeaderOutlets={props.appHeaderOutlets}
          state={state}
          setState={setState}
        />
      </UnexpectedErrorBoundary>
    </Show>
  );
}

/**
 * Defines the complete active-owner inputs for one ChangeSet generation.
 *
 * The outer component supplies durable client state; this inner lifetime owns all
 * query observers and external async work and is disposed whenever the Tab hides.
 */
type ChangeSetContentProps = {
  params: DiffParams;
  view: DiffViewMode;
  profile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  state: ChangeSetState;
  setState: SetStoreFunction<ChangeSetState>;
};

/**
 * Observes one active manifest and renders its complete file presentation.
 *
 * Every query uses the canonical api facade. Solid state contains only lane
 * bookkeeping and expansion; immutable backend values remain in TanStack Query.
 */
function ChangeSetContent(props: ChangeSetContentProps): JSX.Element {
  const queryClient = useQueryClient();
  const [processed, setProcessed] = createSignal(0);
  const [laneActivity, setLaneActivity] = createSignal<FileLaneActivity | null>(
    null,
  );
  const [laneError, setLaneError] = createSignal<Error | null>(null);
  let enqueueSelectedFile: ((fileIndex: number) => void) | null = null;
  let stopFileSequence: (() => Promise<void>) | null = null;

  const manifest = createQuery(() => api.changeSet.manifest(props.params));
  const orderedFiles = createMemo(() =>
    manifest.data === undefined ? [] : manifestFilesInOrder(manifest.data.tree),
  );
  const automaticTotal = createMemo(
    () => orderedFiles().filter((file) => file.entry.lazy === null).length,
  );

  const lazyInfoQueries = createQueries<
    Array<ReturnType<typeof api.changeSet.lazyInfo>>
  >(() => {
    if (
      manifest.data === undefined ||
      !manifestContainsLazyFiles(manifest.data.tree)
    ) {
      return { queries: [] };
    }
    return {
      queries: [api.changeSet.lazyInfo(props.params, manifest.data.cache_id)],
    };
  });

  const fileQueries = createQueries(() => {
    if (manifest.data === undefined) {
      return { queries: [] };
    }
    return {
      queries: orderedFiles().map((file) => ({
        ...api.changeSet.file(props.params, manifest.data.cache_id, file.entry),
        enabled: false,
      })),
    };
  });

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

  const lazyInfoByKey = createMemo(() => {
    const result = new Map<string, LazyInfoFile>();
    const query = lazyInfoQueries[0];
    if (query === undefined || query.data === undefined) {
      return result;
    }
    const expected = new Map<string, ManifestFile>();
    for (const file of orderedFiles()) {
      if (file.entry.lazy === null) {
        continue;
      }
      const key = manifestEntryKey(file.entry);
      if (expected.has(key)) {
        throw new Error(
          `Manifest returned duplicate lazy file ${fileDisplayName(file.entry)}.`,
        );
      }
      expected.set(key, file);
    }
    for (const info of query.data.files) {
      const key = manifestEntryKey(info);
      if (!expected.has(key)) {
        throw new Error(
          `Lazy info returned unexpected file ${fileDisplayName(info)}.`,
        );
      }
      if (result.has(key)) {
        throw new Error(
          `Lazy info returned duplicate file ${fileDisplayName(info)}.`,
        );
      }
      if (info.lazy === null) {
        throw new Error(
          `Lazy info omitted the reason for ${fileDisplayName(info)}.`,
        );
      }
      result.set(key, info);
    }
    for (const [key, file] of expected) {
      if (!result.has(key)) {
        throw new Error(
          `Lazy info omitted manifest file ${fileDisplayName(file.entry)}.`,
        );
      }
    }
    return result;
  });

  const fileStates = createMemo(() =>
    orderedFiles().map((manifestFile, fileIndex) => {
      const query = fileQueries[fileIndex];
      const path = fileDisplayName(manifestFile.entry);
      if (query === undefined) {
        return {
          state: "husk" as const,
          fileIndex,
          name: manifestFile.name,
          path,
          activity: "queued" as const,
        };
      }
      if (query.fetchStatus === "fetching") {
        return {
          state: "husk" as const,
          fileIndex,
          name: manifestFile.name,
          path,
          activity: "fetching" as const,
        };
      }
      if (query.isSuccess) {
        return {
          state: "full" as const,
          fileIndex,
          file: query.data,
        };
      }
      if (query.isError) {
        if (!(query.error instanceof Error)) {
          throw new Error(`File query ${path} failed without an Error value.`);
        }
        return {
          state: "lazy" as const,
          fileIndex,
          file: {
            kind: "error" as const,
            name: manifestFile.name,
            path,
            error: query.error,
          },
        };
      }
      if (manifestFile.entry.lazy !== null) {
        const lazyInfoQuery = lazyInfoQueries[0];
        if (lazyInfoQuery !== undefined && lazyInfoQuery.isError) {
          if (!(lazyInfoQuery.error instanceof Error)) {
            throw new Error(
              `Lazy-info query for ${path} failed without an Error value.`,
            );
          }
          return {
            state: "lazy" as const,
            fileIndex,
            file: {
              kind: "error" as const,
              name: manifestFile.name,
              path,
              error: lazyInfoQuery.error,
            },
          };
        }
        const info = lazyInfoByKey().get(manifestEntryKey(manifestFile.entry));
        if (info !== undefined) {
          return {
            state: "lazy" as const,
            fileIndex,
            file: { kind: "deferred" as const, info },
          };
        }
      }
      return {
        state: "husk" as const,
        fileIndex,
        name: manifestFile.name,
        path,
        activity: "queued" as const,
      };
    }),
  );

  const failedFiles = createMemo(
    () =>
      fileStates().filter(
        (state) => state.state === "lazy" && state.file.kind === "error",
      ).length,
  );
  const sequenceState = createMemo<FileSequenceState>(() => {
    const active = laneActivity();
    if (active !== null) {
      return {
        state: "loading",
        processed: processed(),
        automaticTotal: automaticTotal(),
        failed: failedFiles(),
        active,
      };
    }
    return {
      state: "ready",
      processed: processed(),
      automaticTotal: automaticTotal(),
      failed: failedFiles(),
    };
  });

  // Synthetic backpressure set to break the rendering loop and let the main thread breathe.
  const [admittedFiles, setAdmittedFiles] = createStore<Record<number, true>>(
    {},
  );

  /**
   * Synchronizes one mounted manifest generation with its imperative file-fetch lane.
   *
   * The effect starts after manifest success and reruns only for a new reactive
   * manifest/parameter generation. Cleanup stops scheduling, clears the public
   * enqueue closure, and cancels the exact active canonical query. Query failures
   * remain query state; unexpected orchestration failures are projected back into
   * Solid so the owning ErrorBoundary can contain and Toast them.
   */
  createEffect(() => {
    const dataUpdatedAt = manifest.dataUpdatedAt;
    if (manifest.data === undefined) {
      enqueueSelectedFile = null;
      stopFileSequence = null;
      setProcessed(0);
      setLaneActivity(null);
      return;
    }
    if (dataUpdatedAt <= 0) {
      throw new Error("A loaded manifest requires a positive update time.");
    }
    const snapshot = manifest.data;
    const params = props.params;
    const files = manifestFilesInOrder(snapshot.tree);
    const selectedQueue: number[] = [];
    const selectedSet = new Set<number>();
    let automaticCursor = 0;
    let activeIndex: number | null = null;
    let activeKey: QueryKey | null = null;
    let running = false;
    let stopped = false;
    let stopPromise: Promise<void> | null = null;

    setProcessed(0);
    setLaneActivity(null);
    setLaneError(null);

    /**
     * Projects an unexpected async lane rejection into Solid-owned error state.
     *
     * Promise rejections may legally contain unknown JavaScript values. Real
     * Errors retain their identity and stack; other values become an Error with
     * the original rejection preserved as its cause.
     */
    function reportLaneFailure(error: unknown): void {
      setLaneError(
        error instanceof Error
          ? error
          : new Error("File lane rejected without an Error value.", {
              cause: error,
            }),
      );
    }

    /**
     * Drains explicit selections before resuming strict automatic manifest order.
     *
     * The closure belongs to this exact manifest generation. It catches only
     * query-owned failure/cancellation so the canonical observer retains damage;
     * its launch callback projects unexpected orchestration errors into Solid.
     */
    async function runLane(): Promise<void> {
      if (running || stopped) {
        return;
      }
      running = true;
      try {
        while (!stopped) {
          let kind: FileLaneActivity["kind"] = "selected";
          let fileIndex = selectedQueue.shift();
          if (fileIndex !== undefined) {
            selectedSet.delete(fileIndex);
          } else {
            kind = "sequence";
            while (automaticCursor < files.length) {
              const candidate = files[automaticCursor];
              if (candidate === undefined) {
                throw new Error(
                  `File lane lost manifest index ${automaticCursor}.`,
                );
              }
              if (candidate.entry.lazy === null) {
                break;
              }
              automaticCursor += 1;
            }
            if (automaticCursor >= files.length) {
              break;
            }
            fileIndex = automaticCursor;
            automaticCursor += 1;
          }

          const file = files[fileIndex];
          if (file === undefined) {
            throw new Error(`File lane selected invalid index ${fileIndex}.`);
          }
          const options = api.changeSet.file(
            params,
            snapshot.cache_id,
            file.entry,
          );
          const activity: FileLaneActivity = {
            kind,
            fileIndex,
            path: fileDisplayName(file.entry),
            slow: false,
          };
          activeIndex = fileIndex;
          activeKey = options.queryKey;
          setLaneActivity(activity);
          const slowTimer = window.setTimeout(() => {
            if (!stopped && activeIndex === fileIndex) {
              setLaneActivity({ ...activity, slow: true });
            }
          }, SLOW_FILE_THRESHOLD_MS);

          try {
            await queryClient.fetchQuery(options);
            await schedulerYield();
            if (stopped) {
              return;
            }
            setAdmittedFiles(fileIndex, true);
          } catch (error) {
            if (stopped || isCancelledError(error)) {
              return;
            }
            // The canonical query owns and presents this failed attempt. The lane
            // intentionally proceeds so one file cannot damage later files.
          } finally {
            window.clearTimeout(slowTimer);
            activeIndex = null;
            activeKey = null;
            setLaneActivity(null);
            if (kind === "sequence" && !stopped) {
              setProcessed((current) => current + 1);
            }
          }
        }
      } finally {
        running = false;
      }
    }

    /**
     * Stops this exact manifest generation and cancels its active canonical query.
     *
     * The operation is idempotent and returns the same Promise to concurrent
     * callers. It prevents further scheduling synchronously, then waits for the
     * active cancellation before an explicit manifest reload may proceed.
     */
    function stopLane(): Promise<void> {
      if (stopPromise !== null) {
        return stopPromise;
      }
      stopped = true;
      enqueueSelectedFile = null;
      setLaneActivity(null);
      const queryKey = activeKey;
      stopPromise =
        queryKey === null
          ? Promise.resolve()
          : queryClient.cancelQueries({ queryKey, exact: true });
      return stopPromise;
    }

    stopFileSequence = stopLane;

    enqueueSelectedFile = (fileIndex) => {
      const file = files[fileIndex];
      if (file === undefined) {
        throw new Error(`Cannot load unknown file index ${fileIndex}.`);
      }
      const query = fileQueries[fileIndex];
      if (
        (query !== undefined && query.isSuccess) ||
        activeIndex === fileIndex
      ) {
        return;
      }
      if (!selectedSet.has(fileIndex)) {
        selectedSet.add(fileIndex);
        selectedQueue.push(fileIndex);
      }
      void runLane().catch(reportLaneFailure);
    };

    void runLane().catch(reportLaneFailure);

    onCleanup(() => {
      if (stopFileSequence === stopLane) {
        stopFileSequence = null;
      }
      void stopLane().catch(reportLaneFailure);
    });
  });

  /**
   * Submits one LazyFile or failed FileCard to the current single file-fetch lane.
   *
   * A mounted manifest generation is required. The callback never calls refetch
   * or transport directly and therefore cannot bypass sequencing.
   */
  function loadSelectedFile(fileIndex: number): void {
    if (enqueueSelectedFile === null) {
      throw new Error("Cannot load a file before its manifest lane exists.");
    }
    enqueueSelectedFile(fileIndex);
  }

  /**
   * Performs one explicit ChangeSet reload against the active manifest observer.
   *
   * The current file lane is stopped and its active file-query cancellation settles
   * before durable layout and progress reset. The canonical manifest observer
   * owns the next attempt and its query-level Toast behavior.
   */
  async function reloadChangeSet(): Promise<void> {
    if (stopFileSequence !== null) {
      await stopFileSequence();
    }
    setProcessed(0);
    setLaneActivity(null);
    props.setState({
      treeOpen: false,
      directoryExpansion: {},
      fileExpansion: {},
    });
    await manifest.refetch();
  }

  return (
    <>
      <Show when={laneError()} keyed>
        {(error) => {
          throw error;
        }}
      </Show>
      <Show when={manifest.isPending}>
        <p class="status change-set-title">Loading ChangeSet...</p>
      </Show>
      <Show when={manifest.error} keyed>
        {(error) => (
          <div class="change-set-error">
            <ErrorPanel title="Failed to load ChangeSet" error={error}>
              <RetryButton onRetry={reloadChangeSet} />
            </ErrorPanel>
          </div>
        )}
      </Show>
      <Show when={manifest.data} keyed>
        {(data) => (
          <>
            <Portal mount={props.appHeaderOutlets.status()}>
              <AppHeaderFileStatus state={sequenceState()} />
            </Portal>
            <Portal mount={props.appHeaderOutlets.summary()}>
              <ManifestStatistics summary={data.summary} />
            </Portal>
            <p class="status change-set-title">
              {changeSetTitle(props.params, data)}
            </p>
            <div
              class="diff-workspace"
              classList={{
                "diff-workspace-inline": props.view === "inline",
                "diff-workspace-tree-open": props.state.treeOpen,
              }}
            >
              <UnexpectedErrorBoundary title="Could not render file tree">
                <FileTree
                  tree={data.tree}
                  states={fileStates()}
                  open={props.state.treeOpen}
                  view={props.view}
                  directoryExpansion={props.state.directoryExpansion}
                  fileExpansion={props.state.fileExpansion}
                  onOpenChange={(open) => props.setState("treeOpen", open)}
                  onDirectoryExpandedChange={(directory, expanded) => {
                    props.setState(
                      "directoryExpansion",
                      directory.path,
                      expanded,
                    );
                    for (const file of manifestFilesInOrder(
                      directory.entries,
                    )) {
                      props.setState(
                        "fileExpansion",
                        manifestEntryKey(file.entry),
                        expanded,
                      );
                    }
                  }}
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
                  when={orderedFiles().length > 0}
                  fallback={
                    <div class="directory-groups">
                      <section
                        class="directory-group file-list-empty-shell"
                        aria-label="No changed files"
                      >
                        <p class="empty file-list-empty">
                          No files loaded yet.
                        </p>
                      </section>
                    </div>
                  }
                >
                  <div class="directory-groups">
                    <For each={orderedFiles()}>
                      {(file, fileIndex) => {
                        return (
                          <Show when={fileStates()[fileIndex()]}>
                            {(currentState) => (
                              <FileCard
                                state={currentState()}
                                expanded={fileExpanded(
                                  file,
                                  currentState(),
                                  props.state.fileExpansion,
                                )}
                                admitted={admittedFiles[fileIndex()] === true}
                                engine={props.params.engine}
                                view={props.view}
                                aggressiveFolds={aggressiveFolds()}
                                onExpandedChange={(expanded) =>
                                  props.setState(
                                    "fileExpansion",
                                    manifestEntryKey(file.entry),
                                    expanded,
                                  )
                                }
                                onLoad={() => {
                                  props.setState(
                                    "fileExpansion",
                                    manifestEntryKey(file.entry),
                                    true,
                                  );
                                  loadSelectedFile(fileIndex());
                                }}
                              />
                            )}
                          </Show>
                        );
                      }}
                    </For>
                  </div>
                </Show>
              </section>
            </div>
          </>
        )}
      </Show>
    </>
  );
}

/**
 * Defines the structural state shape FileTree consumes from ChangeSet.
 *
 * The tree reads presentation only. Query objects, backend fetch controls, and
 * mutable aggregates are excluded from this contract.
 */
type FileTreeState =
  | {
      state: "husk";
      fileIndex: number;
      name: string;
      path: string;
      activity: "queued" | "fetching";
    }
  | { state: "full"; fileIndex: number; file: FileDiff }
  | {
      state: "lazy";
      fileIndex: number;
      file:
        | { kind: "deferred"; info: LazyInfoFile }
        | { kind: "error"; name: string; path: string; error: Error };
    };

/**
 * Defines all presentation and expansion inputs for the private FileTree.
 *
 * The tree receives the immutable manifest and shared derived file states. It
 * owns no query, backend data, selection, navigation, or expansion authority.
 */
type FileTreeProps = {
  tree: ManifestNode[];
  states: FileTreeState[];
  open: boolean;
  view: DiffViewMode;
  directoryExpansion: Record<string, boolean | undefined>;
  fileExpansion: Record<string, boolean | undefined>;
  onOpenChange: (open: boolean) => void;
  onDirectoryExpandedChange: (
    directory: ManifestDirectory,
    expanded: boolean,
  ) => void;
  onFileExpandedChange: (file: ManifestFile, expanded: boolean) => void;
};

/**
 * Renders the manifest tree and projects progressive file statistics.
 *
 * Visibility controls update only ChangeSet-owned expansion. The tree owns no
 * selected target or navigation state, while its geometry and labels remain
 * stable across progressively available file statistics.
 */
function FileTree(props: FileTreeProps): JSX.Element {
  const files = createMemo(() => manifestFilesInOrder(props.tree));
  const indexByKey = createMemo(
    () =>
      new Map(
        files().map((file, index) => [manifestEntryKey(file.entry), index]),
      ),
  );
  /**
   * Resolves one manifest file to the exact shared ChangeSet projection.
   *
   * Missing indices or states violate the required parallel manifest/state
   * ordering and throw rather than producing an incomplete tree row.
   */
  const stateForFile = (file: ManifestFile): FileTreeState => {
    const index = indexByKey().get(manifestEntryKey(file.entry));
    if (index === undefined) {
      throw new Error(`FileTree cannot index ${fileDisplayName(file.entry)}.`);
    }
    const state = props.states[index];
    if (state === undefined) {
      throw new Error(
        `FileTree is missing state for ${fileDisplayName(file.entry)}.`,
      );
    }
    return state;
  };
  /**
   * Recursively renders one immutable manifest node at its required tree depth.
   *
   * Directories derive presentation-only aggregates; file rows read the shared
   * FileCard state and report expansion changes to ChangeSet.
   */
  const renderNode = (node: ManifestNode, depth: number): JSX.Element => {
    if (node.type === "directory") {
      const expanded = props.directoryExpansion[node.path] ?? true;
      return (
        <section class="file-tree-group">
          <div
            class="file-tree-directory"
            style={{ "--file-tree-depth": String(depth) }}
          >
            <button
              type="button"
              class="file-tree-visibility-toggle"
              aria-label={expanded ? `Fold ${node.path}` : `Show ${node.path}`}
              onClick={() => props.onDirectoryExpandedChange(node, !expanded)}
            >
              <TreeVisibilityIndicator visible={expanded} />
            </button>
            <span class="file-tree-directory-target">{node.name}</span>
            <TreeStatistics
              stats={sumTreeStatistics(
                manifestFilesInOrder(node.entries).map(stateForFile),
              )}
            />
          </div>
          <Show when={expanded}>
            <div
              class="file-tree-children"
              style={{ "--file-tree-depth": String(depth) }}
            >
              <For each={node.entries}>
                {(child) => renderNode(child, depth + 1)}
              </For>
            </div>
          </Show>
        </section>
      );
    }

    const state = stateForFile(node);
    const expanded = fileExpanded(node, state, props.fileExpansion);
    const fileStatus =
      node.entry.file_kind.type === "git"
        ? node.entry.file_kind.status
        : "untracked";
    const lazyReason =
      state.state === "lazy" && state.file.kind === "deferred"
        ? state.file.info.lazy
        : node.entry.lazy;
    return (
      <div
        class="file-tree-file"
        classList={{
          added: fileStatus === "added",
          removed: fileStatus === "deleted",
          renamed: fileStatus === "renamed",
          untracked: fileStatus === "untracked",
          lazy: state.state === "lazy",
          "lazy-error": state.state === "lazy" && state.file.kind === "error",
          "lazy-generated": lazyReason === "generated",
          "lazy-too-big": lazyReason === "too_big",
        }}
        style={{ "--file-tree-depth": String(depth) }}
        title={fileDisplayName(node.entry)}
      >
        <button
          type="button"
          class="file-tree-visibility-toggle"
          aria-label={
            expanded
              ? `Fold ${fileDisplayName(node.entry)}`
              : `Show ${fileDisplayName(node.entry)}`
          }
          onClick={() => props.onFileExpandedChange(node, !expanded)}
        >
          <TreeVisibilityIndicator visible={expanded} />
        </button>
        <span class="file-tree-file-target">
          <span class="file-tree-file-name">{node.name}</span>
          <TreeStatistics stats={treeStatistics(state)} />
        </span>
      </div>
    );
  };

  return (
    <Show when={files().length > 0 || props.view === "inline"}>
      <div
        class="file-tree-shell"
        classList={{
          open: props.open,
          "file-tree-shell-inline": props.view === "inline",
        }}
      >
        <Show when={props.open}>
          <aside
            id="fileTreeSidebar"
            class="file-tree-sidebar"
            aria-label="Changed file tree"
          >
            <div class="file-tree-groups">
              <For each={props.tree}>{(node) => renderNode(node, 0)}</For>
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
            <TreeStatistics stats={sumTreeStatistics(props.states)} />
          </Show>
          <kbd>t</kbd>
        </button>
      </div>
    </Show>
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
   * Active work, unfinished automatic progress, or localized file failures keep
   * the region mounted; a completely successful ready lane relinquishes space.
   */
  const visible = () => {
    if (props.state.state === "loading") {
      return true;
    }
    if (props.state.processed < props.state.automaticTotal) {
      return true;
    }
    return props.state.failed > 0;
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
            >
              <Clock3 aria-hidden="true" />
              <span class="app-header-slow-file-tooltip" role="tooltip">
                {slowFile.path} is taking longer than expected
              </span>
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
 * The projection reads only ManifestSummary. It never accumulates loaded
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
        <span class="delta added">+ {props.summary.added_lines}</span>
        <span class="delta removed">- {props.summary.removed_lines}</span>
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
 * Returns manifest leaves in exact depth-first backend order.
 *
 * The returned array is a derived projection. It contains original ManifestFile
 * objects and must not be sorted, mutated, or retained as another authority.
 */
function manifestFilesInOrder(nodes: ManifestNode[]): ManifestFile[] {
  const files: ManifestFile[] = [];
  for (const node of nodes) {
    if (node.type === "file") {
      files.push(node);
    } else {
      files.push(...manifestFilesInOrder(node.entries));
    }
  }
  return files;
}

/**
 * Reports whether a manifest tree contains at least one intentionally lazy file.
 *
 * The predicate preserves traversal semantics and starts no query itself.
 */
function manifestContainsLazyFiles(nodes: ManifestNode[]): boolean {
  for (const node of nodes) {
    if (node.type === "file") {
      if (node.entry.lazy !== null) {
        return true;
      }
    } else if (manifestContainsLazyFiles(node.entries)) {
      return true;
    }
  }
  return false;
}

/**
 * Returns the required visible name for one manifest or lazy-info handle.
 *
 * Renames retain both paths with the established arrow, while side-only entries
 * use their existing path. A handle with neither path violates the backend file
 * identity contract and throws immediately.
 */
function fileDisplayName(entry: {
  left_path: string | null;
  right_path: string | null;
}): string {
  const leftPath = entry.left_path;
  const rightPath = entry.right_path;
  if (leftPath !== null && leftPath.length > 0) {
    if (rightPath !== null && rightPath.length > 0 && rightPath !== leftPath) {
      return `${leftPath} -> ${rightPath}`;
    }
    return leftPath;
  }
  if (rightPath !== null && rightPath.length > 0) {
    return rightPath;
  }
  throw new Error("File entry requires a non-empty left or right path.");
}

/**
 * Produces one stable manifest-local key from the two canonical file paths.
 *
 * The key distinguishes renames and side-only entries without adding display
 * names or mutable query state. It is used only for expansion and projections.
 */
function manifestEntryKey(entry: {
  left_path: string | null;
  right_path: string | null;
}): string {
  return `${entry.left_path ?? ""}\u0000${entry.right_path ?? ""}`;
}

/**
 * Derives the exact ChangeSet title used by the established frontend.
 *
 * Complete selected DiffParams choose product wording while manifest labels
 * provide authoritative ref display. The function never mutates URL or requests.
 */
function changeSetTitle(params: DiffParams, manifest: Manifest): string {
  switch (params.mode) {
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
    case "preset": {
      const kind =
        params.project_id === "gumtree"
          ? "GumTree"
          : `${params.project_id.charAt(0).toUpperCase()}${params.project_id.slice(1)}`;
      return `${kind} preset ${params.preset_subset}`;
    }
  }
}

/**
 * Resolves one file's expansion from explicit ChangeSet state or FullFile data.
 *
 * Explicit user state always wins. LazyFiles begin expanded because their body
 * contains the only explicit-load affordance. A first FullFile result supplies
 * its backend default expansion; queued HuskFiles remain folded.
 */
function fileExpanded(
  file: ManifestFile,
  state: FileTreeState,
  expansion: Record<string, boolean | undefined>,
): boolean {
  const selected = expansion[manifestEntryKey(file.entry)];
  if (selected !== undefined) {
    return selected;
  }
  if (state.state === "lazy") {
    return true;
  }
  if (state.state === "full") {
    return state.file.default_expanded;
  }
  return false;
}

/**
 * Derives FileTree line statistics from one shared FileCard state.
 *
 * Full and deferred values expose only their actual backend fields. Husk and
 * error states remain unknown rather than manufacturing zeros.
 */
function treeStatistics(state: FileTreeState): TreeLineStats {
  if (state.state === "full") {
    return {
      added: state.file.summary.added_lines,
      modified: state.file.summary.modified_lines,
      removed: state.file.summary.removed_lines,
      moved: state.file.summary.moved_lines,
    };
  }
  if (state.state === "lazy" && state.file.kind === "deferred") {
    return {
      added: state.file.info.added_lines,
      modified:
        state.file.info.added_lines !== null &&
        state.file.info.removed_lines !== null
          ? 0
          : null,
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
function sumTreeStatistics(states: FileTreeState[]): TreeLineStats {
  const stats = states.map(treeStatistics);
  /**
   * Sums one statistic only when every contributing file knows its value.
   *
   * Null propagates as unknown instead of becoming zero, preserving progressive
   * FileTree semantics for queued and failed files.
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

/**
 * Renders one FileTree expansion marker without virtualization state.
 *
 * The marker reflects only the supplied ChangeSet-owned boolean and is inert;
 * its surrounding button owns interaction and accessible naming.
 */
function TreeVisibilityIndicator(props: { visible: boolean }): JSX.Element {
  return (
    <span
      class="visibility-indicator small"
      classList={{ visible: props.visible }}
      aria-hidden="true"
    />
  );
}
