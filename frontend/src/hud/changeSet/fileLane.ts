/**
 * Runs the canonical file-data lifecycle for one mounted ChangeSet snapshot.
 *
 * `createFileLane` receives one immutable manifest order and starts automatic,
 * explicit, and line-target work through a single queue. Measured light engines
 * may overlap a bounded number of fetches, but render admission remains strictly
 * sequential. Each manifest entry has one canonical query and one replaceable
 * Solid view; immutable payloads stay outside reactive state until admitted.
 *
 * The lane retains those views, settled payload slots, admission state, and its
 * scheduler until the creating reactive owner is disposed. `stop()` is
 * idempotent and prevents later writes, admissions, and host callbacks. An
 * ordinary File failure remains local and never stops later manifest entries.
 *
 * DOM, Toasts, URL identity, file expansion, and Navigation stay with the host.
 * The lane calls outward only to apply explicit-load expansion policy and to
 * await a line target's post-admission restoration gate.
 */
import {
  createMemo,
  createSignal,
  onCleanup,
  requestCallback,
  type Accessor,
} from "solid-js";
import { createStore } from "solid-js/store";
import { createQuery, type QueryClient } from "@tanstack/solid-query";
import { isCancelledError, type QueryKey } from "@tanstack/query-core";
import {
  api,
  type DiffEngine,
  type FileDiff,
  type FileDiffTimeout,
  isHeavyEngine,
  type LazyInfoFile,
  type ManifestFile,
  type ManifestNode,
} from "../../api/api";
import { assert, expect } from "../../utils";

/**
 * Delay before the active file becomes visibly slow in AppHeader status.
 *
 * The per-attempt timer is cleared at settlement, so this threshold changes
 * presentation only and never a query timeout or lane scheduling decision.
 */
const SLOW_FILE_THRESHOLD_MS = 8_000;
// Automatic loading overlaps this many upcoming file fetches with the active
// one, so backend latency stops serializing with render admission. Admission
// itself stays strictly sequential in manifest order; the constant bounds
// concurrent requests and undelivered payloads alike. It applies only to
// engines measured to tolerate concurrent backend renders (dirdiff 23% and
// git 14% faster total load at this width); heavy engines never prefetch
// (see `isHeavyEngine`).
/**
 * Maximum upcoming automatic queries overlapped with the active file.
 *
 * The bound applies only to measured non-heavy engines and limits both backend
 * concurrency and payloads waiting for ordered admission. It never permits
 * render admission to pass the automatic cursor.
 */
const AUTOMATIC_PREFETCH_WIDTH = 2;

/**
 * Produces one stable manifest-local key from the two canonical file paths.
 *
 * The key distinguishes renames and side-only entries without adding display
 * names or mutable query state. It is used only for expansion and calculations.
 */
export function manifestEntryKey(entry: {
  /** Old-side manifest path, null for a newly added file. */
  left_path: string | null;
  /** New-side manifest path, null for a deleted file. */
  right_path: string | null;
}): string {
  return `${entry.left_path ?? ""}\u0000${entry.right_path ?? ""}`;
}

/**
 * Returns the required visible name for one manifest or lazy-info handle.
 *
 * Renames retain both paths with the established arrow, while side-only entries
 * use their existing path. API validation guarantees that present paths are
 * non-empty; a handle with neither path violates the file identity contract.
 */
export function fileDisplayName(entry: {
  /** Old-side canonical path, used alone for deletions and unchanged names. */
  left_path: string | null;
  /** New-side canonical path, used alone for additions and paired for renames. */
  right_path: string | null;
}): string {
  const leftPath = entry.left_path;
  const rightPath = entry.right_path;
  if (leftPath !== null) {
    if (rightPath !== null && rightPath !== leftPath) {
      return `${leftPath} -> ${rightPath}`;
    }
    return leftPath;
  }
  if (rightPath !== null) {
    return rightPath;
  }
  return expect(
    entry.left_path ?? entry.right_path,
    "File entry requires a left or right path.",
  );
}

