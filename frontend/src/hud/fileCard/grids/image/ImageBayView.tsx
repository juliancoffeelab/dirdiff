/**
 * Renders the bay whose content is a picture: `image`.
 *
 * The module exports one component, `ImageBayView`, and is the home of the
 * picture-shaped bay widget the way `grids/text/` is the home of the row-shaped
 * one. It shows the two captured sides as pictures and nothing else: what is
 * known *about* the bytes — media type, size, digest — is a `text` bay composed
 * beside this one, so those facts arrive as real diffed rows a comment can land
 * on rather than as a caption here.
 *
 * Callers provide the composed bay, its already-narrowed `image` content, the
 * File pair every review and pin coordinate is addressed by, the current view
 * mode, and the snapshot's shared line-pin interface. The widget owns its
 * rendered DOM, the one pseudo-line each captured side exposes, and the two DOM
 * operations `FileCard` reaches it through. It fetches no JSON, owns no
 * expansion or navigation state, writes no hunk anchor — its bay's single stop
 * is written by the bay chrome around it — and never calls `selectHunk()`.
 *
 * Bytes never arrive in the payload. Each captured side is an `/api/file-media`
 * URL the browser fetches, decodes, and caches itself; a Snapshot id is never
 * reused, so those URLs are immutable. This is the only widget that fetches
 * anything.
 *
 * ## The review line host
 *
 * An image bay exposes exactly one pseudo-line, numbered 1, on each side it was
 * captured on. That is what makes a picture commentable without inventing a
 * second review target shape, and it costs this widget the rendered-row DOM
 * contract the rest of the review machinery already reads:
 *
 * - a `.diff-grid` carrying `data-review-bay`, holding a `.diff-lines` element
 *   that answers `prepareLine_impl` — how `FileCard` resolves a pin or a
 *   History go-to inside one bay;
 * - one `.diff-side` per side, holding a `.line-no` with the side and line
 *   coordinates and a sibling `.line-code` — the pair `ChangeSet` reads back as
 *   a Comment anchor and `Review` mounts the Comment input beside.
 *
 * Those class names are the review host contract, not text decoration. A widget
 * that wants comments implements them; nothing else in the review path learns
 * that image bays exist.
 */
import {
  Index,
  Show,
  createSignal,
  onCleanup,
  onMount,
  type JSX,
} from "solid-js";

import {
  fileMediaUrl,
  type BayPayload,
  type ImageKindPayload,
  type MediaRef,
  type ReviewFilePair,
} from "../../../../api/api";
import type { DiffViewMode } from "../../../App";
import type { LinePins, LinePinTarget, PreparedLine } from "../../../linePins";
import { assert, expect } from "../../../../utils";
import { useToasts } from "../../../../comp/Toasts";
import {
  useReview,
  type ReviewMarkerDescriptor,
  type ReviewTextGridBinding,
} from "../../../review/Review";

/**
 * The one line coordinate an image bay has.
 *
 * A picture holds no lines, so the single line a review target or a pin may
 * name against it describes nothing inside it. The number is fictional on
 * purpose: it buys one target shape through validation, placement, History, and
 * every frontend path that handles a target, and the backend rejects any range
 * other than this one against a non-text bay.
 */
const PSEUDO_LINE = 1;

/**
 * Describes the enrichment operation every mounted bay wrapper exposes.
 *
 * `FileCard` calls it before searching a bay for a pinned line. An image bay has
 * no deferred representation to materialize, so its implementation resolves
 * immediately: the lines it hosts are mounted for the wrapper's whole lifetime.
 */
type EnrichableImageBay = HTMLElement & {
  waitToEnrich_impl: () => Promise<void>;
};

/**
 * Describes the line-preparation operation the bay's line host exposes.
 *
 * `FileCard` supplies a complete semantic target and its AbortSignal and
 * receives the rendered row, without scrolling, painting, or URL mutation.
 */
