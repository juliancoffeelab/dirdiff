import {
  Show,
  createEffect,
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
import type { LinePins } from "../../../linePins";
import { useNavigation } from "../../../navigation";
import { assert } from "../../../../utils";
import { useToasts } from "../../../../comp/Toasts";
import {
  paintCommentTriggers,
  useReview,
  type ReviewTextGridBinding,
} from "../../../review/Review";
import type { BayHandle } from "../../FrameView";
import { paintLinePin, type LineCell, type Side } from "../text/rowDom";

const PSEUDO_LINE = 1;

export function ImageBayView(props: {
  reviewFile: ReviewFilePair;
  bay: BayPayload;
  content: ImageKindPayload;
  view: DiffViewMode;
  linePins: LinePins;
  // Receives the bay's handle once mounted and null before it goes.
  onHandle: (handle: BayHandle | null) => void;
}): JSX.Element {
  let row!: HTMLDivElement;
  const review = useReview();
  const navigation = useNavigation();
  const bayKey = props.bay.bay_key;
  const binding: ReviewTextGridBinding = {
    snapshot_id: review.snapshotId,
    file: props.reviewFile,
    bay: { bay_key: bayKey },
  };
  // A side is present here exactly while its content is mounted; an absent
  // image has no pseudo-line.
  const cells: Partial<Record<Side, LineCell>> = {};

  onMount(() => {
    props.onHandle({
      mode: () => null,
      // Nothing is fetched, unfolded or virtualized to show an image bay.
      enrich: () => Promise.resolve(),
      intersectsRichEntryZone() {
        throw new Error("An image bay is never virtual.");
      },
      prepareLine(target, abortSignal) {
        assert(
          target.file.left_path === props.reviewFile.left_path &&
            target.file.right_path === props.reviewFile.right_path &&
            target.bay.bay_key === bayKey,
          "Image bay received a line target from another bay.",
        );
        if (abortSignal.aborted || !row.isConnected) {
          return Promise.resolve({ state: "stopped" });
        }
        const cell =
          target.line === String(PSEUDO_LINE) ? cells[target.side] : undefined;
        return Promise.resolve(
          cell === undefined ? { state: "missing" } : { state: "ready", cell },
        );
      },
    });
    onCleanup(() => props.onHandle(null));
  });

  function mountSide(cell: LineCell): void {
    cells[cell.side] = cell;
    // A pin already in the URL when this bay mounts is painted here, the same
    // restoration the text grid performs once it has rendered its rows.
    const parsed = props.linePins.parseUrl();
    if (
      parsed.state === "valid" &&
      parsed.target.file.left_path === props.reviewFile.left_path &&
      parsed.target.file.right_path === props.reviewFile.right_path &&
      parsed.target.bay.bay_key === bayKey &&
      parsed.target.side === cell.side &&
      parsed.target.line === String(PSEUDO_LINE)
    ) {
      paintLinePin(navigation.root(), cell, true);
    }
  }

  function activateLine(cell: LineCell, clicked: EventTarget | null): void {
    const trigger = Array.from(cell.triggerHost.children).find(
      (child) => clicked instanceof Node && child.contains(clicked),
    );
    if (trigger !== undefined) {
      assert(
        trigger instanceof HTMLButtonElement,
        "Comment trigger must be a button inside its line number.",
      );
      review.activateTextCommentInput(
        binding,
        cell.side,
        PSEUDO_LINE,
        { codeCell: cell.codeCell, trigger },
        // A bay with one pseudo-line has no range to extend, so the shift
        // modifier means nothing here.
        false,
      );
      return;
    }
    paintLinePin(
      navigation.root(),
      cell,
      props.linePins.toggleUrlState({
        file: props.reviewFile,
        bay: { bay_key: bayKey },
        side: cell.side,
        line: String(PSEUDO_LINE),
      }) === "pinned",
    );
  }

  return (
    <div class="media-bay">
      <div class="diff-grid media-grid">
        <div class="diff-lines media-lines">
          <div
            class="diff-row media-row"
            classList={{ "media-row-inline": props.view === "inline" }}
            ref={row}
          >
            <ImageSideView
              content={props.content}
              side="left"
              reviewFile={props.reviewFile}
              binding={binding}
              row={row}
              onMount={mountSide}
              onActivate={activateLine}
            />
            <ImageSideView
              content={props.content}
              side="right"
              reviewFile={props.reviewFile}
              binding={binding}
              row={row}
              onMount={mountSide}
              onActivate={activateLine}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

function ImageSideView(props: {
  content: ImageKindPayload;
  side: Side;
  reviewFile: ReviewFilePair;
  binding: ReviewTextGridBinding;
  row: HTMLElement;
  onMount: (cell: LineCell) => void;
  onActivate: (cell: LineCell, clicked: EventTarget | null) => void;
}): JSX.Element {
  const review = useReview();
  const toast = useToasts();
  // One failed decode per mounted side. The browser reports a picture it could
  // not render only through this event, and a silently broken image frame is
  // exactly the "looks empty, actually failed" state review must never show.
  const [decodeFailed, setDecodeFailed] = createSignal(false);
  let codeCell!: HTMLDivElement;
  // Mounted only while this side has content.
  let triggerHost: HTMLSpanElement | undefined;
  let cell: LineCell | null = null;

  const mediaRef = (): MediaRef | null =>
    props.side === "left" ? props.content.left : props.content.right;
  // "old" and "new" are what the inline text header calls the two sides; the
  // same two words name them here.
  const sideName = (): string => (props.side === "left" ? "old" : "new");

  onMount(() => {
    if (triggerHost === undefined) {
      return;
    }
    const mounted: LineCell = {
      side: props.side,
      line: PSEUDO_LINE,
      row: props.row,
      triggerHost,
      codeCell,
    };
    cell = mounted;
    // Indexed repaint, not replacement: a marker whose kind or count changes
    // re-decorates its existing button, which may anchor an active Comment
    // input or Thread panel.
    createEffect(() => {
      paintCommentTriggers(
        mounted.triggerHost,
        review.markerState(props.binding, props.side, PSEUDO_LINE),
        `the ${sideName()} content`,
      );
    });
    props.onMount(mounted);
  });

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
          if (cell === null) {
            return;
          }
          event.stopPropagation();
          props.onActivate(cell, event.target);
        }}
      >
        <Show when={mediaRef() !== null}>
          <span
            class="line-comment-triggers"
            ref={(element) => {
              triggerHost = element;
            }}
          />
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