/**
 * Presents one canonical file query's lifecycle without its payload.
 *
 * The QueryClient cache owns transport; the file lane is this view's only
 * writer and records exactly the transitions it causes: fetch or prefetch
 * start ("fetching") and settlement ("success"/"error"). Each view lives in
 * its own replaceable signal so a write swaps the whole value. A Solid
 * store write would merge fields and let a stale `error` survive a later
 * success. Payloads stay out of reactive state so propagation never walks
 * row data (TanStack's own `createQueries` store deep-unwraps every query's
 * rows on every update, which froze loading). `error` is the settled
 * failure exactly as the fetch rejected with it.
 */
type FileQueryView =
  | {
      /** This lane has not started or joined the canonical query. */
      phase: "idle";
    }
  | {
      /** A lane fetch or automatic prefetch for this index is in flight. */
      phase: "fetching";
    }
  | {
      /** The matching immutable payload was written before this phase became visible. */
      phase: "success";
    }
  | {
      /** The one attempted query settled as an ordinary file-local failure. */
      phase: "error";
      /** Original rejection retained for LazyFile presentation and Retry. */
      error: unknown;
    };

/**
 * Describes the ordinary pre-result presentation of one manifest file.
 *
 * A Husk contains only stable manifest presentation and exact queue activity.
 * It must not pretend that per-file statistics or rendered rows are available.
 */
export type HuskFileState = {
  /** Distinguishes queued or fetching presentation from usable file content. */
  state: "husk";
  /** Stable manifest position shared with FileTree, FileCard, and Navigation. */
  fileIndex: number;
  /** Manifest leaf name used by lightweight queued presentation. */
  name: string;
  /** Complete display path used for activity and failure context. */
  path: string;
  /** Whether the query is waiting in lane order or currently in flight. */
  activity: "queued" | "fetching";
};

/**
 * Describes a successfully loaded canonical file query result.
 *
 * The immutable FileDiff is complete and is the sole backend value accepted by
 * FileBody. Manifest or lazy metadata must not be merged into it.
 */
export type FullFileState = {
  /** Marks the only branch whose complete backend FileDiff is available. */
  state: "full";
  /** Stable manifest position whose response name the lane already validated. */
  fileIndex: number;
  /** Immutable canonical payload consumed directly by FileCard renderers. */
  backend_data: FileDiff;
};

/**
 * Describes either intentional delayed-file metadata or a real file failure.
 *
 * Deferred values come only from lazy-info. Error values retain the original
 * thrown Error and stable manifest path so complete local damage remains visible.
 */
export type LazyFile =
  | {
      /** Intentional manifest deferral that may be loaded explicitly. */
      kind: "deferred";
      /** Validated lazy-info metadata, including reason and available statistics. */
      info: LazyInfoFile;
    }
  | {
      /** Ordinary file or lazy-info failure that remains local and retryable. */
      kind: "error";
      /** Manifest leaf name retained when no FileDiff exists. */
      name: string;
      /** Complete display path used in the visible failure. */
      path: string;
      /** Original Error presented by FileCard and reused until the next attempt. */
      error: Error;
    };

/**
 * Describes a file whose content starts only through explicit user activation.
 *
 * Retry and delayed hydration use distinct host-supplied commands because
 * their HTTP timeout policies differ. The state itself contains no query state,
 * timeout policy, or copied loading flag.
 */
export type LazyFileState = {
  /** Marks content that requires explicit activation or Retry. */
  state: "lazy";
  /** Stable manifest position enqueued by the plank or Retry action. */
  fileIndex: number;
  /** Deferred metadata or the exact ordinary failure presented at this index. */
  file: LazyFile;
};

/**
 * Represents every complete canonical presentation branch of one manifest file.
 *
 * The discriminant is derived by the file lane from canonical query state and
 * consumed by FileCard and FileTree alike. Consumers must never manufacture a
 * FullFile for loading or failure placeholders.
 */
export type FileState = HuskFileState | FullFileState | LazyFileState;

/**
 * Describes the active work presented by the single file lane.
 *
 * `kind` distinguishes ordinary manifest progress, an explicit LazyFile
 * selection, and the file currently satisfying a line target. Slow is a
 * one-shot threshold flag rather than elapsed-time state.
 */
export type FileLaneActivity = {
  /** Scheduling source used to label progress and choose restoration ordering. */
  kind: "sequence" | "selected" | "line-target";
  /** Manifest position of the sole active lane attempt. */
  fileIndex: number;
  /** Stable display path shown if the attempt crosses the slow threshold. */
  path: string;
  /** False until the per-attempt presentation timer fires; never elapsed time. */
  slow: boolean;
};

