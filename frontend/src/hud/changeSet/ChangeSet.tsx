/**
 * Presents one retained Tab selection as replaceable ChangeSet snapshots.
 *
 * The outer `ChangeSet` retains file expansion, Help, and History state while
 * inactive. Active content observes one manifest and replaces the whole mounted
 * snapshot on reload. Each successful snapshot creates exactly one file lane,
 * resolves any URL line target, and mounts FileCards before writing initial hunk
 * selection.
 *
 * Backend results remain in TanStack Query and the lane's canonical views.
 * Workspace and Tab selection stay above this boundary; scrolling, line-pin
 * identity, and FileTree presentation stay in their dedicated modules.
 */
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
import { FileCard } from "../fileCard/FileCard";
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
import { ReviewProvider, type ReviewCodeAnchor } from "../review/Review";
import {
  calculateDirectoryExpansion,
  fileExpanded,
  FileTree,
} from "./FileTree";
import { ChangeSetShell, type HunkDisplay } from "./Shell";

/**
 * Defines the complete inputs and workspace callbacks of one ChangeSet.
 *
 * Tabs supplies the selected diff parameters, engine, and Profile together
 * with workspace display state passed down from App. The callbacks write that
 * display state at its source. This object never represents partially edited
 * controls or backend query state.
 */
