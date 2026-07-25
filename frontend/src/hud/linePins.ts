/**
 * Defines URL-backed exact-line identity and restoration for one ChangeSet snapshot.
 *
 * The module exports complete line-pin contracts and the `linePins()` factory.
 * Each factory call uses the current Navigation and Toast providers, retains
 * only cancellation for its active restoration, and returns operations for URL
 * parsing, direct URL toggling, and admitted-file restoration. It must not render
 * a component, observe browser history, fetch or admit files, paint rows, select
 * hunks, inspect query state, or create another authoritative pin identity.
 */
import { useToasts } from "../comp/Toasts";
import { useNavigation } from "./navigation";

const LINE_PIN_NOTICE_DURATION_MS = 2_000;

/**
 * Represents one complete URL line-pin identity.
 *
 * `file` is the canonical ChangeSet display path. Ordinary text requires
 * `region: null`; notebook source requires one non-empty backend cell key.
 * `side` identifies the old or new side and `line` is one positive backend line
 * number serialized as canonical decimal text. The type never represents DOM,
 * loading, decoration, hunk identity, or a partially parsed target.
 */
export type LinePinTarget = {
  file: string;
  region: string | null;
  side: "left" | "right";
  line: string;
};

/**
 * Describes every validated interpretation of the URL's `pin` hash field.
 *
 * `none` means the field is absent. `invalid` means the user-controlled field
 * violates the complete target contract. `valid` carries the exact semantic
 * target. Callers must preserve the distinction and must not repair invalid
 * identity.
 */
export type ParsedLinePin =
  | { state: "none" }
  | { state: "invalid" }
  | { state: "valid"; target: LinePinTarget };

/**
 * Describes the exact result of one direct URL toggle.
 *
 * `pinned` means the supplied target replaced the previous pin. `unpinned`
 * means the URL already contained that exact valid target and the field was
 * removed. The result says nothing about rendered decoration.
 */
export type LinePinToggleResult = "pinned" | "unpinned";

/**
 * Describes the terminal result of restoring one admitted target file.
 *
 * `complete` means Navigation reached the exact row. `missing` means the
 * complete current file no longer contains that coordinate and LinePins removed
 * the still-current URL target after notifying the user. `stopped` means target
 * replacement, snapshot disposal, or Navigation disposal forbade the final
 * action. Exceptions remain exceptions and are never converted into this union.
 */
export type LinePinRestoration =
  | { state: "complete" }
  | { state: "missing" }
  | { state: "stopped" };

/**
 * Describes the result of preparing one semantic line inside a mounted FullFile.
 *
 * `ready` supplies the unique complete rendered row. `missing` means the
 * complete current file does not contain the requested coordinate. `stopped`
 * means cancellation or FileCard disposal prevented completion. Structural DOM
 * contradictions throw and must not be represented as `missing`.
 */
export type PreparedLine =
  | { state: "ready"; row: HTMLElement }
  | { state: "missing" }
  | { state: "stopped" };

/**
 * Exposes the complete per-snapshot line-pin interface.
 *
 * Callers may parse the current URL, toggle one complete direct target, or
 * restore an admitted target through Navigation. The instance owns only the
 * active restoration controller. It must not be shared across ChangeSetSnapshot
 * lifetimes or used as a general URL, loading, or decoration service.
 */
export type LinePins = {
  parseUrl(): ParsedLinePin;
  toggleUrlState(target: LinePinTarget): LinePinToggleResult;
  restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration>;
};

/**
 * Constructs one line-pin interface beneath the current Navigation and Toast providers.
 *
 * `ChangeSetSnapshot` calls this exactly once and passes the returned instance
 * to every DiffGrid in that snapshot. Callers provide complete semantic targets,
 * valid manifest indices, and the snapshot AbortSignal. The returned operations
 * preserve unrelated URL fields, cancel replaced restoration, and never create
 * another file-loading or painting path.
 */