/**
 * Supplies the lane's one optional line goal as pure scheduling input.
 *
 * `fileIndex` is the exact resolved manifest index the ordinary sequence must
 * reach and load (even when the manifest marked it lazy). `restore` is the
 * host's post-admission gate: the lane awaits it once the target file is
 * fetched and admitted, and blocks later file loading until it settles. The
 * lane never interprets its result. Cancellation arrives through the given
 * AbortSignal and lane stop. A thrown restoration failure is terminal:
 * the lane stops itself and rethrows. URL identity, parsing, and toasts stay
 * with the host.
 */
export type FileLaneLineTarget = {
  /**
   * Exact resolved manifest position that takes priority through restoration.
   * The host validates URL identity and uniqueness before constructing the lane.
   */
  fileIndex: number;
  /**
   * Restores the admitted target before later file loading resumes.
   *
   * The lane invokes it only after `fileIndex` has fetched and been admitted.
   * `signal` is aborted when this lane stops. The host may prepare DOM, navigate,
   * Toast, or update the still-current URL and may return any value because the
   * lane uses settlement only as an ordering gate. A rejection is terminal:
   * the lane stops before propagating it to the snapshot boundary.
   */
  restore(signal: AbortSignal): Promise<unknown>;
};

/**
 * Defines the immutable construction boundary of one file lane.
 *
 * Positional arrays share the same manifest indexing for the lane's whole life.
 */
export type FileLaneArgs = {
  /** File-rendering engine included in every canonical file-query key. */
  engine: DiffEngine;
  /** Opaque manifest Snapshot identity used unchanged for lazy-info and files. */
  snapshotId: string;
  /** Flattened, unique manifest leaves in exact backend order. */
  files: readonly ManifestFile[];
  /**
   * Expected FileDiff display name at each corresponding `files` index.
   * Every successful response is asserted against this value before becoming Full.
   */
  canonicalNames: readonly string[];
  /** Shared TanStack client that deduplicates unobserved canonical file queries. */
  queryClient: QueryClient;
  /** Prevalidated line goal, or null when ordinary and explicit scheduling suffice. */
  lineTarget: FileLaneLineTarget | null;
  /**
   * Notifies the host after an explicit or line-target query becomes Full and
   * before its body is admitted.
   *
   * `fileIndex` is the exact successful manifest position. The host may apply
   * its expansion policy synchronously, and the admitted render sees that
   * accepted state after the callback returns. Automatic files do not invoke
   * it. The callback must not enqueue or stop the lane while it is running.
   */
  onExplicitLoad(fileIndex: number): void;
};

/**
 * Exposes the host-facing lifecycle of one immutable snapshot lane.
 *
 * The interface is the only supported path to file presentation and explicit
 * work. Canonical queries, view signals, and payload slots remain private.
 */
export type FileLane = {
  /**
   * Immutable count of manifest entries eligible for automatic attempts.
   * Deferred entries are excluded even if the user later loads them explicitly.
   */
  automaticTotal: number;
  /**
   * Reads one manifest index's current canonical presentation branch.
   *
   * `fileIndex` must come from this lane's immutable file order. The accessor
   * participates in Solid tracking and throws for an unknown index rather than
   * returning a placeholder.
   */
  fileState(fileIndex: number): FileState;
  /**
   * Returns all current canonical states in immutable manifest order.
   * Consumers may derive presentation but must not mutate or cache the array as
   * another backend authority.
   */
  fileStates: Accessor<readonly FileState[]>;
  /**
   * Reports whether the successful payload at `fileIndex` may mount FileBody.
   * False may coexist with a Full state during the deliberate yield before
   * ordered admission; unknown indexes simply have no admission entry.
   */
  admitted(fileIndex: number): boolean;
  /**
   * Returns completed automatic attempts, including ordinary local failures.
   * Explicit loads and line-target retries never increment it.
   */
  processed: Accessor<number>;
  /**
   * Returns the sole active attempt and its slow marker, or null while idle.
   * Queued selections are not reported until the scheduler activates them.
   */
  activity: Accessor<FileLaneActivity | null>;
  /**
   * Returns the first unexpected orchestration failure for the host to throw.
   * Ordinary query failures remain error LazyFiles and never enter this signal.
   */
  error: Accessor<Error | null>;
  /**
   * Submits one LazyFile or failed file to the lane with an explicit timeout
   * policy. Successful and currently-active files are ignored; duplicate queue
   * entries coalesce. It never bypasses sequencing or alters canonical query
   * identity.
   *
   * @param fileIndex Manifest position of the LazyFile or failed file.
   * @param timeout Bounded ordinary-load or unbounded Retry policy for this attempt.
   */
  enqueue(fileIndex: number, timeout: FileDiffTimeout): void;
  /**
   * Stops this exact lane and cancels its active and prefetched canonical
   * queries. Idempotent; concurrent callers share one Promise. New work is
   * prevented synchronously, and an explicit manifest reload must await it.
   */
  stop(): Promise<void>;
};

