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
  type EngineConfig,
  type FileDiff,
  type FileDiffTimeout,
  isHeavyEngine,
  type LazyInfoFile,
  type ManifestFile,
  type ManifestNode,
} from "../../api/api";
import { assert, expect } from "../../utils";

const SLOW_FILE_THRESHOLD_MS = 8_000;
// Automatic loading overlaps this many upcoming file fetches with the active
// one, so backend latency stops serializing with render admission. Admission
// itself stays strictly sequential in manifest order; the constant bounds
// concurrent requests and undelivered payloads alike. It applies only to
// engines measured to tolerate concurrent backend renders (dirdiff 23% and
// git 14% faster total load at this width); heavy engines never prefetch
// (see `isHeavyEngine`).
const AUTOMATIC_PREFETCH_WIDTH = 2;

export function manifestEntryKey(entry: {
  left_path: string | null;
  right_path: string | null;
}): string {
  return `${entry.left_path ?? ""}\u0000${entry.right_path ?? ""}`;
}

export function fileDisplayName(entry: {
  left_path: string | null;
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

type FileQueryView =
  | {
      phase: "idle";
    }
  | {
      phase: "fetching";
    }
  | {
      phase: "success";
    }
  | {
      phase: "error";
      error: unknown;
    };

export type HuskFileState = {
  state: "husk";
  fileIndex: number;
  name: string;
  path: string;
  activity: "queued" | "fetching";
};

export type FullFileState = {
  state: "full";
  fileIndex: number;
  backend_data: FileDiff;
};

export type LazyFile =
  | {
      kind: "deferred";
      info: LazyInfoFile;
    }
  | {
      kind: "error";
      name: string;
      path: string;
      error: Error;
    };

export type LazyFileState = {
  state: "lazy";
  fileIndex: number;
  file: LazyFile;
};

export type FileState = HuskFileState | FullFileState | LazyFileState;

export type FileLaneActivity = {
  kind: "sequence" | "selected" | "line-target";
  fileIndex: number;
  path: string;
  slow: boolean;
};

export type FileLaneLineTarget = {
  fileIndex: number;
  restore(signal: AbortSignal): Promise<unknown>;
};

export type FileLaneArgs = {
  engine: EngineConfig;
  snapshotId: string;
  files: readonly ManifestFile[];
  canonicalNames: readonly string[];
  queryClient: QueryClient;
  lineTarget: FileLaneLineTarget | null;
  onExplicitLoad(fileIndex: number): void;
};

export type FileLane = {
  automaticTotal: number;
  fileState(fileIndex: number): FileState;
  fileStates: Accessor<readonly FileState[]>;
  admitted(fileIndex: number): boolean;
  processed: Accessor<number>;
  activity: Accessor<FileLaneActivity | null>;
  error: Accessor<Error | null>;
  enqueue(fileIndex: number, timeout: FileDiffTimeout): void;
  stop(): Promise<void>;
};

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

  function fileView(fileIndex: number): FileQueryView {
    return expect(
      fileViewSignals[fileIndex],
      `File lane is missing the view for index ${fileIndex}.`,
    )[0]();
  }

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
    fileIndex: number;
    timeout: FileDiffTimeout;
  }> = [];
  const selectedSet = new Set<number>();
  const laneAbortController = new AbortController();
  let lineTarget: {
    goal: FileLaneLineTarget;
    state: "pending" | "dormant";
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

  function reportLaneFailure(error: unknown): void {
    setLaneError(
      error instanceof Error
        ? error
        : new Error("File lane rejected without an Error value.", {
            cause: error,
          }),
    );
  }

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
                if (stopped) {
                  return;
                }
                fileDiffs[ahead] = data;
                setFileView(ahead, { phase: "success" });
              },
              (error: unknown) => {
                // Cancellation needs no branch here: prefetches are only
                // cancelled by stopLane, which sets `stopped` first and
                // always precedes this snapshot's teardown or replacement,
                // so the view frozen at "fetching" is never presented.
                if (stopped) {
                  return;
                }
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

  function enqueue(fileIndex: number, timeout: FileDiffTimeout): void {
    // A stopped lane is being disposed or replaced; accepting the operation
    // would silently drop it, so the broken expectation stays visible.
    assert(!stopped, "Cannot load a file after its lane stopped.");
    expect(files[fileIndex], `Cannot load unknown file index ${fileIndex}.`);
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