export function linePins(): LinePins {
  const navigation = useNavigation();
  const toast = useToasts();
  let activeRestoration: AbortController | null = null;

  /**
   * Compares two complete semantic targets without consulting rendered DOM.
   */
  function targetsEqual(left: LinePinTarget, right: LinePinTarget): boolean {
    return (
      left.file === right.file &&
      left.region === right.region &&
      left.side === right.side &&
      left.line === right.line
    );
  }

  /**
   * Parses the current hash without accepting missing, extra, or repaired fields.
   */
  function parseUrl(): ParsedLinePin {
    const encodedPins = new URLSearchParams(
      window.location.hash.slice(1),
    ).getAll("pin");
    if (encodedPins.length === 0) {
      return { state: "none" };
    }
    if (encodedPins.length !== 1) {
      return { state: "invalid" };
    }
    const encoded = encodedPins[0];
    if (encoded === undefined) {
      throw new Error("A single line-pin field omitted its value.");
    }
    try {
      const parsed: unknown = JSON.parse(encoded);
      if (typeof parsed !== "object" || parsed === null) {
        return { state: "invalid" };
      }
      const fields = parsed as Record<string, unknown>;
      const keys = Object.keys(fields).sort();
      if (
        keys.length !== 4 ||
        keys[0] !== "file" ||
        keys[1] !== "line" ||
        keys[2] !== "region" ||
        keys[3] !== "side" ||
        typeof fields.file !== "string" ||
        fields.file.length === 0 ||
        (fields.region !== null &&
          (typeof fields.region !== "string" || fields.region.length === 0)) ||
        (fields.side !== "left" && fields.side !== "right") ||
        typeof fields.line !== "string" ||
        !/^[1-9]\d*$/u.test(fields.line)
      ) {
        return { state: "invalid" };
      }
      return {
        state: "valid",
        target: {
          file: fields.file,
          region: fields.region,
          side: fields.side,
          line: fields.line,
        },
      };
    } catch {
      return { state: "invalid" };
    }
  }

  /**
   * Cancels restoration and replaces only the URL's exact line-pin field.
   */
  function toggleUrlState(target: LinePinTarget): LinePinToggleResult {
    activeRestoration?.abort();
    activeRestoration = null;
    const current = parseUrl();
    const unpin =
      current.state === "valid" && targetsEqual(current.target, target);
    const retainedHashFields: string[] = [];
    for (const field of window.location.hash.slice(1).split("&")) {
      if (field.length === 0) {
        continue;
      }
      const fieldParameters = new URLSearchParams(field);
      if (fieldParameters.has("pin")) {
        continue;
      }
      retainedHashFields.push(field);
    }
    if (!unpin) {
      retainedHashFields.push(
        `pin=${encodeURIComponent(JSON.stringify(target))}`,
      );
    }
    const encodedHash = retainedHashFields.join("&");
    history.replaceState(
      history.state,
      "",
      `${window.location.pathname}${window.location.search}${
        encodedHash.length === 0 ? "" : `#${encodedHash}`
      }`,
    );
    return unpin ? "unpinned" : "pinned";
  }

  /**
   * Restores one admitted target through coordinate-bearing Navigation.
   *
   * The caller supplies the exact manifest index and snapshot lifetime.
   * Replacement aborts any older restoration. The operation verifies current
   * URL identity before Navigation and before handling a missing coordinate.
   * Unexpected failures reject to the caller's ChangeSet boundary.
   */
  async function restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration> {
    if (!Number.isInteger(fileIndex) || fileIndex < 0) {
      throw new Error("Line-pin restoration requires a manifest file index.");
    }
    activeRestoration?.abort();
    const abortController = new AbortController();
    activeRestoration = abortController;

    /**
     * Ends this restoration when its owning ChangeSetSnapshot is disposed.
     */
    function abortFromChangeSet(): void {
      abortController.abort(changeSetAbortSignal.reason);
    }

    if (changeSetAbortSignal.aborted) {
      abortFromChangeSet();
    } else {
      changeSetAbortSignal.addEventListener("abort", abortFromChangeSet, {
        once: true,
      });
    }

    try {
      const current = parseUrl();
      if (
        abortController.signal.aborted ||
        current.state !== "valid" ||
        !targetsEqual(current.target, target)
      ) {
        return { state: "stopped" };
      }
      const result = await navigation.navigate({
        kind: "line",
        fileIndex,
        target,
        abortSignal: abortController.signal,
      });
      if (result.state === "stopped" || abortController.signal.aborted) {
        return { state: "stopped" };
      }
      if (result.state === "complete") {
        return { state: "complete" };
      }
      const afterNavigation = parseUrl();
      if (
        afterNavigation.state !== "valid" ||
        !targetsEqual(afterNavigation.target, target)
      ) {
        return { state: "stopped" };
      }
      toast.showTransient(
        "Line pin unavailable",
        `${target.file}:${target.line} is not present in the current file.`,
        LINE_PIN_NOTICE_DURATION_MS,
      );
      const toggleResult = toggleUrlState(target);
      if (toggleResult !== "unpinned") {
        throw new Error("Missing line-pin removal changed its current target.");
      }
      return { state: "missing" };
    } finally {
      changeSetAbortSignal.removeEventListener("abort", abortFromChangeSet);
      if (activeRestoration === abortController) {
        activeRestoration = null;
      }
    }
  }

  return { parseUrl, toggleUrlState, restore };
}
