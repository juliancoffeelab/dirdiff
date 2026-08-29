/**
 * Renders a composed `image` bay as its old and new pictures.
 *
 * The browser loads immutable Snapshot media URLs. A whole image File states its
 * byte facts in a separate text bay, while a notebook output stays one image bay.
 * Each present side exposes one pseudo-line so review and line-pin code can use
 * the same File, bay, side, and line coordinate as text content.
 *
 * The widget retains only its rendered DOM and decode error for each mounted
 * side. It publishes the mounted line-preparation operations FileCard needs, but
 * stores no expansion or navigation state and never selects a hunk.
 *
 * ## The review line host
 *
 * An image bay exposes exactly one pseudo-line, numbered 1, on each side with an
 * image representation. That makes a picture commentable without inventing a
 * second review target shape, and it costs this widget the rendered-row DOM
 * contract the rest of the review machinery already reads:
 *
 * - a `.diff-grid` carrying `data-review-bay`, holding a `.diff-lines` element
 *   that answers `prepareLine_impl`, which is how `FileCard` resolves a pin or
 *   History go-to inside one bay;
 * - one `.diff-side` per side, holding a `.line-no` with the side and line
 *   coordinates and a sibling `.line-code`. `ChangeSet` reads the pair back as
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
  /**
   * Materializes the mounted bay representation before line lookup.
   *
   * FileCard calls this operation through the bay wrapper and awaits it before
   * searching for a line host. Image bays are always fully mounted, so this
   * implementation resolves immediately without changing DOM or state. Cleanup
   * removes the operation when the wrapper unmounts.
   */
  waitToEnrich_impl: () => Promise<void>;
};

/**
 * Describes the line-preparation operation the bay's line host exposes.
 *
 * `FileCard` supplies a complete semantic target and its AbortSignal and
 * receives the rendered row, without scrolling, painting, or URL mutation.
 */
type PreparableImageLines = HTMLElement & {
  /**
   * Resolves one semantic line target against this mounted image bay.
   *
   * `target` must address this File and bay. The operation returns the single
   * mounted pseudo-row only for line one on a present side, reports `missing`
   * for absent coordinates, and reports `stopped` after cancellation or disposal.
   * It never scrolls, paints a pin, loads bytes, or selects a hunk. Cleanup removes
   * the operation with the line host.
   *
   * @param target Complete File, bay, side, and line coordinate to prepare.
   * @param abortSignal Navigation cancellation that may stop the lookup.
   */
  prepareLine_impl(
    target: LinePinTarget,
    abortSignal: AbortSignal,
  ): Promise<PreparedLine>;
};

/**
 * Renders one `image` bay as its two picture representations.
 *
 * The sides sit beside each other in Split view and stack in Inline view, the
 * same arrangement the text grid uses, because the question a reviewer asks of
 * two pictures is the one they ask of two texts. A missing representation is
 * explicit: an added image has no old picture, and one side of a notebook
 * output may have text but no PNG.
 */