type ChangeSetProps = {
  /** False keeps local expansion, Help, and History state but mounts no queries or files. */
  active: boolean;
  /** Complete selected Tab value sent unchanged to the manifest operation. */
  params: DiffParams;
  /** File-rendering choice, deliberately excluded from manifest and Room identity. */
  engine: DiffEngine;
  /** Workspace display mode applied reactively without replacing the manifest. */
  view: DiffViewMode;
  /** Workspace-wide sidebar visibility retained across Tabs and snapshots. */
  fileTreeOpen: boolean;
  /** Workspace-wide developer HUD visibility retained across Tabs and snapshots. */
  debugHudOpen: boolean;
  /** Selected review author, or null when review actions have no signed-in Profile. */
  profile: StoredProfile | null;
  /** Stable AppHeader destinations that receive this active snapshot's status Portals. */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Performs the workspace's inline/split toggle after a shell action.
   *
   * The shell invokes it for `i` and the corresponding UI action, with no
   * argument because the caller already stores the current mode. ChangeSet
   * reads the accepted `view` value back through props on the next render.
   */
  onToggleView: () => void;
  /**
   * Replaces workspace FileTree visibility with `open`.
   *
   * ChangeSet calls it after tree toggles with the complete desired boolean;
   * the caller stores that value and returns it through `fileTreeOpen`. It is
   * not called for private FileTree scrolling or file expansion.
   */
  onFileTreeOpenChange: (open: boolean) => void;
  /**
   * Replaces workspace DebugHud visibility with `open`.
   *
   * The shell calls it only for the explicit Debug action. The caller stores
   * the accepted value and returns it through `debugHudOpen`; metric sampling
   * neither invokes this callback nor changes workspace state.
   */
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
  /**
   * Explicit per-file expansion keyed by the manifest path pair.
   *
   * `undefined` delegates to the current file state's initial policy. Boolean
   * entries survive Tab inactivity and engine replacement, but reload clears
   * the whole map before constructing the replacement snapshot.
   */
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
      /** Work is active, so `active` identifies the one lane operation in flight. */
      state: "loading";
      /** Completed automatic attempts, including files that failed locally. */
      processed: number;
      /** Immutable count of non-lazy manifest entries attempted automatically. */
      automaticTotal: number;
      /** Current error LazyFiles; a successful explicit retry removes one. */
      failed: number;
      /** Current automatic, selected, or line-target lane operation. */
      active: FileLaneActivity;
    }
  | {
      /** No file operation is active, though explicit work may be queued later. */
      state: "ready";
      /** Completed automatic attempts at the moment the lane became idle. */
      processed: number;
      /** Immutable denominator for automatic progress. */
      automaticTotal: number;
      /** Current error LazyFiles that remain available for Retry. */
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
  /** Immutable selection that keys this active manifest observer. */
  params: DiffParams;
  /** Current renderer choice forwarded to a successful keyed snapshot. */
  engine: DiffEngine;
  /** Reactive workspace layout forwarded without refetching the manifest. */
  view: DiffViewMode;
  /** Current workspace sidebar value used by both loading/error and snapshot shells. */
  fileTreeOpen: boolean;
  /** Current workspace DebugHud value used by both loading/error and snapshot shells. */
  debugHudOpen: boolean;
  /** Current review author forwarded only after manifest success. */
  profile: StoredProfile | null;
  /** Stable AppHeader Portal destinations used by a successful snapshot. */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Runs the workspace view toggle for the shell's explicit action.
   * The outer caller returns the accepted mode through `view` afterwards.
   */
  onToggleView: () => void;
  /** Current ChangeSet-local History visibility retained by the outer boundary. */
  historyOpen: boolean;
  /**
   * Replaces local History visibility with `open` after a hotkey or review action.
   * The outer ChangeSet stores the value and returns it through `historyOpen`.
   */
  onHistoryOpenChange: (open: boolean) => void;
  /** Current ChangeSet-local Help visibility retained while content unmounts. */
  helpOpen: boolean;
  /**
   * Replaces local Help visibility with `open` after hotkey, button, or backdrop.
   * The outer ChangeSet stores the value and returns it through `helpOpen`.
   */
  onHelpOpenChange: (open: boolean) => void;
  /**
   * Replaces workspace FileTree visibility with the shell's desired value.
   * The workspace returns the accepted value through `fileTreeOpen`.
   */
  onFileTreeOpenChange: (open: boolean) => void;
  /**
   * Replaces workspace DebugHud visibility with the shell's desired value.
   * The workspace returns the accepted value through `debugHudOpen`.
   */
  onDebugHudOpenChange: (open: boolean) => void;
  /** ChangeSet-local expansion state retained outside this active query lifetime. */
  state: ChangeSetState;
  /**
   * Solid store writer for the retained state.
   *
   * Content uses it to clear all expansion before reload; mounted descendants
   * write only exact file keys. Accepted writes are visible through `state`
   * before later expansion calculations run.
   */
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
 * Defines the host boundary for one immutable manifest snapshot.
 *
 * Backend identity is fixed for the component lifetime. Presentation values may
 * change without retargeting its lane or query definitions.
 */
type ChangeSetSnapshotProps = {
  /** Complete selected Tab value used to interpret snapshot-specific naming. */
  params: DiffParams;
  /** Renderer identity that keys the nested file-lane lifetime. */
  engine: DiffEngine;
  /** Immutable successful manifest and opaque Snapshot identity for this boundary. */
  manifest: Manifest;
  /** Reactive layout mode consumed by mounted files and review presentation. */
  view: DiffViewMode;
  /** Reactive workspace sidebar visibility; file expansion remains separate. */
  fileTreeOpen: boolean;
  /** Selected review author, or null for signed-out Snapshot presentation. */
  profile: StoredProfile | null;
  /** Stable AppHeader elements receiving snapshot status and summary Portals. */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Reads the latest complete display mirror derived from mounted FileCard DOM.
   *
   * Snapshot presentation calls it reactively for counters and FileTree
   * highlighting. Null means the observer has not published a trustworthy
   * calculation yet. Consumers must not turn the value back into navigation or
   * retain it as selected-hunk state.
   */
  hunkDisplay: Accessor<HunkDisplay | null>;
  /** Retained ChangeSet expansion values shared by FileTree and FileCards. */
  state: ChangeSetState;
  /**
   * Writes exact keys in the outer ChangeSet's retained expansion store.
   *
   * Snapshot descendants call it synchronously after file or directory actions.
   * The accepted values return through `state` before dependent expansion memos
   * rerun. It must not write backend state, selection, or workspace visibility.
   */
  setState: SetStoreFunction<ChangeSetState>;
  /**
   * Replaces workspace FileTree visibility after an explicit shell or tree action.
   * The workspace returns the accepted value through `fileTreeOpen`.
   */
  onFileTreeOpenChange(open: boolean): void;
  /**
   * Publishes the current lane's idempotent stop operation to ChangeSetContent.
   *
   * A mounted snapshot sends its exact `stop`; disposal sends null. The host
   * stores the value only so Reload can synchronously stop and then await the
   * old lane before refetching. The host must not call it for ordinary hiding.
   */
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
  /** ChangeSet-local History visibility shared across engine replacements. */
  historyOpen: boolean;
  /**
   * Replaces History visibility with `open` after review or hotkey actions.
   * The outer ChangeSet stores it and supplies the accepted value back through
   * `historyOpen`; it is not a review transport or navigation callback.
   */
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
  /**
   * Publishes the complete set of manifest indexes currently backed by FullFiles.
   *
   * The snapshot invokes it after lane state changes and once with an empty set
   * on cleanup. Review retains the replacement set for go-to enablement only.
   * Neither side mutates a published set, and the callback must not load,
   * expand, or navigate a File.
   */
  onFileNavigabilityChange(indexes: ReadonlySet<number>): void;
  /**
   * Receives the concrete inline History slot after that element mounts.
   *
   * Solid invokes it with the third grid child in inline view; Review uses the
   * element as its Portal target after the callback returns. It is not invoked
   * in split view, whose History ignores the retained accessor value; returning
   * to inline view publishes the newly mounted slot before its Portal renders.
   */
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

  /**
   * Return the unique immutable manifest index addressed by one Thread point.
   *
   * The File pair was indexed at boundary construction. A missing pair is a
   * review/Snapshot contradiction and throws rather than disabling navigation
   * as though the Thread were merely unlocated.
   */
  function reviewFileIndex(point: ThreadCodePoint): number {
    return expect(
      reviewFileIndexes.get(
        JSON.stringify([point.file.left_path, point.file.right_path]),
      ),
      "A located review Thread requires one exact manifest File.",
    );
  }

  /**
   * Report whether the current engine-bound lane exposes the Thread's FullFile.
   *
   * Review calls this to enable its go-to action. It reads the replacement set
   * published by the lane and never loads, expands, or navigates the File.
   */
  function canViewReviewThread(point: ThreadCodePoint): boolean {
    return navigableFileIndexes().has(reviewFileIndex(point));
  }

  /**
   * Navigate to one loaded Thread line and return its mounted review anchor.
   *
   * The point supplies the exact File pair, bay, side, and line. Navigation
   * prepares and centers it without selecting a hunk; after completion this
   * function queries the same stable shell root for the unique code cell and
   * marker. Disposal returns null, while missing or contradictory DOM remains an
   * exception for the review boundary.
   *
   * # Returns
   *
   * - A connected code cell and its visible review marker after navigation
   *   completes.
   * - `null`: Navigation stopped because this ChangeSet was disposed. Review
   *   must leave anchored Thread UI closed.
   */
  async function viewReviewThread(
    point: ThreadCodePoint,
  ): Promise<ReviewCodeAnchor | null> {
    const fileIndex = reviewFileIndex(point);
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
    // The navigation context serves the stable Shell root, mounted in both
    // views; the inline History slot exists only in inline view and must not
    // stand in for it.
    const changeSetRoot = navigation.root();
    const card = changeSetRoot.querySelector<HTMLElement>(
      `[data-file-card][data-file-index="${fileIndex}"]`,
    );
    assert(card !== null, "Review navigation lost its loaded FileCard.");
    const matchingGrids = [
      ...card.querySelectorAll<HTMLElement>(".diff-grid"),
    ].filter((grid) => grid.dataset.reviewBay === bay.bay_key);
    assert(
      matchingGrids.length === 1,
      "Review navigation requires one exact rendered bay.",
    );
    const matchingLines = expect(
      matchingGrids[0],
      "Reviewed bay disappeared after navigation.",
    ).querySelectorAll<HTMLElement>(
      `.line-no[data-line-pin-side="${point.side}"][data-line-pin-line="${point.line}"]`,
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
  // The surrounding ChangeSet uses this exact stop operation to await the lane
  // before manifest reload. Clear the publication when the snapshot unmounts so
  // a later reload cannot stop an already disposed lane.
  props.onFileSequenceChange(lane.stop);
  onCleanup(() => props.onFileSequenceChange(null));

  // Initial hunk selection belongs to this mounted snapshot. FileCards have
  // mounted by the time this runs, and the surrounding NavigationProvider
  // survives engine-keyed snapshot replacement, so the provider cannot
  // initialize a replacement snapshot's fresh DOM.
  onMount(() => writeInitialHunkSelection(changeSetRoot));

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

  // ReviewProvider must react when lane queries replace Husk or Lazy states
  // with FullFiles, but the lane deliberately knows nothing about review. This
  // effect derives a fresh immutable set from `lane.fileStates()` after each
  // canonical state transition and hands it across that boundary. It lives for
  // this engine-keyed snapshot; cleanup publishes the empty set so the longer
  // lived review boundary cannot keep enabling destinations from a disposed lane.
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
 * The compact area contributes no title or long path to AppHeader layout. Once
 * automatic work succeeds with no failures it renders nothing and relinquishes
 * its physical space.
 */
function AppHeaderFileStatus(props: {
  /** Current compact lane progress derived from canonical activity and file states. */
  state: FileSequenceState;
}): JSX.Element {
  /**
   * Reports whether compact file-lane status currently has visible information.
   *
   * Visible automatic progress, localized failures, or an active slow marker
   * keep the area mounted. Activity without one of those children must not
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
function ManifestStatistics(props: {
  /** Immutable backend aggregate for the manifest, never reconstructed from loaded files. */
  summary: ManifestSummary;
}): JSX.Element {
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

/**
 * Derives the exact ChangeSet title used by the established frontend.
 *
 * Complete selected DiffParams choose product wording while manifest labels
 * provide authoritative ref display. The function never mutates URL or requests.
 *
 * @param params Complete selected Tab value that determines the title form.
 * @param manifest Immutable result supplying authoritative ref labels.
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