type PreparableImageLines = HTMLElement & {
  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

/**
 * Renders one `image` bay as its two captured pictures.
 *
 * The sides sit beside each other in Split view and stack in Inline view, the
 * same arrangement the text grid uses, because the question a reviewer asks of
 * two pictures is the one they ask of two texts. A side the File was not
 * captured on renders as explicitly absent: an added image has no old picture,
 * and an empty pane would read as a blank one.
 */
export function ImageBayView(props: {
  reviewFile: ReviewFilePair;
  bay: BayPayload;
  content: ImageKindPayload;
  view: DiffViewMode;
  linePins: LinePins;
}): JSX.Element {
  // Each ref holds the plain element and is converted at the one place its
  // caller-facing operation is attached, so the DOM interfaces above describe
  // the contract without the refs pretending to satisfy it before mount.
  let wrapper!: HTMLElement;
  let lines!: HTMLElement;
  let row!: HTMLElement;
  const review = useReview();
  const bayKey = props.bay.bay_key;
  const binding: ReviewTextGridBinding = {
    snapshot_id: review.snapshotId,
    file: props.reviewFile,
    bay: { bay_key: bayKey },
  };

  /** Returns the captured reference for one side, or null when it is absent. */
  const sideRef = (side: "left" | "right"): MediaRef | null =>
    side === "left" ? props.content.left : props.content.right;

  /**
   * Toggles this bay's pin for one side and repaints the ChangeSet's one pin.
   *
   * The URL is the authority: the class below decorates whatever the toggle
   * decided. The operation scrolls nothing, loads nothing, and selects no hunk.
   */
  function togglePin(side: "left" | "right"): void {
    const target: LinePinTarget = {
      file: props.reviewFile,
      bay: { bay_key: bayKey },
      side,
      line: String(PSEUDO_LINE),
    };
    const changeSetRoot = expect(
      row.closest<HTMLElement>("[data-change-set-root]"),
      "Image bay requires its ChangeSet root.",
    );
    const paintedRows =
      changeSetRoot.querySelectorAll<HTMLElement>(".pinned-line");
    assert(
      paintedRows.length <= 1,
      "ChangeSet contains multiple painted line pins.",
    );
    paintedRows[0]?.classList.remove("pinned-line");
    if (props.linePins.toggleUrlState(target) === "pinned") {
      row.classList.add("pinned-line");
    }
  }

  /**
   * Answers one exact pin or review target inside this bay.
   *
   * A target naming another File or another bay is a routing contradiction and
   * throws. A line other than the pseudo-line, or a side this File was not
   * captured on, names nothing here and is `missing`. The single row is mounted
   * for this widget's whole lifetime, so the only `stopped` cause is
   * cancellation or disposal.
   */
  async function prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine> {
    assert(
      target.file.left_path === props.reviewFile.left_path &&
        target.file.right_path === props.reviewFile.right_path &&
        target.bay.bay_key === bayKey,
      "Image bay received a line target from another bay.",
    );
    // Nothing is fetched, unfolded, or enriched to answer this, so the result
    // is already known; the operation is asynchronous because it is one
    // implementation of the line host every bay widget offers.
    await Promise.resolve();
    if (abortSignal.aborted || !lines.isConnected) {
      return { state: "stopped" };
    }
    if (target.line !== String(PSEUDO_LINE) || sideRef(target.side) === null) {
      return { state: "missing" };
    }
    return { state: "ready", row };
  }

  onMount(() => {
    Object.assign(wrapper, {
      waitToEnrich_impl: () => Promise.resolve(),
    }) satisfies EnrichableImageBay;
    Object.assign(lines, { prepareLine_impl }) satisfies PreparableImageLines;
    // A pin already in the URL when this bay mounts belongs to it whenever it
    // names this bay and a captured side; painting it here is the same
    // restoration the text grid performs once it has rendered its rows.
    const parsed = props.linePins.parseUrl();
    if (
      parsed.state === "valid" &&
      parsed.target.file.left_path === props.reviewFile.left_path &&
      parsed.target.file.right_path === props.reviewFile.right_path &&
      parsed.target.bay.bay_key === bayKey &&
      parsed.target.line === String(PSEUDO_LINE) &&
      sideRef(parsed.target.side) !== null
    ) {
      row.classList.add("pinned-line");
    }
    onCleanup(() => {
      Reflect.deleteProperty(wrapper, "waitToEnrich_impl");
      Reflect.deleteProperty(lines, "prepareLine_impl");
    });
  });

  return (
    <div
      class="media-bay"
      ref={(element) => {
        wrapper = element;
      }}
      data-bay-key={bayKey}
    >
      <div class="diff-grid media-grid" data-review-bay={bayKey}>
        <div
          class="diff-lines media-lines"
          ref={(element) => {
            lines = element;
          }}
        >
          <div
            class="diff-row media-row"
            classList={{ "media-row-inline": props.view === "inline" }}
            ref={(element) => {
              row = element;
            }}
          >
            <ImageSideView
              content={props.content}
              side="left"
              reviewFile={props.reviewFile}
              binding={binding}
              onPin={togglePin}
            />
            <ImageSideView
              content={props.content}
              side="right"
              reviewFile={props.reviewFile}
              binding={binding}
              onPin={togglePin}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Renders one side of an image bay, with its pseudo-line coordinate.
 *
 * A captured side carries the review coordinate — the line-number cell holding
 * the Comment triggers, and the code cell the Comment input mounts beside — and
 * shows the picture itself. An absent side says so and carries no coordinate:
 * there is nothing there to comment on, and the backend rejects a target naming
 * it.
 */
function ImageSideView(props: {
  content: ImageKindPayload;
  side: "left" | "right";
  reviewFile: ReviewFilePair;
  binding: ReviewTextGridBinding;
  onPin: (side: "left" | "right") => void;
}): JSX.Element {
  const review = useReview();
  const toast = useToasts();
  // One failed decode per mounted side. The browser reports a picture it could
  // not render only through this event, and a silently broken image frame is
  // exactly the "looks empty, actually failed" state review must never show.
  const [decodeFailed, setDecodeFailed] = createSignal(false);
  let codeCell!: HTMLDivElement;

  const mediaRef = (): MediaRef | null =>
    props.side === "left" ? props.content.left : props.content.right;
  // "old" and "new" are what the inline text header calls the two sides; the
  // same two words name them here.
  const sideName = (): string => (props.side === "left" ? "old" : "new");
  const markerState = () =>
    review.markerState(props.binding, props.side, PSEUDO_LINE);

  return (
    <div
      class="diff-side media-side"
      classList={{
        "side-left": props.side === "left",
        "side-right": props.side === "right",
        "empty-side": mediaRef() === null,
      }}
    >
      <div
        class="line-no media-line-no"
        // The coordinate exists only where content does: an absent side has no
        // pseudo-line, so it is neither pinnable nor commentable.
        data-line-pin-side={mediaRef() === null ? undefined : props.side}
        data-line-pin-line={
          mediaRef() === null ? undefined : String(PSEUDO_LINE)
        }
        title={
          mediaRef() === null ? undefined : `Pin the ${sideName()} content`
        }
        onClick={(event) => {
          if (mediaRef() === null) {
            return;
          }
          // The Comment triggers sit inside this cell and stop their own
          // clicks, so anything still reaching here is a pin.
          event.stopPropagation();
          props.onPin(props.side);
        }}
      >
        <Show when={mediaRef() !== null}>
          <span class="line-comment-triggers">
            {/* Indexed, not keyed: a marker whose kind or count changes must
                re-decorate its existing button. Replacing the element would
                disconnect an active Comment input or Thread panel anchored to
                it, which the next anchored-UI sweep then closes. */}
            <Index each={markerState().markers}>
              {(marker) => (
                <CommentTrigger
                  marker={marker()}
                  side={props.side}
                  disabled={markerState().disabled}
                  onActivate={(trigger) => {
                    review.activateTextCommentInput(
                      props.binding,
                      props.side,
                      PSEUDO_LINE,
                      { codeCell, trigger },
                      marker().kind,
                      // A bay with one pseudo-line has no range to extend, so
                      // the shift modifier means nothing here.
                      false,
                    );
                  }}
                />
              )}
            </Index>
          </span>
        </Show>
      </div>
      <div
        class="line-code media-cell"
        ref={(element) => {
          codeCell = element;
        }}
      >
        <span class="media-side-name">{sideName()}</span>
        <Show
          when={mediaRef()}
          keyed
          fallback={<p class="media-absent">Not captured on this side.</p>}
        >
          {(reference) => (
            <Show
              when={!decodeFailed()}
              fallback={
                <p class="media-absent">This image could not be displayed.</p>
              }
            >
              <img
                class="media-image"
                src={fileMediaUrl(
                  props.binding.snapshot_id,
                  props.reviewFile,
                  props.side,
                )}
                alt={`The ${sideName()} side of this image`}
                onError={() => {
                  setDecodeFailed(true);
                  toast.showError(
                    "Could not display image",
                    new Error(
                      `The ${sideName()} side did not decode as ${reference.media_type}.`,
                    ),
                  );
                }}
              />
            </Show>
          )}
        </Show>
      </div>
    </div>
  );
}

/**
 * Renders one review control at an image bay's pseudo-line.
 *
 * The control says what it does — start a Comment, reopen a draft, or open the
 * Threads already recorded here — and hands its own button back on activation,
 * because the Comment input and the Thread panel anchor to that exact element.
 * The wording and classes match the text grid's controls deliberately: the same
 * action at the same kind of coordinate must not be named two ways. That grid
 * builds its controls imperatively against reused DOM, so the few lines of
 * shared vocabulary are restated here rather than pulled into a helper neither
 * renderer could use as it stands.
 */
function CommentTrigger(props: {
  marker: ReviewMarkerDescriptor;
  side: "left" | "right";
  disabled: boolean;
  onActivate: (trigger: HTMLButtonElement) => void;
}): JSX.Element {
  const counted = (): boolean =>
    props.marker.kind === "open" ||
    props.marker.kind === "resolved" ||
    props.marker.kind === "deleted";
  const actionLabel = (): string => {
    const marker = props.marker;
    if (marker.kind === "draft") {
      return "Draft";
    }
    if (marker.kind === "new") {
      return "Add comment";
    }
    const state =
      marker.kind === "resolved"
        ? "Resolved"
        : marker.kind === "deleted"
          ? "Deleted"
          : "Open";
    return `${marker.count} ${state} Thread${marker.count === 1 ? "" : "s"}`;
  };

  return (
    <button
      type="button"
      class="line-comment-trigger"
      classList={{
        "line-comment-trigger-commented": counted(),
        "line-comment-trigger-draft": props.marker.kind === "draft",
        "line-comment-trigger-open": props.marker.kind === "open",
        "line-comment-trigger-resolved": props.marker.kind === "resolved",
        "line-comment-trigger-deleted": props.marker.kind === "deleted",
        "line-comment-trigger-warning":
          "warning" in props.marker && props.marker.warning,
      }}
      data-review-marker-kind={props.marker.kind}
      disabled={props.disabled}
      title={actionLabel()}
      aria-label={`${actionLabel()} on the ${props.side === "left" ? "old" : "new"} content`}
      onClick={(event) => {
        // The pin handler wraps this control; a review action is not a pin.
        event.stopPropagation();
        const trigger = event.currentTarget;
        assert(
          trigger instanceof HTMLButtonElement,
          "Comment trigger must activate from its own button.",
        );
        props.onActivate(trigger);
      }}
    >
      <span class="line-comment-trigger-icon" aria-hidden="true" />
      <span class="line-comment-trigger-label">
        {counted() && "count" in props.marker
          ? String(props.marker.count)
          : actionLabel()}
      </span>
    </button>
  );
}
