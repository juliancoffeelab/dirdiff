import { z } from "zod";

import { ReviewFilePairSchema, ReviewTextBaySchema } from "../api/api";
import { useToasts } from "../comp/Toasts";
import { assert, expect } from "../utils";
import { useNavigation } from "./navigation";
import type { LineCell } from "./fileCard/FileCard";

const LINE_PIN_NOTICE_DURATION_MS = 2_000;

const LinePinTargetSchema = z.strictObject({
  file: ReviewFilePairSchema,
  bay: ReviewTextBaySchema,
  side: z.enum(["left", "right"]),
  // 1-based line number as canonical decimal text.
  line: z.string().regex(/^[1-9]\d*$/u),
});

export type LinePinTarget = z.infer<typeof LinePinTargetSchema>;

export type ParsedLinePin =
  | {
      state: "none";
    }
  | {
      state: "invalid";
    }
  | {
      state: "valid";
      target: LinePinTarget;
    };

export type LinePinToggleResult = "pinned" | "unpinned";

export type LinePinRestoration =
  | {
      state: "complete";
    }
  | {
      state: "missing";
    }
  | {
      state: "stopped";
    };

export type PreparedLine =
  | {
      state: "ready";
      cell: LineCell;
    }
  | {
      state: "missing";
    }
  | {
      state: "stopped";
    };

export type LinePins = {
  parseUrl(): ParsedLinePin;
  toggleUrlState(target: LinePinTarget): LinePinToggleResult;
  restore(
    target: LinePinTarget,
    fileIndex: number,
    changeSetAbortSignal: AbortSignal,
  ): Promise<LinePinRestoration>;
};

export function linePins(): LinePins {
  const navigation = useNavigation();
  const toast = useToasts();
  let activeRestoration: AbortController | null = null;

  function targetsEqual(left: LinePinTarget, right: LinePinTarget): boolean {
    return (
      left.file.left_path === right.file.left_path &&
      left.file.right_path === right.file.right_path &&
      left.bay.bay_key === right.bay.bay_key &&
      left.side === right.side &&
      left.line === right.line
    );
  }

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

  function toggleUrlState(target: LinePinTarget): LinePinToggleResult {
    // Abort first: the history write below may throw.
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
    // replaceState neither navigates nor fires a history event.
    history.replaceState(
      history.state,
      "",
      `${window.location.pathname}${window.location.search}${
        encodedHash.length === 0 ? "" : `#${encodedHash}`
      }`,
    );
    return unpin ? "unpinned" : "pinned";
  }

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