/**
 * Creates the single file lane of one immutable snapshot and starts it.
 *
 * Must be called during component setup: the lane's signals, memos, and
 * observers live in the calling reactive owner, and disposal of that owner
 * stops the lane. The lane begins loading immediately; a supplied line
 * target constrains scheduling before the first query starts.
 */
export function createFileLane(args: FileLaneArgs): FileLane {
  const { engine, snapshotId, files, canonicalNames, queryClient } = args;
  const [processed, setProcessed] = createSignal(0);
  const [laneActivity, setLaneActivity] = createSignal<FileLaneActivity | null>(
    null,
  );
  const [laneError, setLaneError] = createSignal<Error | null>(null);

  const automaticTotal = files.filter(
    (file) => file.entry.lazy === null,
  ).length;

  // The file list is immutable for this lane, so this setup-time branch
  // creates exactly one lazy-info observer when the entity exists and none
  // when it does not. It is not a reactive zero-or-one observer collection.
  const lazyInfo = files.some((file) => file.entry.lazy !== null)
    ? createQuery(() => api.changeSet.lazyInfo(snapshotId))
    : null;

  // One replaceable view signal per manifest position. A Solid store write
  // merges object fields, which would let a stale `error` field survive a
  // later success and falsify the FileQueryView union, so each view is
  // swapped wholesale through its own signal instead.
  const fileViewSignals = files.map(() =>
    createSignal<FileQueryView>(
      { phase: "idle" },
      {
        // Signals lack the store's write deduplication, and every redundant
        // notification costs a full-manifest fileStates pass, so writes that
        // do not change the observable view must not notify (the lane joins
        // in-flight prefetches and replays recorded failures, both of which
        // re-write the value already present).
        equals: (a, b) =>
          a.phase === b.phase &&
          (a.phase !== "error" || (b.phase === "error" && a.error === b.error)),
      },
    ),
  );

  /**
   * Read the lifecycle view signal for one immutable manifest position.
   *
   * The returned branch participates in Solid tracking but never exposes the
   * settled payload. Unknown indexes throw because sparse view state would make
   * the parallel manifest and payload arrays disagree.
   */
  function fileView(fileIndex: number): FileQueryView {
    return expect(
      fileViewSignals[fileIndex],
      `File lane is missing the view for index ${fileIndex}.`,
    )[0]();
  }

  /**
   * Replaces one exact manifest index's view wholesale.
   *
   * The writer must place a successful payload in `fileDiffs` before publishing
   * `success`. Unknown indexes throw instead of creating sparse lane state.
   *
   * @param fileIndex Immutable manifest position whose signal is replaced.
   * @param view Complete next lifecycle branch caused by this lane.
   */
  function setFileView(fileIndex: number, view: FileQueryView): void {
    expect(
      fileViewSignals[fileIndex],
      `File lane is missing the view for index ${fileIndex}.`,
    )[1](view);
  }

  // Immutable settled payloads, indexed like the view signals. Every write
  // happens before the matching view flips to "success"; the prefetch settle
  // and the lane's join of the same in-flight fetch may both record the
  // identical payload. The canonical query is garbage-collected on settle
  // (gcTime 0, no observers), so this array is the sole surviving reference.
  const fileDiffs: (FileDiff | undefined)[] = files.map(() => undefined);

  const lazyInfoByKey = createMemo(() => {
    const result = new Map<string, LazyInfoFile>();
    const query = lazyInfo;
    if (query === null || query.data === undefined) {
      return result;
    }
    const expected = new Map<string, ManifestFile>();
    for (const file of files) {
      if (file.entry.lazy === null) {
        continue;
      }
      const key = manifestEntryKey(file.entry);
      assert(
        !expected.has(key),
        `Manifest returned duplicate lazy file ${fileDisplayName(file.entry)}.`,
      );
      expected.set(key, file);
    }
    for (const info of query.data.files) {
      const key = manifestEntryKey(info);
      assert(
        expected.has(key),
        `Lazy info returned unexpected file ${fileDisplayName(info)}.`,
      );
      assert(
        !result.has(key),
        `Lazy info returned duplicate file ${fileDisplayName(info)}.`,
      );
      assert(
        info.lazy !== null,
        `Lazy info omitted the reason for ${fileDisplayName(info)}.`,
      );
      result.set(key, info);
    }
    for (const [key, file] of expected) {
      assert(
        result.has(key),
        `Lazy info omitted manifest file ${fileDisplayName(file.entry)}.`,
      );
    }
    return result;
  });

  const fileStateAccessors = files.map((manifestFile, fileIndex) =>
    createMemo<FileState>(() => {
      const view = fileView(fileIndex);
      const path = fileDisplayName(manifestFile.entry);
      if (view.phase === "fetching") {
        return {
          state: "husk" as const,
          fileIndex,
          name: manifestFile.name,
          path,
          activity: "fetching" as const,
        };
      }
      if (view.phase === "success") {
        const backendData = expect(
          fileDiffs[fileIndex],
          `File query view for ${path} settled without its payload.`,
        );
        const canonicalDisplayName = expect(
          canonicalNames[fileIndex],
          `File lane is missing the canonical name for index ${fileIndex}.`,
        );
        assert(
          backendData.display_name === canonicalDisplayName,
          `File query returned ${backendData.display_name} for canonical file ${canonicalDisplayName}.`,
        );
        return {
          state: "full" as const,
          fileIndex,
          backend_data: backendData,
        };
      }
      if (view.phase === "error") {
        assert(
          view.error instanceof Error,
          `File query ${path} failed without an Error value.`,
        );
        return {
          state: "lazy" as const,
          fileIndex,
          file: {
            kind: "error" as const,
            name: manifestFile.name,
            path,
            error: view.error,
          },
        };
      }
      if (manifestFile.entry.lazy !== null) {
        const lazyInfoQuery = lazyInfo;
        if (lazyInfoQuery !== null && lazyInfoQuery.isError) {
          assert(
            lazyInfoQuery.error instanceof Error,
            `Lazy-info query for ${path} failed without an Error value.`,
          );
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
  const fileStates = createMemo(() =>
    fileStateAccessors.map((fileState) => fileState()),
  );

  // Synthetic backpressure set to break the rendering loop and let the main
  // thread breathe.
  const [admittedFiles, setAdmittedFiles] = createStore<Record<number, true>>(
    {},
  );

  const selectedQueue: Array<{
    /** Manifest position awaiting an explicit lane attempt. */
    fileIndex: number;
    /** Attempt-specific HTTP timeout chosen by ordinary Load or Retry. */
    timeout: FileDiffTimeout;
  }> = [];
  const selectedSet = new Set<number>();
  const laneAbortController = new AbortController();
  let lineTarget: {
    /** Prevalidated target and post-admission restoration gate. */
    goal: FileLaneLineTarget;
    /** Pending participates in scheduling; dormant waits for explicit Retry. */
    state: "pending" | "dormant";
    /** Whether this snapshot's single lazy-info observer must settle first. */
    needsLazyInfo: boolean;
  } | null =
    args.lineTarget === null
      ? null
      : {
          goal: args.lineTarget,
          state: "pending",
          needsLazyInfo: lazyInfo !== null,
        };
  let automaticCursor = 0;
  let activeIndex: number | null = null;
  let activeKey: QueryKey | null = null;
  // Query keys of launched automatic prefetches, kept until the lane
  // processes their index so stopping can cancel in-flight ones.
  const prefetchedKeys = new Map<number, QueryKey>();
  let running = false;
  let stopped = false;
  let stopPromise: Promise<void> | null = null;

  /**
   * Routes an unexpected async lane rejection into a Solid error signal.
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
   * Advances one current line goal before queued explicit file selections.
   *
   * A line goal preserves manifest order through its target; afterwards queued
   * user-selected files run before ordinary automatic work resumes. The closure
   * belongs to this exact immutable snapshot. It catches only query-owned
   * failure/cancellation so the canonical observer retains damage; its launch
   * callback routes unexpected orchestration errors into Solid.
   */
  async function runLane(): Promise<void> {
    if (running || stopped) {
      return;
    }
    running = true;
    try {
      while (!stopped) {
        let kind: FileLaneActivity["kind"] = "selected";
        let timeout: FileDiffTimeout = "bounded";
        let currentLineTarget: NonNullable<typeof lineTarget> | null = null;
        let countsAsAutomatic = false;

        if (
          lineTarget !== null &&
          lineTarget.state === "pending" &&
          lineTarget.needsLazyInfo
        ) {
          const preparingTarget = lineTarget;
          const lazyInfoQuery = expect(
            lazyInfo,
            "Line target requires the canonical lazy-info observer.",
          );
          try {
            // Solid exposes QueryObserverResult.promise in its client type, but
            // that Promise only settles with experimental prefetch enabled.
            // Refetching this already-fetching canonical observer with
            // cancellation disabled returns TanStack's same in-flight Promise.
            const lazyInfoResult = await lazyInfoQuery.refetch({
              cancelRefetch: false,
            });
            if (lazyInfoResult.isError) {
              throw lazyInfoResult.error;
            }
            if (lineTarget === preparingTarget) {
              preparingTarget.needsLazyInfo = false;
            }
          } catch (error: unknown) {
            if (isCancelledError(error) || stopped) {
              return;
            }
            if (lineTarget === preparingTarget) {
              preparingTarget.state = "dormant";
              preparingTarget.needsLazyInfo = false;
            }
          }
          continue;
        }

        if (
          lineTarget !== null &&
          fileView(lineTarget.goal.fileIndex).phase === "success" &&
          admittedFiles[lineTarget.goal.fileIndex] === true
        ) {
          const readyTarget = lineTarget;
          try {
            // The host's restore gate runs to completion before later file
            // loading resumes; its result is not the lane's concern because
            // cancellation arrives through the shared signal and lane stop.
            await readyTarget.goal.restore(laneAbortController.signal);
          } catch (error: unknown) {
            // Unexpected restoration failure is terminal local damage. Stop
            // this lane before rethrowing so the pending target cannot
            // relaunch while Solid routes the error to the boundary.
            await stopLane();
            throw error;
          }
          if (lineTarget === readyTarget) {
            lineTarget = null;
          }
          continue;
        }

        let fileIndex: number;
        const pendingLineTarget =
          lineTarget?.state === "pending" ? lineTarget : null;
        if (
          pendingLineTarget !== null &&
          automaticCursor > pendingLineTarget.goal.fileIndex
        ) {
          kind = "line-target";
          currentLineTarget = pendingLineTarget;
          fileIndex = pendingLineTarget.goal.fileIndex;
          timeout =
            fileView(fileIndex).phase === "error" ? "unbounded" : "bounded";
        } else if (pendingLineTarget !== null || selectedQueue.length === 0) {
          kind = "sequence";
          while (automaticCursor < files.length) {
            const candidate = expect(
              files[automaticCursor],
              `File lane lost manifest index ${automaticCursor}.`,
            );
            if (
              candidate.entry.lazy === null ||
              pendingLineTarget?.goal.fileIndex === automaticCursor
            ) {
              break;
            }
            automaticCursor += 1;
          }
          if (automaticCursor >= files.length) {
            break;
          }
          assert(
            pendingLineTarget === null ||
              automaticCursor <= pendingLineTarget.goal.fileIndex,
            "File lane advanced beyond a pending line target.",
          );
          fileIndex = automaticCursor;
          automaticCursor += 1;
          countsAsAutomatic =
            expect(
              files[fileIndex],
              "Automatic file loading selected an invalid manifest index.",
            ).entry.lazy === null;
          if (pendingLineTarget?.goal.fileIndex === fileIndex) {
            kind = "line-target";
            currentLineTarget = pendingLineTarget;
            timeout =
              fileView(fileIndex).phase === "error" ? "unbounded" : "bounded";
          }
        } else {
          const selection = expect(
            selectedQueue.shift(),
            "Selected file queue lost its first entry.",
          );
          fileIndex = selection.fileIndex;
          timeout = selection.timeout;
          selectedSet.delete(fileIndex);
        }

        const file = expect(
          files[fileIndex],
          `File lane selected invalid index ${fileIndex}.`,
        );
        if (kind === "sequence") {
          // Launch a bounded number of upcoming automatic fetches so the
          // network overlaps this file's fetch and admission. Their errors
          // and results land on the same canonical queries the lane reads
          // when it reaches those indexes. Heavy engines never
          // prefetch: see AUTOMATIC_PREFETCH_WIDTH.
          const prefetchWidth = isHeavyEngine(engine)
            ? 0
            : AUTOMATIC_PREFETCH_WIDTH;
          let inFlight = 0;
          for (
            let ahead = automaticCursor;
            ahead < files.length && inFlight < prefetchWidth;
            ahead += 1
          ) {
            const candidate = files[ahead];
            if (candidate === undefined || candidate.entry.lazy !== null) {
              continue;
            }
            const aheadView = fileView(ahead);
            if (aheadView.phase === "success" || aheadView.phase === "error") {
              continue;
            }
            if (aheadView.phase === "fetching") {
              inFlight += 1;
              continue;
            }
            const aheadOptions = api.changeSet.file(
              engine,
              snapshotId,
              candidate.entry,
              "bounded",
            );
            prefetchedKeys.set(ahead, aheadOptions.queryKey);
            setFileView(ahead, { phase: "fetching" });
            // The settled query is garbage-collected immediately (gcTime 0,
            // no observers), so this handler is what records the payload;
            // when the lane reaches the file first it joins this exact
            // in-flight fetch and records the same settlement.
            void queryClient.fetchQuery(aheadOptions).then(
              (data) => {
                if (stopped) return;
                fileDiffs[ahead] = data;
                setFileView(ahead, { phase: "success" });
              },
              (error: unknown) => {
                // Cancellation needs no branch here: prefetches are only
                // cancelled by stopLane, which sets `stopped` first and
                // always precedes this snapshot's teardown or replacement,
                // so the view frozen at "fetching" is never presented.
                if (stopped) return;
                setFileView(ahead, { phase: "error", error });
              },
            );
            inFlight += 1;
          }
        }
        const options = api.changeSet.file(
          engine,
          snapshotId,
          file.entry,
          timeout,
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
        // Each active attempt gets one presentation-only timer. Its callback
        // reads the immutable attempt index and marks activity slow only if that
        // same attempt is still active. The `finally` block clears the timer on
        // success, failure, or cancellation, so settled files cannot later
        // rewrite lane activity.
        const slowTimer = window.setTimeout(() => {
          if (!stopped && activeIndex === fileIndex) {
            setLaneActivity({ ...activity, slow: true });
          }
        }, SLOW_FILE_THRESHOLD_MS);

        try {
          const view = fileView(fileIndex);
          if (kind === "sequence" && view.phase === "error") {
            // A prefetched attempt already failed and this view presents
            // that failure; the automatic pass makes exactly one attempt
            // per file, so it does not fetch again.
            throw view.error;
          }
          if (view.phase !== "success") {
            // A settled query is garbage-collected immediately (gcTime 0,
            // no observers), so a "success" view means the payload already
            // lives in `fileDiffs` and fetching again would hit the
            // network, not the cache.
            setFileView(fileIndex, { phase: "fetching" });
            const data = await queryClient.fetchQuery(options);
            if (stopped) {
              return;
            }
            fileDiffs[fileIndex] = data;
            setFileView(fileIndex, { phase: "success" });
          }
          if (kind === "selected" || kind === "line-target") {
            // The query result has replaced LazyFile with FullFile; the host
            // applies its expansion policy now, before admission.
            args.onExplicitLoad(fileIndex);
          }
          // Yield one continuation through Solid's cooperative scheduler so
          // the main thread can process browser work before admission. The
          // scheduled callback has no side effects and needs no cancellation.
          await new Promise<void>((resolve) => {
            requestCallback(resolve);
          });
          if (stopped) {
            return;
          }
          setAdmittedFiles(fileIndex, true);
          if (currentLineTarget !== null && lineTarget === currentLineTarget) {
            // Admission synchronously mounts FullFile. Wait through the next
            // paint so Navigation receives complete measurable TextDiffGrid rows.
            await new Promise<void>((resolve) => {
              requestAnimationFrame(() => resolve());
            });
          }
        } catch (error) {
          if (stopped || isCancelledError(error)) {
            return;
          }
          // Record the failed attempt on the file's view (replaying a
          // recorded prefetch failure re-writes the same value, which the
          // signal's equality drops). The lane intentionally proceeds so
          // one file cannot damage later files.
          setFileView(fileIndex, { phase: "error", error });
          if (currentLineTarget !== null && lineTarget === currentLineTarget) {
            currentLineTarget.state = "dormant";
          }
        } finally {
          window.clearTimeout(slowTimer);
          activeIndex = null;
          activeKey = null;
          prefetchedKeys.delete(fileIndex);
          setLaneActivity(null);
          if (countsAsAutomatic && !stopped) {
            setProcessed((current) => current + 1);
          }
        }
      }
    } finally {
      running = false;
      if (
        !stopped &&
        (selectedQueue.length > 0 || lineTarget?.state === "pending")
      ) {
        void runLane().catch(reportLaneFailure);
      }
    }
  }

  /**
   * Stops this exact immutable lane and cancels its active canonical query.
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
    laneAbortController.abort();
    setLaneActivity(null);
    const queryKey = activeKey;
    const cancellations = [
      ...(queryKey === null
        ? []
        : [queryClient.cancelQueries({ queryKey, exact: true })]),
      ...Array.from(prefetchedKeys.values(), (prefetchedKey) =>
        queryClient.cancelQueries({ queryKey: prefetchedKey, exact: true }),
      ),
    ];
    stopPromise = Promise.all(cancellations).then(() => undefined);
    return stopPromise;
  }

  /**
   * Queues one explicit load or Retry on the lane's single scheduler.
   *
   * Successful and currently active indexes need no new work; repeated queued
   * indexes coalesce without replacing the first timeout policy. A stopped lane
   * rejects new work because disposal cannot accept an operation it will not run.
   * After insertion, `runLane` preserves line-target priority and executes this
   * entry between automatic files.
   *
   * @param fileIndex Manifest position selected through LazyFile or Retry UI.
   * @param timeout Bounded ordinary-load or unbounded Retry policy for this attempt.
   */
  function enqueue(fileIndex: number, timeout: FileDiffTimeout): void {
    // A stopped lane is being disposed or replaced; accepting the operation
    // would silently drop it, so the broken expectation stays visible.
    assert(!stopped, "Cannot load a file after its lane stopped.");
    const file = expect(
      files[fileIndex],
      `Cannot load unknown file index ${fileIndex}.`,
    );
    if (fileView(fileIndex).phase === "success" || activeIndex === fileIndex) {
      return;
    }
    if (!selectedSet.has(fileIndex)) {
      selectedSet.add(fileIndex);
      selectedQueue.push({ fileIndex, timeout });
    }
    void runLane().catch(reportLaneFailure);
  }

  // The host resolved any line target synchronously before creating the lane,
  // so the target already constrains the lane before its first query begins.
  void runLane().catch(reportLaneFailure);

  // The reactive owner and lane have identical lifetimes. Cleanup calls the
  // idempotent stop operation so active and prefetched queries are cancelled,
  // the restoration AbortSignal aborts, and every later view write is suppressed.
  // Any unexpected cancellation failure still reaches the lane error signal.
  onCleanup(() => {
    void stopLane().catch(reportLaneFailure);
  });

  return {
    automaticTotal,
    fileState(fileIndex: number): FileState {
      return expect(
        fileStateAccessors[fileIndex],
        `File lane is missing the state for index ${fileIndex}.`,
      )();
    },
    fileStates,
    admitted(fileIndex: number): boolean {
      return admittedFiles[fileIndex] === true;
    },
    processed,
    activity: laneActivity,
    error: laneError,
    enqueue,
    stop: stopLane,
  };
}

/**
 * Returns manifest leaves in exact depth-first backend order.
 *
 * The returned array is a derived calculation. It contains original ManifestFile
 * objects and must not be sorted, mutated, or retained as another authority.
 */
export function manifestFilesInOrder(
  nodes: readonly ManifestNode[],
): ManifestFile[] {
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
