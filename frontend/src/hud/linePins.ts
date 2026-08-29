/**
 * Maintains the URL-backed exact-line pin for one mounted ChangeSet snapshot.
 *
 * `linePins()` parses and writes the single `pin` hash field, paints only the
 * currently connected matching row, and restores an admitted target through
 * Navigation. A new toggle or snapshot disposal aborts the prior restoration.
 * Invalid identity remains invalid and is never repaired or replaced.
 *
 * The file lane decides when a target File is fetched and admitted. This module
 * does not observe history, select hunks, or retain a second pin identity.
 */
import { z } from "zod";

import { ReviewFilePairSchema, ReviewTextBaySchema } from "../api/api";
import { useToasts } from "../comp/Toasts";
import { assert, expect } from "../utils";
import { useNavigation } from "./navigation";

/**
 * Keeps malformed or missing-pin notices visible long enough to read without
 * turning URL restoration damage into a persistent Toast.
 */
const LINE_PIN_NOTICE_DURATION_MS = 2_000;

/**
 * Validates the complete JSON value stored in the URL's single `pin` field.
 *
 * The shared review schemas keep File-pair and bay identity identical across
 * line pins and Threads. The side-specific path requirement is checked after
 * parsing because the File-pair schema requires only one side to exist.
 */
const LinePinTargetSchema = z.strictObject({
  /** Exact old/new path pair shared with Snapshot review targets. */
  file: ReviewFilePairSchema,
  /** Public composed bay key containing the pinned line. */
  bay: ReviewTextBaySchema,
  /**
   * Captured File side whose numbered line is pinned.
   * `parseUrl` rejects the value unless the matching path in `file` is present.
   */
  side: z.enum(["left", "right"]),
  /** Positive backend line number stored as canonical decimal text. */
  line: z.string().regex(/^[1-9]\d*$/u),
});

/**
 * Represents one complete URL line-pin identity.
 *
 * The identity uses the same File pair and bay schemas as review Threads, so a
 * renamed File keeps one pin identity and flat files carry their conventional
 * bay key. The selected side's path must be present. This type never represents
 * DOM, loading, decoration, hunk identity, or a partially parsed target.
 */
export type LinePinTarget = z.infer<typeof LinePinTargetSchema>;

/**
 * Describes every validated interpretation of the URL's `pin` hash field.
 *
 * `none` means the field is absent. `invalid` means the user-controlled field
 * violates the complete target contract. `valid` carries the exact semantic
 * target. Callers must preserve the distinction and must not repair invalid
 * identity.
 */
export type ParsedLinePin =
  | {
      /** No `pin` field is present in the current hash. */
      state: "none";
    }
  | {
      /** The hash has duplicate fields, invalid JSON, or an invalid target. */
      state: "invalid";
    }
  | {
      /** The hash contains exactly one complete target accepted by the schema. */
      state: "valid";
      /** Exact semantic coordinate decoded from the current URL. */
      target: LinePinTarget;
    };

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
  | {
      /** Navigation centered the exact prepared row. */
      state: "complete";
    }
  | {
      /** The admitted file lacked the coordinate; the still-current pin was removed. */
      state: "missing";
    }
  | {
      /** Replacement or disposal ended restoration without URL or scroll work. */
      state: "stopped";
    };

/**
 * Describes the result of preparing one semantic line inside a mounted FullFile.
 *
 * `ready` supplies the unique complete rendered row. `missing` means the
 * complete current file does not contain the requested coordinate. `stopped`
 * means cancellation or FileCard disposal prevented completion. Structural DOM
 * contradictions throw and must not be represented as `missing`.
 */
export type PreparedLine =
  | {
      /** File and bay preparation completed and found one connected row. */
      state: "ready";
      /** Complete rendered row Navigation may measure before its final scroll. */
      row: HTMLElement;
    }
  | {
      /** The complete admitted file has no row at the supplied coordinate. */
      state: "missing";
    }
  | {
      /** Cancellation or FileCard disposal prevented a trustworthy result. */
      state: "stopped";
    };

/**
 * Exposes the complete per-snapshot line-pin interface.
 *
 * Callers may parse the current URL, toggle one complete direct target, or
 * restore an admitted target through Navigation. The instance owns only the
 * active restoration controller. It must not be shared across ChangeSetSnapshot
 * lifetimes or used as a general URL, loading, or decoration service.
 */
