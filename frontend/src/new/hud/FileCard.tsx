/**
 * Renders one stable manifest-position file owner and its complete state branch.
 *
 * The module exports FileCard. It owns HuskFile, LazyFile, FullFile, their
 * distinct headers, FileBody dispatch, per-card rendering containment, and the
 * explicit lazy-load affordance. Callers provide a reactive derived state,
 * ChangeSet-owned expansion, and the single-lane load command. This module must
 * not observe queries, schedule HTTP work, own ChangeSet progress, or navigate
 * hunks. File representation changes remain internal to this owner.
 */
import { Show, type JSX } from "solid-js";
import { LoaderCircle } from "lucide-solid";
import type {
  DiffEngine,
  EngineWarning,
  FileDiff,
  LazyInfoFile,
} from "../api/api";
import {
  ErrorPanel,
  RetryButton,
  UnexpectedErrorBoundary,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import { DiffGrid } from "./DiffGrid";
import { NotebookFile } from "./NotebookFile";

/**
 * Describes the ordinary pre-result presentation of one manifest file.
 *
 * A Husk contains only stable manifest presentation and exact queue activity.
 * It must not pretend that per-file statistics or rendered rows are available.
 */
type HuskFileState = {
  state: "husk";
  fileIndex: number;
  name: string;
  path: string;
  activity: "queued" | "fetching";
};

/**
 * Describes a successfully loaded canonical file query result.
 *
 * The immutable FileDiff is complete and is the sole backend value accepted by
 * FileBody. Manifest or lazy metadata must not be merged into it.
 */
type FullFileState = {
  state: "full";
  fileIndex: number;
  file: FileDiff;
};

/**
 * Describes either intentional delayed-file metadata or a real file failure.
 *
 * Deferred values come only from lazy-info. Error values retain the original
 * thrown Error and stable manifest path so complete local damage remains visible.
 */
type LazyFile =
  | { kind: "deferred"; info: LazyInfoFile }
  | { kind: "error"; name: string; path: string; error: Error };

/**
 * Describes a file whose content starts only through explicit user activation.
 *
 * Retry and delayed hydration use the same ChangeSet-supplied request-lane
 * command; the state itself owns no request or copied loading flag.
 */
type LazyFileState = {
  state: "lazy";
  fileIndex: number;
  file: LazyFile;
};

/**
 * Represents every complete presentation branch accepted by FileCard.
 *
 * The discriminant is derived by ChangeSet from canonical query state. Callers
 * must never manufacture a FullFile for loading or failure placeholders.
 */
type FileCardState = HuskFileState | FullFileState | LazyFileState;

/**
 * Defines every input required by one stable FileCard owner.
 *
 * Expansion remains ChangeSet-owned so it survives active-content replacement.
 * FileCard may only request an explicit load and expansion change; it cannot
 * mutate its state or begin a query independently.
 */
type FileCardProps = {
  state: FileCardState;
  expanded: boolean;
  engine: DiffEngine;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onLoad: () => void;
};

/**
 * Contains one file renderer failure without damaging sibling FileCards.
 *
 * Callers keep this component mounted at one manifest position and replace only
 * its reactive state. Unexpected header or body failures produce one complete
 * local panel and Toast through the shared boundary.
 */
export function FileCard(props: FileCardProps): JSX.Element {
  return (
    <UnexpectedErrorBoundary title="Could not render file">
      <FileCardContent
        state={props.state}
        expanded={props.expanded}
        engine={props.engine}
        view={props.view}
        aggressiveFolds={props.aggressiveFolds}
        onExpandedChange={props.onExpandedChange}
        onLoad={props.onLoad}
      />
    </UnexpectedErrorBoundary>
  );
}

/**
 * Projects one reactive state branch into stable FileCard DOM.
 *
 * The article persists for this mounted keyed manifest entry. State replacement
 * swaps complete Husk, Full, or Lazy content without moving query ownership into
 * the card or retaining partial content from the prior branch.
 */
function FileCardContent(props: FileCardProps): JSX.Element {
  return (
    <article
      class="file-card"
      classList={{
        "is-collapsed": props.state.state === "husk" || !props.expanded,
      }}
      data-file-card
      data-file-index={props.state.fileIndex}
      data-file-state={props.state.state}
      data-file-render={props.state.state === "full" ? "rich" : undefined}
    >
      <Show when={props.state.state === "husk" ? props.state : null} keyed>
        {(state) => <HuskFile state={state} />}
      </Show>
      <Show when={props.state.state === "full" ? props.state : null} keyed>
        {(state) => (
          <FullFile
            state={state}
            expanded={props.expanded}
            engine={props.engine}
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
            onExpandedChange={props.onExpandedChange}
          />
        )}
      </Show>
      <Show when={props.state.state === "lazy" ? props.state : null} keyed>
        {(state) => (
          <LazyFileView
            state={state}
            expanded={props.expanded}
            onExpandedChange={props.onExpandedChange}
            onLoad={props.onLoad}
          />
        )}
      </Show>
    </article>
  );
}

/**
 * Renders a queued or actively fetching file without reserving body height.
 *
 * The header uses only manifest path and activity. It exposes no body or
 * expansion control while no file or lazy-info result exists, and therefore
 * never reserves the eventual rendered height.
 */
function HuskFile(props: { state: HuskFileState }): JSX.Element {
  return <HuskFileHeader state={props.state} />;
}

/**
 * Renders the complete non-interactive header for a queued or fetching file.
 *
 * Callers provide stable manifest presentation and exact lane activity. The
 * header exposes neither file statistics nor expansion before content exists.
 */
function HuskFileHeader(props: { state: HuskFileState }): JSX.Element {
  return (
    <header class="file-card-header husk-file-header">
      <span class="file-card-heading">
        <span class="file-card-title-row">
          <h2>{props.state.path}</h2>
          <span class="file-card-status">
            {props.state.activity === "fetching" ? "loading" : "queued"}
          </span>
        </span>
      </span>
      <LoaderCircle
        class="file-state-spinner"
        classList={{ "is-spinning": props.state.activity === "fetching" }}
        aria-hidden="true"
      />
    </header>
  );
}

/**
 * Renders a complete file header and rich body from one immutable query result.
 *
 * View and aggressive-fold changes are read reactively by the renderer. They do
 * not replace query data or transfer global progress and navigation ownership
 * into FileBody.
 */
function FullFile(props: {
  state: FullFileState;
  expanded: boolean;
  engine: DiffEngine;
  view: DiffViewMode;
  aggressiveFolds: boolean;
  onExpandedChange: (expanded: boolean) => void;
}): JSX.Element {
  return (
    <>
      <FullFileHeader
        state={props.state}
        expanded={props.expanded}
        onExpandedChange={props.onExpandedChange}
      />
      <Show when={props.expanded}>
        <div class="file-card-body" data-file-body>
          <FileBody
            fileIndex={props.state.fileIndex}
            file={props.state.file}
            engine={props.engine}
            view={props.view}
            aggressiveFolds={props.aggressiveFolds}
          />
        </div>
      </Show>
    </>
  );
}

/**
 * Renders the complete interactive header for one successfully loaded file.
 *
 * Callers provide the canonical FullFile state and ChangeSet-owned expansion.
 * The header shows only file-local statistics and warnings and reports expansion
 * changes without owning them.
 */
function FullFileHeader(props: {
  state: FullFileState;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      class="file-card-header full-file-header"
      aria-expanded={props.expanded}
      onClick={() => props.onExpandedChange(!props.expanded)}
    >
      <span class="file-card-heading">
        <VisibilityIndicator visible={props.expanded} />
        <span class="file-card-title-row">
          <h2>{props.state.file.display_name}</h2>
          <span class="file-card-status">
            {props.state.file.file_kind.type === "git"
              ? props.state.file.file_kind.status
              : "untracked"}
          </span>
          <Show
            when={
              "engine_warning" in props.state.file
                ? props.state.file.engine_warning
                : null
            }
            keyed
          >
            {(warning) => (
              <span class="file-card-engine-warning" title={warning.message}>
                {engineWarningLabel(warning)}
              </span>
            )}
          </Show>
        </span>
      </span>
      <FileStatistics summary={props.state.file.summary} />
    </button>
  );
}

/**
 * Maps one validated renderer warning to the established visible label.
 *
 * The complete backend message remains the tooltip. Every stable warning type
 * has explicit copy, and an added backend variant fails TypeScript exhaustivity.
 */
function engineWarningLabel(warning: EngineWarning): string {
  switch (warning.type) {
    case "difftastic_graph_limit":
      return "Difftastic failed: unified fallback";
    case "difftastic_empty_rows":
      return "Difftastic claims no changes";
    case "gumtree_invalid_json":
      return "GumTree failed: unified fallback";
  }
}

/**
 * Renders intentionally delayed content or complete localized query damage.
 *
 * Deferred planks submit exactly one explicit load command. Error planks retain
 * the complete message, open stack, and RetryButton; neither branch invokes the
 * command without direct user activation.
 */
function LazyFileView(props: {
  state: LazyFileState;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onLoad: () => void;
}): JSX.Element {
  return (
    <>
      <LazyFileHeader
        state={props.state}
        expanded={props.expanded}
        onExpandedChange={props.onExpandedChange}
      />
      <Show when={props.expanded}>
        <Show
          when={props.state.file.kind === "deferred" ? props.state.file : null}
          keyed
        >
          {(deferred) => (
            <DeferredFilePlank info={deferred.info} onLoad={props.onLoad} />
          )}
        </Show>
        <Show
          when={props.state.file.kind === "error" ? props.state.file : null}
          keyed
        >
          {(failure) => (
            <div class="file-lazy-error-panel is-error">
              <ErrorPanel
                title={`Failed to load ${failure.path}`}
                error={failure.error}
              >
                <RetryButton onRetry={props.onLoad} />
              </ErrorPanel>
            </div>
          )}
        </Show>
      </Show>
    </>
  );
}

/**
 * Renders the complete interactive header for a delayed or failed file.
 *
 * Deferred values show only available lazy metadata; failures show their local
 * status without hiding the full body error. Expansion remains ChangeSet-owned
 * and determines whether the explicit action or error body participates.
 */
function LazyFileHeader(props: {
  state: LazyFileState;
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
}): JSX.Element {
  return (
    <button
      type="button"
      class="file-card-header lazy-file-header"
      aria-expanded={props.expanded}
      onClick={() => props.onExpandedChange(!props.expanded)}
    >
      <span class="file-card-heading">
        <VisibilityIndicator visible={props.expanded} />
        <span class="file-card-title-row">
          <h2>
            {props.state.file.kind === "deferred"
              ? props.state.file.info.display_name
              : props.state.file.path}
          </h2>
          <span class="file-card-status">
            {props.state.file.kind === "error"
              ? "failed"
              : props.state.file.info.lazy}
          </span>
        </span>
      </span>
      <Show
        when={props.state.file.kind === "deferred" ? props.state.file : null}
        keyed
      >
        {(deferred) => <LazyStatistics info={deferred.info} />}
      </Show>
    </button>
  );
}

/**
 * Renders the colored explicit-fetch plank for one backend delay reason.
 *
 * The complete LazyInfoFile is required. Activation reports only the supplied
 * command, leaving request ordering and fetching presentation with ChangeSet.
 */
function DeferredFilePlank(props: {
  info: LazyInfoFile;
  onLoad: () => void;
}): JSX.Element {
  /**
   * Returns the complete action label for the required backend delay reason.
   *
   * Null is forbidden for a LazyFile plank and throws as a response-contract
   * violation rather than inventing a generic load operation.
   */
  const title = () => {
    switch (props.info.lazy) {
      case "deleted":
        return "Load deleted file diff";
      case "generated":
        return "Load generated diff";
      case "too_big":
        return "Load large diff";
      case "untracked":
        return "Load untracked file";
      case "pure_renamed":
        return "Load renamed file diff";
      case null:
        throw new Error("LazyFile metadata requires a lazy reason.");
    }
  };
  /**
   * Returns the complete visible explanation for the backend delay reason.
   *
   * The backend display name is preserved verbatim. Null is an invalid
   * LazyFile reason and throws instead of producing fallback copy.
   */
  const explanation = () => {
    switch (props.info.lazy) {
      case "deleted":
        return `${props.info.display_name} is deleted. Click to fetch and open it.`;
      case "generated":
        return `${props.info.display_name} looks generated. Click to fetch and open it.`;
      case "too_big":
        return `${props.info.display_name} is large. Click to fetch and open it.`;
      case "untracked":
        return `${props.info.display_name} is untracked. Click to fetch and open it.`;
      case "pure_renamed":
        return `${props.info.display_name} was renamed without content changes. Click to fetch and open it.`;
      case null:
        throw new Error("LazyFile metadata requires a lazy reason.");
    }
  };

  return (
    <button
      type="button"
      class="file-lazy-load-toggle"
      classList={{
        "is-untracked": props.info.lazy === "untracked",
        "is-generated": props.info.lazy === "generated",
        "is-deleted": props.info.lazy === "deleted",
        "is-too-big": props.info.lazy === "too_big",
        "is-pure-renamed": props.info.lazy === "pure_renamed",
      }}
      onClick={props.onLoad}
    >
      <span class="file-lazy-load-toggle-title">{title()}</span>
      <span class="file-lazy-load-toggle-meta">{explanation()}</span>
    </button>
  );
}

/**
 * Dispatches one complete FileDiff to its established rich renderer.
 *
 * The notebook discriminator is the only variant test. Text rows retain the
 * exact backend labels, hints, and Difftastic collapse policy; this boundary does
 * not subscribe to progress, headers, other files, or navigation state.
 */
function FileBody(props: {
  fileIndex: number;
  file: FileDiff;
  engine: DiffEngine;
  view: DiffViewMode;
  aggressiveFolds: boolean;
}): JSX.Element {
  if ("render_kind" in props.file) {
    return (
      <NotebookFile
        fileIndex={props.fileIndex}
        file={props.file}
        view={props.view}
        aggressiveFolds={props.aggressiveFolds}
      />
    );
  }
  return (
    <DiffGrid
      fileIndex={props.fileIndex}
      displayName={props.file.display_name}
      leftLabel={props.file.left_label}
      rightLabel={props.file.right_label}
      rows={props.file.rows}
      foldHints={props.file.fold_hints}
      viewMode={props.view}
      aggressiveFolds={props.aggressiveFolds}
      collapseInsertOnlyReplaceRows={props.engine === "difftastic"}
    />
  );
}

/**
 * Renders exact per-file line statistics supplied by a FullFile response.
 *
 * The summary is complete. These values remain file-local and never contribute
 * to manifest totals or sequence progress.
 */
function FileStatistics(props: { summary: FileDiff["summary"] }): JSX.Element {
  return (
    <span class="file-stats">
      <span class="delta added">+ {props.summary.added_lines}</span>
      <span class="delta changed">~ {props.summary.modified_lines}</span>
      <span class="delta removed">- {props.summary.removed_lines}</span>
      <span class="delta moved">* {props.summary.moved_lines}</span>
    </span>
  );
}

/**
 * Renders the stable four-cell statistic shape for one LazyFile header.
 *
 * Added and removed values come from lazy-info. Modified is known to be zero
 * only when both line totals are known; every unavailable metric uses the
 * established question mark and never becomes an aggregate authority.
 */
function LazyStatistics(props: { info: LazyInfoFile }): JSX.Element {
  return (
    <span class="file-stats">
      <span class="delta added">
        + {props.info.added_lines === null ? "?" : props.info.added_lines}
      </span>
      <span class="delta changed">
        ~{" "}
        {props.info.added_lines !== null && props.info.removed_lines !== null
          ? 0
          : "?"}
      </span>
      <span class="delta removed">
        - {props.info.removed_lines === null ? "?" : props.info.removed_lines}
      </span>
      <span class="delta moved">* ?</span>
    </span>
  );
}

/**
 * Renders the established card expansion indicator for one file header.
 *
 * The indicator reflects only ChangeSet-owned expansion. File representation
 * changes must preserve that selected expansion and cannot alter its meaning.
 */
function VisibilityIndicator(props: { visible: boolean }): JSX.Element {
  return (
    <span
      class="visibility-indicator large"
      classList={{ visible: props.visible }}
      aria-hidden="true"
    />
  );
}