export function ImageBayView(props: {
  /**
   * Complete captured File pair used by every review and pin coordinate.
   *
   * It must match the containing File response and remains intact across renames;
   * ImageBayView never reconstructs it from media references.
   */
  reviewFile: ReviewFilePair;
  /**
   * Complete enclosing bay envelope carrying public identity and presentation facts.
   *
   * The widget uses its opaque key for DOM, review, and line-pin addressing. Its
   * content arm has already been narrowed separately by FrameView.
   */
  bay: BayPayload;
  /**
   * Narrowed image content containing nullable references for both sides.
   *
   * A null side renders explicit absence and exposes no pseudo-line coordinate;
   * present references describe bytes fetched only through the media URL.
   */
  content: ImageKindPayload;
  /**
   * Current split or inline presentation shared with text grids.
   *
   * It changes side layout only and never affects review coordinates or media URLs.
   */
  view: DiffViewMode;
  /**
   * Snapshot-scoped line-pin interface shared by the containing ChangeSet.
   *
   * The widget reads initial URL state at mount and invokes direct toggle for user
   * line-number activation; it stores no second pin authority.
   */
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

  /**
   * Returns the current image reference for one exact side.
   *
   * The accessor reads the narrowed content reactively. `null` is genuine side
   * absence and controls whether that side receives review or pin coordinates.
   *
   * # Returns
   *
   * - The composed media reference for the requested side.
   * - `null`: That side has no image representation. Pin parsing and review
   *   marker lookup must reject coordinates for it.
   */
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
   * throws. A line other than the pseudo-line, or a side with no image
   * representation, names nothing here and is `missing`. The single row is mounted
   * for this widget's whole lifetime, so the only `stopped` cause is
   * cancellation or disposal.
   *
   * @param target Complete semantic coordinate routed to this bay.
   * @param abortSignal Navigation cancellation checked before returning DOM.
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

  // FileCard and Navigation reach this widget through two mounted DOM
  // operations. Publish both only after their elements mount, paint any existing
  // matching URL pin, and remove the operations before the widget is disposed.
  onMount(() => {
    Object.assign(wrapper, {
      waitToEnrich_impl: () => Promise.resolve(),
    }) satisfies EnrichableImageBay;
    Object.assign(lines, { prepareLine_impl }) satisfies PreparableImageLines;
    // A pin already in the URL when this bay mounts belongs to it whenever it
    // names this bay and a present side; painting it here is the same
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
 * A present image side carries the review coordinate. It contains the line-number
 * cell holding Comment triggers and the code cell where Comment input mounts. It
 * shows the picture itself. An absent representation says so and carries no
 * coordinate: there is nothing there to comment on, and the backend rejects a
 * target naming it. For a notebook output, the File side may exist while its
 * MIME bundle has no PNG.
 */
function ImageSideView(props: {
  /**
   * Narrowed two-sided image content shared by both rendered side components.
   *
   * This component reads only the reference selected by `side` and treats null as
   * genuine absence without borrowing the opposite reference.
   */
  content: ImageKindPayload;
  /**
   * Exact old or new side rendered by this component instance.
   *
   * It selects media, review, line-pin, classes, and accessible wording together;
   * the value never changes meaning within the mount.
   */
  side: "left" | "right";
  /**
   * Complete captured File pair used in media, review, and pin addresses.
   *
   * It is forwarded unchanged and never reduced to the current side's path.
   */
  reviewFile: ReviewFilePair;
  /**
   * Snapshot, File, and bay identity shared with the Review provider.
   *
   * The side and pseudo-line are added only at marker lookup or activation so
   * this binding may serve either rendered side.
   */
  binding: ReviewTextGridBinding;
  /**
   * Handles direct activation of this present side's pseudo-line number.
   *
   * `side` is this component's exact side. The callback runs only when media is
   * present and the click was not consumed by a Comment control; the parent
   * toggles authoritative URL state and repaints the single ChangeSet pin.
   */
  onPin: (side: "left" | "right") => void;
}): JSX.Element {
  const review = useReview();
  const toast = useToasts();
  // One failed decode per mounted side. The browser reports a picture it could
  // not render only through this event, and a silently broken image frame is
  // exactly the "looks empty, actually failed" state review must never show.
  const [decodeFailed, setDecodeFailed] = createSignal(false);
  let codeCell!: HTMLDivElement;

  /**
   * Reads this component's current composed media reference.
   *
   * Null drives explicit absence and removes pin and review coordinates rather
   * than producing a broken media URL.
   *
   * # Returns
   *
   * - The composed media reference for this rendered side.
   * - `null`: This side has no media. The component renders its empty-side state
   *   without a pin coordinate, review coordinate, or media request.
   */
  const mediaRef = (): MediaRef | null =>
    props.side === "left" ? props.content.left : props.content.right;
  // "old" and "new" are what the inline text header calls the two sides; the
  // same two words name them here.
  /**
   * Returns the established user-facing name for this rendered side.
   *
   * The same old/new vocabulary labels inline text sides, media alternatives,
   * pin affordances, and decode errors.
   */
  const sideName = (): string => (props.side === "left" ? "old" : "new");
  /**
   * Reads derived review controls for this exact pseudo-line coordinate.
   *
   * Review remains authoritative for marker availability and state; the image
   * widget does not copy or infer Thread and draft data.
   */
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
        // The coordinate exists only where content does: an absent image has no
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
          fallback={
            <p class="media-absent">No image representation on this side.</p>
          }
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
                  props.binding.bay.bay_key,
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
 * The control names its action: start a Comment, reopen a draft, or open the
 * Threads already recorded here. It hands its own button back on activation,
 * because the Comment input and the Thread panel anchor to that exact element.
 * The wording and classes match the text grid's controls deliberately: the same
 * action at the same kind of coordinate must not be named two ways. That grid
 * builds its controls imperatively against reused DOM, so the few lines of
 * shared vocabulary are restated here rather than pulled into a helper neither
 * renderer could use as it stands.
 */
function CommentTrigger(props: {
  /**
   * Derived review control represented by this exact button.
   *
   * Its discriminant selects classes, count, warning, and activation meaning. The
   * button stores no Thread or draft state outside this descriptor.
   */
  marker: ReviewMarkerDescriptor;
  /**
   * Captured old or new side on which the pseudo-line control is rendered.
   *
   * It contributes accessible wording and is already part of the parent binding
   * used by activation.
   */
  side: "left" | "right";
  /**
   * Whether canonical review state currently forbids marker activation.
   *
   * The native button enforces the value. Disabled controls emit no activation
   * callback and remain visible to explain unavailable review state.
   */
  disabled: boolean;
  /**
   * Handles activation from this exact connected Comment button.
   *
   * `trigger` is the button element that received the click and becomes the
   * anchored UI identity. CommentTrigger stops pin propagation before invoking
   * the callback; the caller opens the descriptor's action and retains later state.
   */
  onActivate: (trigger: HTMLButtonElement) => void;
}): JSX.Element {
  /**
   * Reports whether the marker represents a countable persisted Thread group.
   *
   * New-comment and draft controls remain action labels; open, resolved, and
   * deleted groups render their exact derived count.
   */
  const counted = (): boolean =>
    props.marker.kind === "open" ||
    props.marker.kind === "resolved" ||
    props.marker.kind === "deleted";
  /**
   * Builds the complete visible and accessible action label for this marker.
   *
   * Draft and creation actions use fixed verbs. Persisted groups include their
   * exact count, lifecycle state, and singular or plural Thread wording.
   */
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