export type LinePins = {
  /**
   * Parses the current URL at call time.
   *
   * It distinguishes absence from invalid user-controlled identity and never
   * repairs, removes, or retains the result. Callers decide how to present an
   * invalid field and must not treat it as no pin.
   *
   * Malformed JSON, invalid fields, duplicate `pin` fields, and a target whose
   * selected side has no path all return `invalid`; none of them throw or alter
   * the hash.
   */
  parseUrl(): ParsedLinePin;
  /**
   * Toggles one exact semantic target in the current URL.
   *
   * `target` replaces any other pin, or removes the field when it matches the
   * current valid pin. The call first aborts active restoration, preserves the
   * path, query, and unrelated hash fields, then synchronously returns whether
   * the supplied target is now pinned. It does not navigate or repaint rows;
   * the activating bay applies its own decoration after this returns.
   *
   * @param target Complete semantic coordinate to install or remove.
   *
   * # Failures
   *
   * A browser history write failure propagates after the active restoration has
   * already been stopped.
   */
  toggleUrlState(target: LinePinTarget): LinePinToggleResult;
  /**
   * Restores one admitted file's exact line through Navigation.
   *
   * `target` must still be the URL's current valid pin. `fileIndex` is its
   * unique manifest index, and `changeSetAbortSignal` ends work when the owning
   * snapshot is disposed. A newer restoration replaces the older one. The
   * Promise settles only after navigation completes or stops; a missing row is
   * announced and the pin is removed only if the same target is still current.
   * Callers must await the result before allowing later lane work to proceed.
   *
   * @param target Exact URL target that must remain current during restoration.
   * @param fileIndex Unique manifest index already loaded and admitted by the lane.
   * @param changeSetAbortSignal Lifetime of the snapshot that supplied the file.
   */
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
 * to every TextDiffGrid in that snapshot. Callers provide complete semantic targets,
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
   *
   * @param left Existing parsed or retained semantic coordinate.
   * @param right Candidate coordinate being compared with `left`.
   */
  function targetsEqual(left: LinePinTarget, right: LinePinTarget): boolean {
    return (
      left.file.left_path === right.file.left_path &&
      left.file.right_path === right.file.right_path &&
      left.bay.bay_key === right.bay.bay_key &&
      left.side === right.side &&
      left.line === right.line
    );
  }

  /**
   * Parses the current hash without accepting missing, extra, or repaired fields.
   *
   * The helper reads all `pin` fields at call time. It returns `none` for genuine
   * absence, `invalid` for duplicate fields or any decoding and schema failure,
   * and `valid` only when the selected File side has a path. It has no URL, Toast,
   * navigation, or retained-state side effects.
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
    const encoded = expect(
      encodedPins[0],
      "A single line-pin field omitted its value.",
    );
    // Only the decode can throw; everything after it is a total function on
    // the decoded value, so the `try` covers exactly the throwing statement.
    let decoded: unknown;
    try {
      decoded = JSON.parse(encoded);
    } catch {
      return { state: "invalid" };
    }
    const parsed = LinePinTargetSchema.safeParse(decoded);
    if (!parsed.success) {
      return { state: "invalid" };
    }
    const target = parsed.data;
    // A pin lives on one side, so the pair's path on that side must exist.
    // The pair itself only guarantees that one of the two is present.
    const sidePath =
      target.side === "left" ? target.file.left_path : target.file.right_path;
    if (sidePath === null) {
      return { state: "invalid" };
    }
    return { state: "valid", target };
  }

  /**
   * Cancels restoration and replaces only the URL's exact line-pin field.
   *
   * Cancellation happens first, even when the following browser history write
   * fails. The rewrite removes every prior `pin` field, preserves other hash
   * fields in their existing order and spelling, and appends the encoded target
   * unless the current valid pin matches it. `replaceState` does not navigate or
   * dispatch a history event.
   *
   * @param target Complete semantic coordinate to install or remove.
   *
   * # Failures
   *
   * Browser history write failures propagate after cancellation.
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
   * Restores one admitted target by passing its coordinate to Navigation.
   *
   * The caller supplies the exact manifest index and snapshot lifetime.
   * Replacement aborts any older restoration. The operation verifies current
   * URL identity before Navigation and before handling a missing coordinate.
   * Unexpected failures reject to the caller's ChangeSet boundary.
   *
   * @param target Exact URL target that must remain current during restoration.
   * @param fileIndex Unique manifest index already loaded and admitted by the lane.
   * @param changeSetAbortSignal Lifetime of the snapshot that supplied the file.
   */
  async function restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration> {
    assert(
      Number.isInteger(fileIndex) && fileIndex >= 0,
      "Line-pin restoration requires a manifest file index.",
    );
    activeRestoration?.abort();
    const abortController = new AbortController();
    activeRestoration = abortController;

    /**
     * Ends this restoration when its owning ChangeSetSnapshot is disposed.
     *
     * The callback copies the snapshot signal's abort reason into this operation's
     * replaceable controller. It runs directly for an already-aborted snapshot or
     * once from the temporary listener below; the operation's `finally` block
     * removes that listener.
     */
    function abortFromChangeSet(): void {
      abortController.abort(changeSetAbortSignal.reason);
    }

    // The listener bridges snapshot disposal into the replaceable per-restore
    // controller. It lives only for this Promise and the `finally` block always
    // removes it, even when Navigation rejects. `once` also releases it as soon
    // as snapshot cancellation fires.
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
      const sidePath = expect(
        target.side === "left" ? target.file.left_path : target.file.right_path,
        "A line pin names a side its File pair does not have.",
      );
      toast.showTransient(
        "Line pin unavailable",
        `${sidePath}:${target.line} is not present in the current file.`,
        LINE_PIN_NOTICE_DURATION_MS,
      );
      const toggleResult = toggleUrlState(target);
      assert(
        toggleResult === "unpinned",
        "Missing line-pin removal changed its current target.",
      );
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
