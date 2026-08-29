/**
 * Keeps the five review Tabs mounted with their independent selection workflows.
 *
 * Each Tab retains local input and its last complete `DiffParams` while inactive.
 * Repository-backed controls observe shared refs or defaults and publish browser
 * URL state only after an explicit selection or later reactivation. A completed
 * selection mounts its stable ChangeSet boundary.
 *
 * Global repository, engine, view, and visibility choices stay controlled by the
 * workspace. Backend responses remain canonical TanStack Query data.
 */
import {
  type Accessor,
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  on,
  onMount,
  type JSX,
} from "solid-js";
import { Portal } from "solid-js/web";
import {
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { RefreshCw, Save, Trash2 } from "lucide-solid";
import {
  api,
  type BranchReviewDiffParams,
  type BranchSelection,
  type BuiltinRef,
  type DiffEngine,
  type HeadDiffParams,
  type PreparedPullRequest,
  type PresetDiffParams,
  type PresetType,
  type ProjectId,
  type PullRequestDiffParams,
  type RefChoices,
  type RefsDiffParams,
  type RepoMark,
} from "../api/api";
import { AutocompleteInput } from "../comp/AutocompleteInput";
import {
  ErrorPopover,
  UnexpectedErrorBoundary,
  useToasts,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets, RepositoryState } from "./AppHeader";
import { ChangeSet } from "./changeSet/ChangeSet";
import type { StoredProfile } from "./Profile";
import { assert, expect } from "../utils";

/**
 * Identifies one user-visible application Tab.
 *
 * The value selects a mounted control workflow and the same `tab` field carried by
 * that workflow's complete manifest parameters.
 */
export type TabId =
  | "head"
  | "refs"
  | "branch-review"
  | "pull-request"
  | "preset";

/**
 * Fixes the selector's user-visible Tab order independently of mounted content order.
 *
 * The readonly list is checked to contain only valid identities and is used only
 * to render controls; it carries no activation or lifetime state.
 */
const tabIds: readonly TabId[] = [
  "head",
  "refs",
  "branch-review",
  "pull-request",
  "preset",
];

/**
 * Provides the selector label required for every Tab identity.
 *
 * The exhaustive record keeps presentation out of URL and DiffParams values.
 */
const tabLabels: Record<TabId, string> = {
  head: "Diff against HEAD",
  refs: "Compare refs",
  "branch-review": "Branch review",
  "pull-request": "PR",
  preset: "Preset",
};

/**
 * Explains each backend-recognized built-in ref in autocomplete results.
 *
 * Exhaustiveness makes a newly supported built-in require deliberate HUD copy;
 * arbitrary branches and remotes do not enter this record.
 */
const builtinDescriptions: Record<BuiltinRef, string> = {
  HEAD: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
};

/**
 * Calls one explicit reactivation operation when an eternal Tab becomes active again.
 *
 * The effect explicitly observes only `active`, ignores the initial mount,
 * and invokes `reactivate` solely on a false-to-true transition. Solid disposes
 * the effect with the Tab; no external subscription or cleanup exists. Because
 * `on` untracks the callback, Tab selection reads and writes cannot accidentally
 * become dependencies or trigger duplicate activations.
 *
 * @param active Reactive accessor for this eternal Tab's current visibility.
 * @param reactivate Zero-argument synchronous operation that may establish local
 * selection and publish its accepted URL state after a later activation. It is
 * never called on initial mount or while the Tab remains continuously active; its
 * return value is ignored, and the effect performs no further work after it returns.
 */
function onTabReactivated(
  active: Accessor<boolean>,
  reactivate: () => void,
): void {
  // Track only active visibility for this helper's lifetime. Solid disposes the
  // effect with its Tab; it installs no external resource and needs no cleanup.
  createEffect(
    on(active, (isActive, wasActive) => {
      if (isActive && wasActive === false) {
        reactivate();
      }
    }),
  );
}

/**
 * Defines the public rendering inputs for the Tab selector.
 *
 * App supplies one valid active Tab and the explicit selection command. TabStrip
 * stores no Tab lifetime or URL behavior.
 */
type TabStripProps = {
  /**
   * App-controlled Tab currently marked and presented as active.
   *
   * TabStrip never changes this value locally; caller updates are reflected
   * through the matching button's pressed state.
   */
  active: TabId;
  /**
   * Reports activation of the exact button's Tab, including the current Tab.
   *
   * The callback may update workspace state and browser navigation. TabStrip does
   * not suppress same-value activation or retain the result, so the caller must
   * pass its accepted value back through `active`; focus remains on the button
   * after the synchronous callback.
   */
  onSelect: (tab: TabId) => void;
};

/**
 * Defines every workspace value and explicit command consumed by eternal Tabs.
 *
 * Repository absence is genuine and gates only repo-backed workflows. Browser
 * URL updates remain explicit per workflow; Tabs receive no generic App setter.
 */
type TabsProps = {
  /**
   * App-controlled Tab whose panel and metadata activity are visible.
   *
   * All five children stay mounted when this value changes so their completed
   * selections and control input survive navigation.
   */
  active: TabId;
  /**
   * Global repository identity used by repo-backed workflows, or genuine absence.
   *
   * Null mounts the local RepoGate paths. Preset and Pull Request input remain
   * usable without a repository.
   */
  repoId: ProjectId | null;
  /**
   * Current backend file renderer forwarded to every mounted ChangeSet.
   *
   * Changing it affects file requests without replacing a Tab's selected
   * DiffParams or repository identity.
   */
  engine: DiffEngine;
  /**
   * Current shared split or inline text presentation for ChangeSets.
   *
   * It changes client rendering only and never enters manifest query identity.
   */
  view: DiffViewMode;
  /**
   * Workspace-controlled visibility of the active ChangeSet FileTree.
   *
   * Tabs forward the value without retaining another visibility state.
   */
  fileTreeOpen: boolean;
  /**
   * Workspace-controlled visibility of ChangeSet diagnostics.
   *
   * The value remains separate from selected Tab parameters and backend data.
   */
  debugHudOpen: boolean;
  /**
   * Confirmed Profile used for review authorship, or genuine absence.
   *
   * Tabs forward identity to ChangeSet and never load or alter Profile preferences.
   */
  selectedProfile: StoredProfile | null;
  /**
   * Stable mounted header destinations used by active ChangeSet Portals.
   *
   * Accessors may throw before AppHeader registration; Tabs neither call nor
   * replace them until a ChangeSet consumes them.
   */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Shared AppHeader target for compact Tab metadata status.
   *
   * Null suppresses Portal output but does not create placeholder DOM or move
   * query state out of the Tab.
   */
  metadataTarget: HTMLElement | null;
  /**
   * Accepts one validated repository chosen from a RepoGate list.
   *
   * `projectId` is the exact backend mark identity. It does not run while a repo
   * is already available or when removal is requested. The caller may reset the
   * workspace and must return the accepted identity through `repoId`; that reset
   * may dispose all current Tab state after the callback completes.
   */
  onRepoSelected: (projectId: ProjectId) => void;
  /**
   * Serializes the fixed Head selection after explicit Load or later reactivation.
   *
   * It receives no value because HeadTab has one complete parameter set. It does
   * not run without a repository or merely because the initial active selection
   * mounts. The caller may replace browser URL state; HeadTab already retains the
   * matching selection before invoking it and needs no controlled value returned.
   */
  onHeadSelected: () => void;
  /**
   * Serializes one complete old/new ref pair after Load or retained reactivation.
   *
   * Arguments are the exact accepted free-form left and right texts. It does not
   * run for edits, incomplete form submission, or repo-gated controls. The caller
   * may replace browser URL state; RefsTab retains the matching DiffParams before
   * invocation and therefore requires no value to be passed back.
   *
   * @param left Exact accepted old-side Git ref.
   * @param right Exact accepted new-side Git ref.
   */
  onRefsSelected: (left: string, right: string) => void;
  /**
   * Serializes a complete structured base/review pair after Load or reactivation.
   *
   * `base` and `review` are the exact accepted local-or-remote selections, with
   * no defaults left to infer. It does not run for field edits, incomplete input,
   * or repo-gated controls. The caller may replace URL state; Branch Review stores
   * the matching DiffParams before invocation and needs no controlled handback.
   *
   * @param base Complete accepted base-side structured branch.
   * @param review Complete accepted review-side structured branch.
   */
  onBranchReviewSelected: (
    base: BranchSelection,
    review: BranchSelection,
  ) => void;
  /**
   * Serializes one validated catalog and subset after selection or reactivation.
   *
   * `presetType` is the active backend catalog ID and `preset` its accepted group
   * ID. The callback does not run while catalogs/defaults are unavailable or a
   * waiting command is obsolete. The caller may replace URL state; Preset retains
   * the matching DiffParams before invocation and needs no value returned.
   *
   * @param presetType Validated backend catalog identity.
   * @param preset Exact selected group identity within that catalog.
   */
  onPresetSelected: (presetType: PresetType, preset: string) => void;
  /**
   * Republishes a complete already-prepared Pull Request selection on reactivation.
   *
   * `selection` contains the retained repository, URL, and two commits. This does
   * not run for input edits, preparation failure, or initial mount. The caller may
   * replace URL state but must not reinterpret the values; the Tab retains its
   * selection and requires no controlled handback.
   */
  onPullRequestSelected: (selection: PullRequestDiffParams) => void;
  /**
   * Applies the authoritative backend result after Pull Request preparation succeeds.
   *
   * `prepared` contains the replacement repository, authoritative URL, and commit
   * pair. It never runs for invalid input or mutation failure. The caller may
   * invalidate repository data and reconstruct the workspace; this Tab keeps no
   * prepared copy, so accepted state returns only through that reconstruction,
   * which may dispose the invoking component after the callback completes.
   */
  onPullRequestPrepared: (prepared: PreparedPullRequest) => void;
  /**
   * Requests the opposite shared text presentation from a ChangeSet control.
   *
   * It runs only on explicit view-toggle interaction and receives no proposed
   * value. The caller may update URL and workspace state and must pass the accepted
   * mode back through `view`; Tabs perform no follow-up after it returns.
   */
  onToggleView: () => void;
  /**
   * Accepts the exact FileTree visibility requested inside a mounted ChangeSet.
   *
   * It does not run from Tab activation or file selection. The caller stores the
   * boolean and must pass it back through `fileTreeOpen`; the ChangeSet continues
   * using that controlled value after the synchronous callback.
   */
  onFileTreeOpenChange: (open: boolean) => void;
  /**
   * Accepts the exact diagnostic HUD visibility requested by ChangeSet controls.
   *
   * It does not run from Tab activation or engine changes. The caller stores the
   * boolean and must pass it back through `debugHudOpen`; no other action follows
   * in Tabs after the synchronous callback.
   */
  onDebugHudOpenChange: (open: boolean) => void;
};

/**
 * Defines shared inputs for one mounted private Tab.
 *
 * Active controls and expensive ChangeSet content depend on `active`; the outer
 * Tab remains mounted. Engine changes replace file rendering without changing a
 * selected workflow value or manifest identity.
 */
type TabProps = {
  /**
   * Whether this eternal Tab currently presents controls and active ChangeSet work.
   *
   * False keeps local selections mounted while disabling Tab-specific observers.
   */
  active: boolean;
  /**
   * Current global repository identity, or absence that mounts this Tab's gate.
   *
   * A repository change occurs through workspace reconstruction rather than local
   * synchronization in a mounted private Tab.
   */
  repoId: ProjectId | null;
  /**
   * Reactive backend file renderer forwarded unchanged to selected ChangeSets.
   *
   * It is not stored in any private Tab selection.
   */
  engine: DiffEngine;
  /**
   * Reactive split or inline presentation forwarded to selected ChangeSets.
   *
   * It affects no metadata query or DiffParams value.
   */
  view: DiffViewMode;
  /**
   * Controlled FileTree visibility shared across all selected ChangeSets.
   *
   * The private Tab forwards it without deriving per-Tab state.
   */
  fileTreeOpen: boolean;
  /**
   * Controlled diagnostic HUD visibility shared across selected ChangeSets.
   *
   * The private Tab forwards it without placing it in manifest identity.
   */
  debugHudOpen: boolean;
  /**
   * Confirmed review Profile or genuine absence, forwarded to ChangeSet.
   *
   * A private Tab neither mutates nor persists this identity.
   */
  selectedProfile: StoredProfile | null;
  /**
   * Stable header Portal accessors consumed by selected ChangeSets.
   *
   * Private Tabs preserve their identity and do not create alternate targets.
   */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Current shared target for this Tab's compact metadata presentation.
   *
   * Null suppresses Portal rendering without disabling query observation.
   */
  metadataTarget: HTMLElement | null;
  /**
   * Accepts a repository chosen from this Tab's RepoGate.
   *
   * The exact mark ID is supplied only by explicit choice. The caller may reset
   * the workspace and must return it through `repoId`; the invoking gated branch
   * can be disposed after the callback, and no later local action is required.
   */
  onRepoSelected: (projectId: ProjectId) => void;
  /**
   * Requests the opposite workspace view from a selected ChangeSet.
   *
   * The caller stores the result and passes it back through `view`; inactive or
   * unselected Tabs never invoke it, and the private Tab takes no later action.
   */
  onToggleView: () => void;
  /**
   * Accepts exact FileTree visibility requested by the selected ChangeSet.
   *
   * The caller must return accepted state through `fileTreeOpen`. Tab activation
   * alone does not invoke it, and no asynchronous work follows the callback.
   */
  onFileTreeOpenChange: (open: boolean) => void;
  /**
   * Accepts exact diagnostic HUD visibility requested by the selected ChangeSet.
   *
   * The caller must return accepted state through `debugHudOpen`. It is not called
   * by selection or navigation, and the private Tab retains no visibility copy.
   */
  onDebugHudOpenChange: (open: boolean) => void;
};

/**
 * Defines shared required inputs after a RepoGate has narrowed repository state.
 *
 * The project ID is concrete, so canonical query definitions never receive a
 * placeholder identity. App still supplies engine and activation.
 */
type RepoTabProps = {
  /**
   * Whether this repository-bound Tab currently displays and observes metadata.
   *
   * False retains local input and selected DiffParams for later reactivation.
   */
  active: boolean;
  /**
   * Concrete repository identity fixed for this keyed child mount.
   *
   * Every refs, defaults, and manifest definition built here uses this real ID;
   * workspace reset replaces the child rather than changing it in place.
   */
  projectId: ProjectId;
  /**
   * Reactive backend file renderer forwarded to the selected ChangeSet.
   *
   * It remains outside the stored selection and manifest identity.
   */
  engine: DiffEngine;
  /**
   * Reactive text presentation forwarded to the selected ChangeSet.
   *
   * The value creates no repository query and changes no DiffParams.
   */
  view: DiffViewMode;
  /**
   * Workspace-controlled FileTree visibility forwarded to ChangeSet.
   *
   * The repository child never stores a competing value.
   */
  fileTreeOpen: boolean;
  /**
   * Workspace-controlled diagnostic HUD visibility forwarded to ChangeSet.
   *
   * It is presentation state, not part of the repository selection.
   */
  debugHudOpen: boolean;
  /**
   * Confirmed review Profile or genuine absence for ChangeSet authorship.
   *
   * Repository controls do not observe or alter Profile data.
   */
  selectedProfile: StoredProfile | null;
  /**
   * Stable AppHeader targets used when the selected ChangeSet portals status.
   *
   * This child forwards the accessors and never creates replacement elements.
   */
  appHeaderOutlets: AppHeaderOutlets;
  /**
   * Shared physical target for refs/defaults status, or null before registration.
   *
   * Absence hides the Portal only; query and control lifetimes remain unchanged.
   */
  metadataTarget: HTMLElement | null;
  /**
   * Requests the opposite workspace text view from the selected ChangeSet.
   *
   * The caller must publish accepted state back through `view`. It is not invoked
   * by metadata changes, and no repository-child action follows it.
   */
  onToggleView: () => void;
  /**
   * Accepts exact FileTree visibility requested by the selected ChangeSet.
   *
   * The caller returns the accepted boolean through `fileTreeOpen`; the child
   * keeps no duplicate and does not invoke it while no ChangeSet is mounted.
   */
  onFileTreeOpenChange: (open: boolean) => void;
  /**
   * Accepts exact diagnostic HUD visibility requested by the selected ChangeSet.
   *
   * The caller returns the accepted boolean through `debugHudOpen`; metadata and
   * Tab activation do not invoke this callback.
   */
  onDebugHudOpenChange: (open: boolean) => void;
};

/**
 * Defines the complete presentation inputs of a metadata refresh control.
 *
 * The caller supplies current fetching and failure data together with the exact
 * refresh operation. The control stores no query state and chooses no refetch policy.
 */
type MetadataRefreshProps = {
  /**
   * User-visible operation name used for title and accessible labelling.
   *
   * It must describe the caller's metadata rather than a generic icon action.
   */
  label: string;
  /**
   * Whether the shared observer is currently fetching any attempt.
   *
   * True disables and animates the control and temporarily takes precedence over
   * stale failure presentation.
   */
  fetching: boolean;
  /**
   * Current observer failure shown in the complete error popover, or null.
   *
   * The control does not clear, wrap, or retain this error after caller state changes.
   */
  error: Error | null;
  /**
   * Starts the caller's explicit metadata refetch from button activation or retry.
   *
   * It receives no query data and may choose TanStack cancellation policy. It is
   * not called while the ordinary button is disabled for `fetching`; after it
   * returns, the caller must publish progress or failure through these props.
   */
  onRefetch: () => void;
};

/**
 * Defines one active metadata observer's compact AppHeader presentation.
 *
 * The Tab supplies genuine pending/error state and retry behavior. This value
 * carries presentation only and never moves query data.
 */
type MetadataStatusPortalProps = {
  /**
   * Physical AppHeader destination for the compact status, or genuine absence.
   *
   * Null prevents rendering without moving query state into the Portal component.
   */
  target: HTMLElement | null;
  /**
   * Whether this observer is allowed to contribute status to the shared header.
   *
   * False suppresses both loading and failure even when the underlying state exists.
   */
  active: boolean;
  /**
   * Stable presentation category used only to distinguish the status element.
   *
   * It carries no query key or backend identity.
   */
  kind: "defaults" | "refs" | "pull-request" | "presets" | "repo-removal";
  /**
   * Whether pending work should currently replace failure presentation.
   *
   * The caller decides which observer flags qualify as loading.
   */
  loading: boolean;
  /**
   * Current operation failure eligible for retry, or null when none is present.
   *
   * It is shown only while active and not loading and is never retained locally.
   */
  error: Error | null;
  /**
   * Exact compact text shown while `loading` is true.
   *
   * The Portal does not derive operation names from `kind`.
   */
  loadingText: string;
  /**
   * User-visible failure heading and basis for the error trigger label.
   *
   * It must identify the caller's failed operation, not merely the Portal itself.
   */
  errorTitle: string;
  /**
   * Retries the exact failed operation when the error popover requests it.
   *
   * It does not run while loading, inactive, targetless, or error-free because no
   * retry control is rendered. The caller may start work and must return resulting
   * state through `loading` and `error`; the Portal performs no follow-up action.
   */
  onRetry: () => void;
};

/**
 * Defines RepoGate's required workspace selection inputs.
 *
 * Gate receives no repository data because it observes the shared canonical
 * query itself. Selection returns only a validated numeric project ID.
 */
type RepoGateProps = {
  /**
   * Whether the gated Tab may observe and display repository metadata.
   *
   * False keeps the gate mounted but disables the canonical list query.
   */
  active: boolean;
  /**
   * AppHeader destination for repository-removal progress, or null before mount.
   *
   * Absence suppresses only Portal presentation, not the removal mutation itself.
   */
  metadataTarget: HTMLElement | null;
  /**
   * Accepts the exact positive project ID of an explicitly chosen backend mark.
   *
   * It does not run for list loading, failure, or repository removal. The caller
   * may reconstruct the workspace and must return the accepted identity through
   * its repo-controlled state; RepoGate performs no action after invocation and
   * may be disposed by that reconstruction.
   */
  onSelect: (projectId: ProjectId) => void;
};

/**
 * Renders the persistent top-level Tab buttons.
 *
 * Activation reports exactly one TabId. Buttons retain the established Tab
 * selector presentation and do not mount or unmount Tab components.
 */
export function TabStrip(props: TabStripProps): JSX.Element {
  return (
    <fieldset class="tab-choices">
      <legend>View</legend>
      <For each={tabIds}>
        {(tab) => (
          <button
            type="button"
            classList={{ "is-active": props.active === tab }}
            aria-pressed={props.active === tab}
            onClick={() => props.onSelect(tab)}
          >
            {tabLabels[tab]}
          </button>
        )}
      </For>
    </fieldset>
  );
}

/**
 * Mounts all five Tab contents for one workspace lifetime.
 *
 * Only the active control panel is displayed and observes active metadata, while
 * every Tab retains local interaction and selected values. Complete selections
 * mount stable ChangeSet boundaries; no backend data is copied between Tabs.
 */
export function Tabs(props: TabsProps): JSX.Element {
  return (
    <>
      <div class="tab-content" hidden={props.active !== "head"}>
        <UnexpectedErrorBoundary title="Head Tab failed" retryOnR={false}>
          <HeadTab
            active={props.active === "head"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onHeadSelected}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "refs"}>
        <UnexpectedErrorBoundary title="Refs Tab failed" retryOnR={false}>
          <RefsTab
            active={props.active === "refs"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onRefsSelected}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "branch-review"}>
        <UnexpectedErrorBoundary
          title="Branch Review Tab failed"
          retryOnR={false}
        >
          <BranchReviewTab
            active={props.active === "branch-review"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onBranchReviewSelected}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "pull-request"}>
        <UnexpectedErrorBoundary
          title="Pull Request Tab failed"
          retryOnR={false}
        >
          <PullRequestTab
            active={props.active === "pull-request"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPullRequestSelected}
            onPrepared={props.onPullRequestPrepared}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "preset"}>
        <UnexpectedErrorBoundary title="Preset Tab failed" retryOnR={false}>
          <PresetTab
            active={props.active === "preset"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPresetSelected}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        </UnexpectedErrorBoundary>
      </div>
    </>
  );
}

/**
 * Parses one complete base/review pair from canonical browser fields.
 *
 * Both local and remote variants must be complete and noncontradictory. Total
 * absence returns null; partial fields, blank values, unknown sources, and remote
 * fields attached to a local selection throw so the active caller can report the
 * malformed URL before continuing without a URL-backed pair.
 *
 * # Returns
 *
 * - An object pairing the validated base and review selections. Neither side
 *   can be returned without the other.
 * - `null`: All six branch fields are absent. The caller leaves the Branch Tab
 *   unselected rather than constructing a partial pair.
 */
function branchPairFromUrl(): {
  /**
   * Complete local or remote base side reconstructed from the `base_*` fields.
   *
   * It is returned only when the review side is also complete and valid.
   */
  base: BranchSelection;
  /**
   * Complete local or remote review side reconstructed from the `review_*` fields.
   *
   * It is returned only as part of the same validated pair as `base`.
   */
  review: BranchSelection;
} | null {
  const search = new URLSearchParams(window.location.search);
  const baseSource = search.get("base_source");
  const baseBranch = search.get("base_branch");
  const baseRemote = search.get("base_remote");
  const reviewSource = search.get("review_source");
  const reviewBranch = search.get("review_branch");
  const reviewRemote = search.get("review_remote");
  if (
    baseSource === null &&
    baseBranch === null &&
    baseRemote === null &&
    reviewSource === null &&
    reviewBranch === null &&
    reviewRemote === null
  ) {
    return null;
  }

  assert(
    baseSource === "local" || baseSource === "remote",
    "base_source must be local or remote.",
  );
  assert(
    baseBranch !== null && baseBranch.trim().length > 0,
    "base_branch must be nonblank.",
  );
  assert(
    baseSource === "local"
      ? baseRemote === null
      : baseRemote !== null && baseRemote.trim().length > 0,
    "base_remote does not match base_source.",
  );
  assert(
    reviewSource === "local" || reviewSource === "remote",
    "review_source must be local or remote.",
  );
  assert(
    reviewBranch !== null && reviewBranch.trim().length > 0,
    "review_branch must be nonblank.",
  );
  assert(
    reviewSource === "local"
      ? reviewRemote === null
      : reviewRemote !== null && reviewRemote.trim().length > 0,
    "review_remote does not match review_source.",
  );

  return {
    base:
      baseSource === "local"
        ? { source: "local", branch: baseBranch }
        : {
            source: "remote",
            remote: expect(baseRemote),
            branch: baseBranch,
          },
    review:
      reviewSource === "local"
        ? { source: "local", branch: reviewBranch }
        : {
            source: "remote",
            remote: expect(reviewRemote),
            branch: reviewBranch,
          },
  };
}

/**
 * Returns one genuinely complete branch selection or explicit absence.
 *
 * Controls may render empty local/remote values while metadata is pending or the
 * user is editing. Only nonblank branches, and nonblank remotes for remote
 * selections, may become selected DiffParams or mutation inputs.
 *
 * # Returns
 *
 * - The original complete selection object, preserving its local or remote arm.
 * - `null`: The input is absent or incomplete. Callers withhold loading and
 *   serialization until the controls produce a complete selection.
 */
function selectedBranch(
  selection: BranchSelection | null,
): BranchSelection | null {
  if (selection === null || selection.branch.trim().length === 0) {
    return null;
  }
  if (selection.source === "remote" && selection.remote.trim().length === 0) {
    return null;
  }
  return selection;
}

/**
 * Renders the repo gate or the required-repository Head controls.
 *
 * The explicit null gate keeps every Head selection path, including Tab
 * reactivation, unmounted until a repository exists, so no incomplete Head value
 * can be constructed. The selected Head value is fixed, while engine remains
 * reactive and does not replace the mounted ChangeSet. Workspace reset replaces
 * repository identity.
 */
function HeadTab(
  props: TabProps & {
    /**
     * Serializes Head's fixed parameters after explicit Load or later reactivation.
     *
     * It is never called without a repository or from initial selection creation.
     * The caller may replace browser URL state; Head has already retained the
     * matching value before invocation and requires no controlled handback. The
     * submit/reactivation path performs no further action after it returns.
     */
    onSelected: () => void;
  },
): JSX.Element {
  return (
    <Show
      when={props.repoId}
      keyed
      fallback={
        // Without a repository the panel starts no query and constructs no
        // DiffParams. Every control RepoGate renders is a plain button, so this
        // panel has no submittable content.
        <form class="tab-panel" hidden={!props.active}>
          <RepoGate
            active={props.active}
            metadataTarget={props.metadataTarget}
            onSelect={props.onRepoSelected}
          />
        </form>
      }
    >
      {(projectId) => {
        // Solid owns this branch: the selection signal and the reactivation
        // effect are created when a repository appears and disposed when the
        // gate replaces the branch or the Tab unmounts. The repository is
        // concrete for that whole lifetime, so every Head value built here is
        // complete.
        const [selected, setSelected] = createSignal<HeadDiffParams | null>(
          props.active
            ? {
                project_id: projectId,
                tab: "head",
                left: "HEAD",
                right: "worktree",
                show_untracked: true,
              }
            : null,
        );
        onTabReactivated(
          () => props.active,
          () => {
            if (selected() === null) {
              setSelected({
                project_id: projectId,
                tab: "head",
                left: "HEAD",
                right: "worktree",
                show_untracked: true,
              });
            }
            props.onSelected();
          },
        );
        return (
          <>
            <form
              class="tab-panel"
              hidden={!props.active}
              onSubmit={(event) => {
                event.preventDefault();
                setSelected({
                  project_id: projectId,
                  tab: "head",
                  left: "HEAD",
                  right: "worktree",
                  show_untracked: true,
                });
                props.onSelected();
              }}
            >
              <button class="load-button" type="submit">
                Load
              </button>
            </form>
            <Show when={selected()} keyed>
              {(selection) => (
                <ChangeSet
                  active={props.active}
                  engine={props.engine}
                  params={selection}
                  view={props.view}
                  fileTreeOpen={props.fileTreeOpen}
                  debugHudOpen={props.debugHudOpen}
                  profile={props.selectedProfile}
                  appHeaderOutlets={props.appHeaderOutlets}
                  onToggleView={props.onToggleView}
                  onFileTreeOpenChange={props.onFileTreeOpenChange}
                  onDebugHudOpenChange={props.onDebugHudOpenChange}
                />
              )}
            </Show>
          </>
        );
      }}
    </Show>
  );
}

/**
 * Renders the repo gate or the required-repository Refs Tab.
 *
 * The explicit null gate ensures the query-owning child never receives a missing
 * or placeholder project ID. Workspace reset replaces repository identity.
 */
function RefsTab(
  props: TabProps & {
    /**
     * Serializes a retained complete pair after Load or later reactivation.
     *
     * `left` and `right` are the exact accepted free-form old/new texts. The
     * callback does not run for edits or without a repository. The caller may
     * replace URL state; the child retains matching DiffParams before invocation,
     * and no controlled handback or subsequent local operation is required.
     *
     * @param left Exact accepted old-side Git ref.
     * @param right Exact accepted new-side Git ref.
     */
    onSelected: (left: string, right: string) => void;
  },
): JSX.Element {
  return (
    <Show
      when={props.repoId}
      keyed
      fallback={
        <RefsWithoutRepo
          active={props.active}
          metadataTarget={props.metadataTarget}
          onRepoSelected={props.onRepoSelected}
        />
      }
    >
      {(projectId) => (
        <RefsRepoTab
          active={props.active}
          projectId={projectId}
          engine={props.engine}
          view={props.view}
          fileTreeOpen={props.fileTreeOpen}
          debugHudOpen={props.debugHudOpen}
          selectedProfile={props.selectedProfile}
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
          onToggleView={props.onToggleView}
          onFileTreeOpenChange={props.onFileTreeOpenChange}
          onDebugHudOpenChange={props.onDebugHudOpenChange}
        />
      )}
    </Show>
  );
}

/**
 * Defines the complete presentation contract shared by both Refs control states.
 *
 * Values and choices are realtime inputs. Each autocomplete reports completed
 * text through its corresponding callback, and the caller stores that value.
 * `action` is either Load or the required repository gate.
 */
type RefsControlsProps = {
  /**
   * Whether this persistent form is currently visible to the user.
   *
   * Hidden controls stay mounted so autocomplete edits survive Tab changes.
   */
  active: boolean;
  /**
   * Current caller-held old-side ref used as the first autocomplete seed.
   *
   * Autocomplete may protect locally edited text from later seed updates.
   */
  left: string;
  /**
   * Current caller-held new-side ref used as the second autocomplete seed.
   *
   * It is presented independently from the selected ChangeSet value.
   */
  right: string;
  /**
   * Current grouped backend suggestions shared by both ref inputs.
   *
   * An empty list leaves free-form completion available and does not disable Load.
   */
  choices: ReturnType<typeof refsChoices>;
  /**
   * Caller-provided action rendered inside each suggestions panel, or none.
   *
   * RefsControls renders the element unchanged and never invokes it itself.
   */
  panelAction: JSX.Element | null;
  /**
   * Complete action rendered after the two fields.
   *
   * Repository-backed callers supply Load; the missing-repo workflow supplies
   * RepoGate. RefsControls assigns no behavior beyond placing it in the form.
   */
  action: JSX.Element;
  /**
   * Reports direct typing in either autocomplete so the caller may warm metadata.
   *
   * It receives no text, does not run for seed changes or choice activation, and
   * null disables notification. AutocompleteInput has already stored the new local
   * text and opened its panel before invocation, then performs no follow-up;
   * completed values return separately through the side-specific callbacks.
   */
  onEditNotification: (() => void) | null;
  /**
   * Accepts the exact old-side text completed by choice, Enter, or blur.
   *
   * It does not run for untouched seed changes. The caller may update its live
   * edit state and must pass the accepted text back through `left`. Choice and
   * Enter completion store the text and close the panel before invocation, then
   * restore input focus afterward; blur invokes it before delayed panel dismissal.
   */
  onLeftDone: (value: string) => void;
  /**
   * Accepts the exact new-side text completed by choice, Enter, or blur.
   *
   * It does not run for untouched seed changes. The caller may update its live
   * edit state and must pass the accepted text back through `right`. Choice and
   * Enter completion store the text and close the panel before invocation, then
   * restore input focus afterward; blur invokes it before delayed panel dismissal.
   */
  onRightDone: (value: string) => void;
  /**
   * Handles form submission after native default submission is prevented.
   *
   * Null makes Enter and submit inert, as required by the repository gate. When
   * present, the callback receives no values and may snapshot the latest accepted
   * `left` and `right`; RefsControls performs no action after it returns.
   */
  onSubmit: (() => void) | null;
};

/**
 * Renders the free-form old/new ref inputs and their repo-dependent action.
 *
 * It stores no input, query, or selected state. Missing metadata is represented by
 * empty choices and a null panel action without disabling either text control.
 */
function RefsControls(props: RefsControlsProps): JSX.Element {
  return (
    <form
      class="tab-panel"
      hidden={!props.active}
      onSubmit={(event) => {
        event.preventDefault();
        if (props.onSubmit !== null) {
          props.onSubmit();
        }
      }}
    >
      <AutocompleteInput
        class=""
        label="Old ref"
        seed={props.left}
        placeholder="HEAD~1"
        choices={props.choices}
        inputVisible={true}
        inputPrefix={null}
        fieldAction={null}
        panelAction={props.panelAction}
        onEditNotification={props.onEditNotification}
        onDone={props.onLeftDone}
      />
      <AutocompleteInput
        class=""
        label="New ref"
        seed={props.right}
        placeholder="HEAD"
        choices={props.choices}
        inputVisible={true}
        inputPrefix={null}
        fieldAction={null}
        panelAction={props.panelAction}
        onEditNotification={props.onEditNotification}
        onDone={props.onRightDone}
      />
      {props.action}
    </form>
  );
}

/**
 * Keeps the Refs workflow usable before a global repository is selected.
 *
 * The component stores only temporary free-form input for this workspace. It starts
 * no repo query, constructs no DiffParams, and substitutes RepoGate for Load.
 */
function RefsWithoutRepo(
  props: Pick<TabProps, "active" | "metadataTarget" | "onRepoSelected">,
): JSX.Element {
  const [left, setLeft] = createSignal("HEAD~1");
  const [right, setRight] = createSignal("HEAD");

  return (
    <RefsControls
      active={props.active}
      left={left()}
      right={right()}
      choices={[]}
      panelAction={null}
      action={
        <RepoGate
          active={props.active}
          metadataTarget={props.metadataTarget}
          onSelect={props.onRepoSelected}
        />
      }
      onEditNotification={null}
      onLeftDone={setLeft}
      onRightDone={setRight}
      onSubmit={null}
    />
  );
}

/**
 * Defines the required inputs of the query-owning Refs Tab implementation.
 *
 * The repository is concrete and stable for this mount. Completed selected refs
 * are reported to App solely for canonical browser URL serialization.
 */
type RefsRepoTabProps = RepoTabProps & {
  /**
   * Serializes one complete retained refs selection after Load or reactivation.
   *
   * `left` and `right` are exact nonblank old/new values stored in DiffParams. It
   * does not run for editing or invalid submission. The caller may replace URL
   * state; this Tab already retains the matching selection before invocation and
   * needs no controlled handback or later action.
   *
   * @param left Exact accepted old-side Git ref.
   * @param right Exact accepted new-side Git ref.
   */
  onSelected: (left: string, right: string) => void;
};

/**
 * Renders free-form ref controls backed by shared autocomplete metadata.
 *
 * Both inputs remain usable without refs data. A separate selected entity controls
 * ChangeSet lifetime; URL and live input seed only the controls until explicit Load.
 */
function RefsRepoTab(props: RefsRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();

  /**
   * Parses one complete initial Refs pair from the active browser URL.
   *
   * A fresh or inactive Tab starts with the canonical pair. Partial and blank URL
   * pairs are one invalid entity: the user is notified after mount and the whole
   * pair is reconstructed from defaults without rewriting browser state.
   */
  function parseInitialRefs(): RefsDiffParams {
    if (!props.active) {
      return {
        project_id: props.projectId,
        tab: "refs",
        left: "HEAD~1",
        right: "HEAD",
      };
    }
    const search = new URLSearchParams(window.location.search);
    const left = search.get("left");
    const right = search.get("right");
    if (left === null && right === null) {
      return {
        project_id: props.projectId,
        tab: "refs",
        left: "HEAD~1",
        right: "HEAD",
      };
    }
    if (
      left === null ||
      left.trim().length === 0 ||
      right === null ||
      right.trim().length === 0
    ) {
      // Report this one startup parse failure only after the active Refs Tab has
      // mounted. The hook captures the invalid initial pair, tracks no reactive
      // input, allocates no external resource, and needs no cleanup.
      onMount(() => {
        toast.showError(
          "Could not restore refs from URL",
          new Error(
            "The URL must provide both nonblank refs. Restored HEAD~1 and HEAD for this page.",
          ),
        );
      });
      return {
        project_id: props.projectId,
        tab: "refs",
        left: "HEAD~1",
        right: "HEAD",
      };
    }
    return {
      project_id: props.projectId,
      tab: "refs",
      left,
      right,
    };
  }

  const initial = parseInitialRefs();
  const [left, setLeft] = createSignal(initial.left);
  const [right, setRight] = createSignal(initial.right);
  const [selected, setSelected] = createSignal<RefsDiffParams | null>(null);
  const loadReady = createMemo(() => {
    const currentLeft = left();
    const currentRight = right();
    return currentLeft.trim().length > 0 && currentRight.trim().length > 0;
  });
  const refs = createQuery(() => ({
    ...api.repos.refs(props.projectId),
    enabled: props.active,
  }));
  const choices = createMemo(() => refsChoices(refs.data?.ref_choices ?? null));
  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null) {
        props.onSelected(current.left, current.right);
      }
    },
  );

  /**
   * Stores and serializes the complete two-ref selection.
   *
   * The live controls may be incomplete, but submission requires both values to
   * be present and nonblank before they can become a selected comparison.
   */
  function loadRefs(): void {
    const currentLeft = left();
    const currentRight = right();
    assert(
      currentLeft.trim().length > 0 && currentRight.trim().length > 0,
      "Loading refs requires nonblank old and new refs.",
    );
    const next: RefsDiffParams = {
      project_id: props.projectId,
      tab: "refs",
      left: currentLeft,
      right: currentRight,
    };
    setSelected(next);
    props.onSelected(next.left, next.right);
  }

  /**
   * Returns the refs observer's panel-local refresh presentation.
   *
   * Both autocomplete panels may render this control; they share one observer,
   * failure state, and refetch operation rather than duplicate backend work.
   */
  function refreshControl(): JSX.Element {
    return (
      <MetadataRefresh
        label="Refresh refs"
        fetching={refs.isFetching}
        error={refs.error}
        onRefetch={() => void refs.refetch({ cancelRefetch: false })}
      />
    );
  }

  return (
    <>
      <RefsControls
        active={props.active}
        left={left()}
        right={right()}
        choices={choices()}
        panelAction={refreshControl()}
        action={
          <button class="load-button" type="submit" disabled={!loadReady()}>
            Load
          </button>
        }
        onEditNotification={() => {
          // Editing warms canonical refs; TanStack freshness avoids request spam.
          void queryClient.prefetchQuery(api.repos.refs(props.projectId));
        }}
        onLeftDone={setLeft}
        onRightDone={setRight}
        onSubmit={loadRefs}
      />
      <MetadataStatusPortal
        active={true}
        kind="refs"
        target={props.metadataTarget}
        loading={refs.isFetching}
        error={refs.error}
        loadingText="Loading refs..."
        errorTitle="Failed to load refs"
        onRetry={() => void refs.refetch()}
      />
      <Show when={selected()} keyed>
        {(selection) => (
          <ChangeSet
            active={props.active}
            engine={props.engine}
            params={selection}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Calculates domain-independent grouped autocomplete choices from one refs entity.
 *
 * The calculation preserves backend order and adds only established display labels
 * and built-in descriptions. Missing metadata produces an empty list.
 */
function refsChoices(refs: RefChoices | null) {
  if (refs === null) {
    return [];
  }
  return [
    ...refs.builtins.map((value) => ({
      value,
      label: value,
      description: builtinDescriptions[value],
      group: "Built-ins",
    })),
    ...refs.local_branches.map((value) => ({
      value,
      label: value,
      description: null,
      group: "Local branches",
    })),
    ...refs.remote_branches.map((value) => ({
      value: value.gitref,
      label: value.gitref,
      description: null,
      group: "Remote branches",
    })),
  ];
}

/**
 * Renders the repo gate or required-repository Branch Review Tab.
 *
 * The explicit null gate supplies one concrete project ID, so refs/defaults
 * queries keep canonical identities and no placeholder query is invented.
 */
function BranchReviewTab(
  props: TabProps & {
    /**
     * Serializes a complete retained base/review pair after Load or reactivation.
     *
     * Both arguments are exact accepted structured selections with all remote
     * data present. It does not run for edits, incomplete controls, or without a
     * repository. The caller may replace URL state; the child retains the matching
     * DiffParams first and needs no controlled handback or later operation.
     *
     * @param base Complete accepted base-side structured branch.
     * @param review Complete accepted review-side structured branch.
     */
    onSelected: (base: BranchSelection, review: BranchSelection) => void;
  },
): JSX.Element {
  return (
    <Show
      when={props.repoId}
      keyed
      fallback={
        <RepoGate
          active={props.active}
          metadataTarget={props.metadataTarget}
          onSelect={props.onRepoSelected}
        />
      }
    >
      {(projectId) => (
        <BranchReviewRepoTab
          active={props.active}
          projectId={projectId}
          engine={props.engine}
          view={props.view}
          fileTreeOpen={props.fileTreeOpen}
          debugHudOpen={props.debugHudOpen}
          selectedProfile={props.selectedProfile}
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
          onToggleView={props.onToggleView}
          onFileTreeOpenChange={props.onFileTreeOpenChange}
          onDebugHudOpenChange={props.onDebugHudOpenChange}
        />
      )}
    </Show>
  );
}

/**
 * Defines the required inputs of the query-owning Branch Review implementation.
 *
 * The repository is concrete for this mount. Completed branch selections are
 * reported only for canonical browser URL serialization.
 */
type BranchReviewRepoTabProps = RepoTabProps & {
  /**
   * Serializes a complete retained base/review pair after Load or reactivation.
   *
   * `base` and `review` are the exact validated local-or-remote selections. The
   * callback is absent from field edits and incomplete submissions. The caller may
   * replace URL state; this Tab already retained the pair and requires no value
   * returned or subsequent local work.
   *
   * @param base Complete accepted base-side structured branch.
   * @param review Complete accepted review-side structured branch.
   */
  onSelected: (base: BranchSelection, review: BranchSelection) => void;
};

/**
 * Represents whether Preset has no selection, awaits its requested catalog
 * default, or contains the complete immutable manifest parameters.
 *
 * Catalog arrival alone cannot create a ChangeSet without the waiting command.
 * Live kind and highlighted control state are not selected Tab parameters.
 *
 * A wait's `presetType` is the catalog the user asked for, which is `null`
 * when nobody has asked for one yet and the first listed catalog will answer.
 * It exists so a wait cannot be completed against a catalog selected after it.
 */
type PresetSelected =
  | {
      /**
       * Marks an explicit selection command that cannot complete before catalog data.
       *
       * This arm mounts no ChangeSet and becomes inert once replaced by DiffParams.
       */
      kind: "waiting-default";
      /**
       * Catalog identity the command must still match when data arrives.
       *
       * Null means no catalog was named and only the first listed catalog may
       * answer; changing kinds creates a newly tagged command instead.
       */
      presetType: PresetType | null;
    }
  | PresetDiffParams
  | null;

/**
 * Defines the shared presentational contract of Branch Review controls.
 *
 * Realtime selections and refs remain with the caller. Query-backed actions are
 * explicit nullable slots; `action` is either Load or the repository gate.
 */
type BranchReviewControlsProps = {
  /**
   * Whether this retained form is currently visible.
   *
   * Hidden controls remain mounted with caller-held input intact.
   */
  active: boolean;
  /**
   * Effective structured base value currently presented by the first field pair.
   *
   * It may still contain empty text while defaults load or the user edits.
   */
  base: BranchSelection;
  /**
   * Effective structured review value presented by the second field pair.
   *
   * It remains separate from any immutable selected ChangeSet parameters.
   */
  review: BranchSelection;
  /**
   * Current backend-preferred remote used when a control enters remote mode.
   *
   * Empty means no complete remote default is available; controls do not invent one.
   */
  defaultRemote: string;
  /**
   * Current canonical ref choices used for remote and branch suggestions.
   *
   * Null keeps every text field free-form without treating missing data as empty
   * backend metadata.
   */
  refs: RefChoices | null;
  /**
   * Factory for the base field's duplicated position-specific action, or none.
   *
   * BranchSelectionFields invokes a non-null factory with no arguments once for
   * the base source field and once for its branch field during reactive rendering.
   * The callback may read caller state and must return the complete action JSX;
   * null prevents both calls. Each result is inserted beside its field immediately
   * after the callback returns, with no controlled state handoff or retained copy.
   */
  baseFieldAction: (() => JSX.Element) | null;
  /**
   * Factory for the suggestions-panel action shared by all autocomplete fields.
   *
   * Each BranchSelectionFields invokes a non-null zero-argument factory for its
   * source and branch inputs during reactive rendering. The callback may read
   * caller query state and must return one complete action; null prevents every
   * call. Each result is handed to AutocompleteInput immediately, which renders it
   * only with an open panel; no state value is passed back or retained here.
   */
  panelAction: (() => JSX.Element) | null;
  /**
   * Current defaults-query failure shown beside the controls, or null.
   *
   * Ref-query failures remain with their refresh/status presentations.
   */
  error: Error | null;
  /**
   * Complete trailing form action supplied by the current repository state.
   *
   * It is the Load button for a concrete repo and is rendered unchanged.
   */
  action: JSX.Element;
  /**
   * Retries the canonical defaults observer from its visible error popover.
   *
   * It is not invoked without `error` because no retry control exists. The caller
   * may start a refetch and must return new state through `error`; controls do no
   * work after the synchronous callback, and the popover remains until caller
   * state removes or replaces the failure.
   */
  onErrorRetry: () => void;
  /**
   * Reports direct text edits so the caller may warm shared ref metadata.
   *
   * It receives no edited value and does not run for seed changes or choice
   * activation. AutocompleteInput has already stored the local text and opened its
   * panel before invocation, then performs no follow-up. Completed selections
   * arrive through the side-specific callbacks.
   */
  onEditNotification: () => void;
  /**
   * Accepts the exact complete-or-in-progress structured base produced by a field.
   *
   * It runs after source toggles and remote/branch completion, not for untouched
   * seed changes. The caller stores the value and must return it through `base`;
   * controls then render from that accepted controlled selection. A source toggle
   * performs no later work; autocomplete choice and Enter restore focus afterward,
   * while blur schedules panel dismissal after invocation.
   */
  onBaseSelection: (selection: BranchSelection) => void;
  /**
   * Accepts the exact complete-or-in-progress structured review from its field.
   *
   * It runs after source toggles and remote/branch completion, not for untouched
   * seed changes. The caller stores the value and must return it through `review`;
   * controls then render from that accepted controlled selection. A source toggle
   * performs no later work; autocomplete choice and Enter restore focus afterward,
   * while blur schedules panel dismissal after invocation.
   */
  onReviewSelection: (selection: BranchSelection) => void;
  /**
   * Requests an immutable snapshot of the effective pair on form submission.
   *
   * It receives no arguments because the caller holds `base` and `review`. The
   * callback is not invoked merely by editing; it may assert completeness, retain
   * DiffParams, and serialize them. Controls perform no later action.
   */
  onSubmit: () => void;
};

/**
 * Renders both structured branch inputs and their smallest dependent actions.
 *
 * It stores no query, defaults, mutation, selected value, or URL state. Null refs
 * keep every field free-form, and callers place RepoGate exactly where Load sits.
 */
function BranchReviewControls(props: BranchReviewControlsProps): JSX.Element {
  return (
    <form
      class="tab-panel"
      hidden={!props.active}
      onSubmit={(event) => {
        event.preventDefault();
        props.onSubmit();
      }}
    >
      <BranchSelectionFields
        sourceLabel="Base remote"
        branchLabel="Base branch"
        branchPlaceholder="base branch"
        selection={props.base}
        defaultRemote={props.defaultRemote}
        refs={props.refs}
        fieldAction={props.baseFieldAction}
        panelAction={props.panelAction}
        onEditNotification={props.onEditNotification}
        onSelection={props.onBaseSelection}
      />
      <BranchSelectionFields
        sourceLabel="Branch remote"
        branchLabel="Branch to review"
        branchPlaceholder="review branch"
        selection={props.review}
        defaultRemote={props.defaultRemote}
        refs={props.refs}
        fieldAction={null}
        panelAction={props.panelAction}
        onEditNotification={props.onEditNotification}
        onSelection={props.onReviewSelection}
      />
      <Show when={props.error} keyed>
        {(error) => (
          <ErrorPopover
            title="Failed to load repository defaults"
            error={error}
            onRetry={props.onErrorRetry}
            trigger={<span>Repository defaults failed</span>}
            triggerClass="compact-error-trigger controls-error-trigger"
            triggerLabel="Show repository defaults error"
          />
        )}
      </Show>
      {props.action}
    </form>
  );
}

/**
 * Renders structured branch controls backed by canonical refs/defaults queries.
 *
 * Untouched inputs derive realtime defaults. URL and default values seed only the
 * controls; explicit Load snapshots the current values. Later edits do not alter
 * the mounted ChangeSet until another Load.
 */
function BranchReviewRepoTab(props: BranchReviewRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  let initialBranches: ReturnType<typeof branchPairFromUrl> = null;
  if (props.active) {
    try {
      initialBranches = branchPairFromUrl();
    } catch (error) {
      // Defer the one captured startup validation failure until the active Tab is
      // mounted. This hook observes no later URL or prop changes, creates no
      // external resource, and therefore needs no cleanup.
      onMount(() => {
        toast.showError("Could not restore Branch Review from URL", error);
      });
    }
  }
  const [baseEdit, setBaseEdit] = createSignal<BranchSelection | null>(
    initialBranches?.base ?? null,
  );
  const [reviewEdit, setReviewEdit] = createSignal<BranchSelection | null>(
    initialBranches?.review ?? null,
  );
  const [selected, setSelected] = createSignal<BranchReviewDiffParams | null>(
    null,
  );
  const refs = createQuery(() => ({
    ...api.repos.refs(props.projectId),
    enabled: props.active,
  }));
  const defaults = createQuery(() => ({
    ...api.repos.defaults(props.projectId),
    enabled: props.active,
  }));
  const defaultRemote = createMemo(() => {
    const selection = defaults.data?.default_base_selection;
    return selection?.source === "remote" ? selection.remote : "";
  });
  const base = createMemo(
    () => baseEdit() ?? defaults.data?.default_base_selection ?? null,
  );
  const review = createMemo(
    () => reviewEdit() ?? defaults.data?.preferred_review_selection ?? null,
  );
  const baseControl = createMemo<BranchSelection>(
    () => base() ?? { source: "local", branch: "" },
  );
  const reviewControl = createMemo<BranchSelection>(
    () => review() ?? { source: "local", branch: "" },
  );
  const saveMainBranch = createMutation(() => ({
    ...api.repos.saveMainBranch(),
    /**
     * Refreshes repository defaults after the backend accepts the main branch.
     *
     * TanStack invokes this only on success. The callback invalidates the one
     * canonical defaults key and does not copy defaults into Branch Review state.
     */
    onSuccess() {
      void queryClient.invalidateQueries({
        queryKey: api.repos.defaults(props.projectId).queryKey,
      });
    },
  }));

  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null) {
        props.onSelected(current.base_selection, current.review_selection);
      }
    },
  );

  /**
   * Replaces the Tab's immutable selected pair and serializes it through App.
   *
   * Callers provide two complete structured selections. This is the sole path that
   * changes the current Branch Review ChangeSet identity.
   *
   * @param baseSelection Complete accepted local or remote base side.
   * @param reviewSelection Complete accepted local or remote review side paired
   * with the base in the new immutable DiffParams.
   */
  function selectBranchReview(
    baseSelection: BranchSelection,
    reviewSelection: BranchSelection,
  ): void {
    setSelected({
      project_id: props.projectId,
      tab: "branch-review",
      base_selection: baseSelection,
      review_selection: reviewSelection,
    });
    props.onSelected(baseSelection, reviewSelection);
  }

  /**
   * Confirms the complete effective base/review pair for this Tab.
   *
   * Missing defaults or selection fields block submission visibly by leaving the
   * action inert; complete selections are serialized and retained as user edits.
   */
  function loadBranchReview(): void {
    const baseSelection = selectedBranch(base());
    const reviewSelection = selectedBranch(review());
    assert(
      baseSelection !== null && reviewSelection !== null,
      "Branch Review submission requires complete base and review selections.",
    );
    setBaseEdit(baseSelection);
    setReviewEdit(reviewSelection);
    selectBranchReview(baseSelection, reviewSelection);
  }

  /**
   * Returns the shared refs observer's panel-local refresh presentation.
   *
   * Every branch/remote autocomplete receives its own rendering of the same state
   * and operation; no standing field icon is rendered.
   */
  function refreshControl(): JSX.Element {
    return (
      <MetadataRefresh
        label="Refresh branches and remotes"
        fetching={refs.isFetching}
        error={refs.error}
        onRefetch={() => void refs.refetch({ cancelRefetch: false })}
      />
    );
  }

  /**
   * Returns the old two-position Save main branch control for the effective base.
   *
   * The button is disabled until a complete nonempty local or remote base exists.
   * Both rendered instances invoke the same canonical mutation.
   */
  function saveMainBranchControl(): JSX.Element {
    const failure = saveMainBranch.error;
    if (failure !== null) {
      const variables = expect(
        saveMainBranch.variables,
        "Main-branch save error is missing its command input.",
      );
      return (
        <ErrorPopover
          title="Failed to save main branch"
          error={failure}
          onRetry={() => saveMainBranch.mutate(variables)}
          trigger={<Save class="field-icon" aria-hidden="true" />}
          triggerClass="field-icon-button is-error"
          triggerLabel="Show save main branch error"
        />
      );
    }
    const selection = selectedBranch(base());
    const savable = selection !== null;
    return (
      <button
        type="button"
        class="field-icon-button"
        aria-label="Save main branch"
        title="Save main branch"
        disabled={!savable || saveMainBranch.isPending}
        onClick={() => {
          const current = expect(
            selectedBranch(base()),
            "Saving main branch requires a base selection.",
          );
          saveMainBranch.mutate({
            projectId: props.projectId,
            selection: current,
          });
        }}
      >
        <Save class="field-icon" aria-hidden="true" />
      </button>
    );
  }

  return (
    <>
      <BranchReviewControls
        active={props.active}
        base={baseControl()}
        review={reviewControl()}
        defaultRemote={defaultRemote()}
        refs={refs.data?.ref_choices ?? null}
        baseFieldAction={saveMainBranchControl}
        panelAction={refreshControl}
        error={defaults.error}
        action={
          <button
            class="load-button"
            type="submit"
            disabled={
              selectedBranch(base()) === null ||
              selectedBranch(review()) === null
            }
          >
            Load
          </button>
        }
        onErrorRetry={() => void defaults.refetch()}
        onEditNotification={() => {
          // Editing warms shared branch/ref metadata without receiving live text.
          void queryClient.prefetchQuery(api.repos.refs(props.projectId));
        }}
        onBaseSelection={setBaseEdit}
        onReviewSelection={setReviewEdit}
        onSubmit={loadBranchReview}
      />
      <MetadataStatusPortal
        active={true}
        kind="defaults"
        target={props.metadataTarget}
        loading={defaults.isFetching}
        error={defaults.error}
        loadingText="Loading repo defaults..."
        errorTitle="Failed to load repo defaults"
        onRetry={() => void defaults.refetch()}
      />
      <Show when={selected()} keyed>
        {(selection) => (
          <ChangeSet
            active={props.active}
            engine={props.engine}
            params={selection}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Defines the complete inputs for one structured source/branch field pair.
 *
 * The caller stores the selected BranchSelection; the component stores no duplicate
 * domain state and reports complete local or remote variants after interaction.
 */
type BranchSelectionFieldsProps = {
  /**
   * Caption for the source/remote control in this side of the comparison.
   *
   * It must distinguish base from review context for the user.
   */
  sourceLabel: string;
  /**
   * Caption for the branch autocomplete paired with the source control.
   *
   * The label describes the branch text later returned in `selection`.
   */
  branchLabel: string;
  /**
   * Native hint shown only while the branch input text is empty.
   *
   * It is never accepted as branch data or returned to the caller.
   */
  branchPlaceholder: string;
  /**
   * Caller-controlled structured selection currently rendered by both fields.
   *
   * Empty branch or remote text is allowed during editing but cannot be loaded
   * until the outer workflow validates completeness.
   */
  selection: BranchSelection;
  /**
   * Backend-preferred remote used to enter remote mode or seed an empty remote.
   *
   * Empty preserves incomplete state when no default exists; the fields never
   * choose another remote implicitly.
   */
  defaultRemote: string;
  /**
   * Current canonical repository metadata for suggestions, or genuine absence.
   *
   * Null leaves the controls free-form and retains the supplied selection.
   */
  refs: RefChoices | null;
  /**
   * Builds the caller action rendered beside each source and branch caption.
   *
   * When non-null, BranchSelectionFields invokes this zero-argument callback once
   * for each of its two fields during reactive rendering. It may read caller state
   * and must return the complete action JSX; null prevents both calls. Each result
   * is passed immediately to AutocompleteInput, with no controlled value returned
   * to the callback and no retained copy after rendering.
   */
  fieldAction: (() => JSX.Element) | null;
  /**
   * Builds the caller action available inside each autocomplete suggestions panel.
   *
   * When non-null, BranchSelectionFields invokes this zero-argument callback once
   * for each of its two fields during reactive rendering. It may read caller query
   * state and must return complete action JSX; null prevents both calls. Each result
   * is passed immediately to AutocompleteInput, which shows it only while its panel
   * is open; neither component returns controlled state or retains the JSX as data.
   */
  panelAction: (() => JSX.Element) | null;
  /**
   * Reports direct text editing so the caller may warm canonical ref metadata.
   *
   * It receives no text and is not called for seed updates, source toggles, or
   * choice activation. Completed state arrives through `onSelection`; the fields
   * take no action after this notification.
   */
  onEditNotification: () => void;
  /**
   * Accepts each structured selection produced by source toggle or field completion.
   *
   * `selection` is the exact local or remote value, possibly incomplete while the
   * user edits. The callback may store it and must pass accepted state back through
   * the `selection` prop. It does not run for untouched seeds or metadata arrival.
   * A source toggle performs no later work; autocomplete choice and Enter restore
   * focus after invocation, while blur schedules panel dismissal after invocation.
   */
  onSelection: (selection: BranchSelection) => void;
};

/**
 * Renders one source toggle/remote control beside one branch autocomplete.
 *
 * Source changes preserve branch text, remote selections require a concrete
 * remote, and branch choices follow the current structured selection variant.
 */
function BranchSelectionFields(props: BranchSelectionFieldsProps): JSX.Element {
  const remoteChoices = createMemo(() =>
    (props.refs?.remotes ?? []).map((remote) => ({
      value: remote,
      label: remote,
      description: null,
      group: "Remotes",
    })),
  );
  const branchChoices = createMemo(() => {
    const refs = props.refs;
    if (refs === null) {
      return [];
    }
    const selection = props.selection;
    if (selection.source === "local") {
      return refs.local_branches.map((branch) => ({
        value: branch,
        label: branch,
        description: null,
        group: "Local branches",
      }));
    }
    const remote =
      selection.remote.trim().length > 0
        ? selection.remote
        : props.defaultRemote;
    return refs.remote_branches
      .filter((branch) => branch.structured.remote === remote)
      .map((branch) => ({
        value: branch.structured.branch,
        label: branch.structured.branch,
        description: null,
        group: "Remote branches",
      }));
  });

  /**
   * Switches between local and remote selection while preserving branch text.
   *
   * Entering a remote selection uses the repository default remote. When defaults provide
   * none, the remote field remains empty and the incomplete selection cannot load.
   */
  function toggleSource(): void {
    if (props.selection.source === "remote") {
      props.onSelection({ source: "local", branch: props.selection.branch });
      return;
    }
    props.onSelection({
      source: "remote",
      remote: props.defaultRemote,
      branch: props.selection.branch,
    });
  }

  return (
    <>
      <AutocompleteInput
        class="branch-source-field"
        label={props.sourceLabel}
        seed={
          props.selection.source === "remote" &&
          props.selection.remote.trim().length > 0
            ? props.selection.remote
            : props.defaultRemote
        }
        placeholder="remote"
        choices={remoteChoices()}
        inputVisible={props.selection.source === "remote"}
        inputPrefix={
          <button
            type="button"
            class="branch-source-toggle"
            aria-pressed={props.selection.source === "remote"}
            onClick={toggleSource}
          >
            {props.selection.source === "remote" ? "Remote" : "Local"}
          </button>
        }
        fieldAction={props.fieldAction === null ? null : props.fieldAction()}
        panelAction={props.panelAction === null ? null : props.panelAction()}
        onEditNotification={props.onEditNotification}
        onDone={(remote) => {
          const selection = props.selection;
          assert(
            selection.source === "remote",
            "Remote completion requires a remote selection.",
          );
          props.onSelection({ ...selection, remote });
        }}
      />
      <AutocompleteInput
        class=""
        label={props.branchLabel}
        seed={props.selection.branch}
        placeholder={props.branchPlaceholder}
        choices={branchChoices()}
        inputVisible={true}
        inputPrefix={null}
        fieldAction={props.fieldAction === null ? null : props.fieldAction()}
        panelAction={props.panelAction === null ? null : props.panelAction()}
        onEditNotification={props.onEditNotification}
        onDone={(branch) => {
          const selection = props.selection;
          props.onSelection(
            selection.source === "local"
              ? { source: "local", branch }
              : { ...selection, branch },
          );
        }}
      />
    </>
  );
}

/**
 * Renders the PR URL workflow and authoritative preparation mutation.
 *
 * The input remains Tab-local. Success returns the complete backend result to App
 * for one URL-backed workspace reset; it never combines with an existing repo.
 */
function PullRequestTab(
  props: TabProps & {
    /**
     * Republishes one retained prepared selection when this Tab is reactivated.
     *
     * `selection` is the complete repository, URL, and commit tuple originally
     * reconstructed from the workspace. It does not run for input edits,
     * preparation success, failure, or initial mount. The caller may replace URL
     * state but must preserve these values; the Tab keeps its selection and needs
     * no controlled handback or subsequent action.
     */
    onSelected: (selection: PullRequestDiffParams) => void;
    /**
     * Applies the exact backend preparation result after the mutation succeeds.
     *
     * `prepared` contains the authoritative replacement repository, URL, and two
     * commits. It never runs for empty input or mutation failure. The caller may
     * invalidate repository metadata and reconstruct the workspace; accepted state
     * returns only through that reconstruction because this component deliberately
     * stores no prepared result, and it may be disposed once the callback returns.
     */
    onPrepared: (prepared: PreparedPullRequest) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const toast = useToasts();
  let pullRequestInput!: HTMLInputElement;
  const initialUrl = props.active ? (search.get("pull_request_url") ?? "") : "";
  const [url, setUrl] = createSignal(initialUrl);
  let initialSelection: PullRequestDiffParams | null = null;
  if (props.active) {
    try {
      const leftCommit = search.get("left_commit");
      const rightCommit = search.get("right_commit");
      if (leftCommit !== null || rightCommit !== null) {
        assert(
          initialUrl.trim().length > 0,
          "pull_request_url must be nonblank.",
        );
        assert(
          leftCommit !== null && leftCommit.trim().length > 0,
          "left_commit must be nonblank.",
        );
        assert(
          rightCommit !== null && rightCommit.trim().length > 0,
          "right_commit must be nonblank.",
        );
        assert(
          props.repoId !== null,
          "A selected Pull Request requires repo_id.",
        );
        initialSelection = {
          tab: "pull-request",
          project_id: props.repoId,
          pull_request_url: initialUrl,
          left_commit: leftCommit,
          right_commit: rightCommit,
        };
      }
    } catch (error) {
      // Present the one captured URL validation failure after this active Tab has
      // mounted. The hook does not track later input or URL state, installs no
      // resource, and needs no cleanup.
      onMount(() => {
        toast.showError("Could not restore pull request from URL", error);
      });
    }
  }
  const [selected] = createSignal<PullRequestDiffParams | null>(
    initialSelection,
  );
  const prepare = createMutation(() => ({
    ...api.pullRequest.prepare(),
    /**
     * Reports authoritative prepared Pull Request values to the Tab.
     *
     * TanStack invokes this only after the backend resolves the URL. The callback
     * passes the complete result upward without retaining a second prepared value.
     */
    onSuccess(prepared: PreparedPullRequest) {
      props.onPrepared(prepared);
    },
  }));

  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null) {
        props.onSelected(current);
      }
    },
  );

  /**
   * Submits the complete trimmed PR URL to the preparation mutation.
   *
   * Empty input is rejected locally as a required interaction field rather than
   * being sent as a meaningless backend command.
   */
  function preparePullRequest(): void {
    const selectedUrl = url().trim();
    if (selectedUrl.length === 0) {
      pullRequestInput.reportValidity();
      return;
    }
    prepare.mutate(selectedUrl);
  }

  return (
    <>
      <form
        class="tab-panel"
        hidden={!props.active}
        onSubmit={(event) => {
          event.preventDefault();
          preparePullRequest();
        }}
      >
        <label class="field pull-request-field">
          <span>Pull request</span>
          <input
            ref={pullRequestInput}
            required
            value={url()}
            placeholder="GitHub PR or GitLab MR URL"
            spellcheck={false}
            autocomplete="off"
            onInput={(event) => setUrl(event.currentTarget.value)}
          />
        </label>
        <Show when={prepare.error} keyed>
          {(error) => (
            <ErrorPopover
              title="Failed to prepare pull request"
              error={error}
              onRetry={preparePullRequest}
              trigger={<span>Pull request preparation failed</span>}
              triggerClass="compact-error-trigger controls-error-trigger"
              triggerLabel="Show pull request error"
            />
          )}
        </Show>
        <button class="load-button" type="submit" disabled={prepare.isPending}>
          {prepare.isPending ? "Loading..." : "Load"}
        </button>
      </form>
      <MetadataStatusPortal
        active={props.active}
        kind="pull-request"
        target={props.metadataTarget}
        loading={prepare.isPending}
        error={null}
        loadingText="Preparing pull request..."
        errorTitle="Failed to prepare pull request"
        onRetry={preparePullRequest}
      />
      <Show when={selected()} keyed>
        {(selection) => (
          <ChangeSet
            active={props.active}
            engine={props.engine}
            params={selection}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Renders preset-kind and subset controls backed by the shared catalog query.
 *
 * Preset ignores global repo but preserves it in App. Catalog defaults remain
 * derived query data until the user selects a subset, and engine stays reactive.
 */
function PresetTab(
  props: TabProps & {
    /**
     * Serializes one catalog-backed subset after selection or later reactivation.
     *
     * `presetType` is the exact validated catalog ID and `preset` the exact group
     * ID selected within it. It does not run while data is missing, a waiting
     * command targets another catalog, or submission lacks a preset. The caller
     * may replace browser URL state; the Tab stores matching DiffParams before the
     * call and requires no controlled handback or later operation.
     *
     * @param presetType Validated backend catalog identity.
     * @param preset Exact selected group identity within that catalog.
     */
    onSelected: (presetType: PresetType, preset: string) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const toast = useToasts();
  const initialType = props.active ? search.get("preset_type") : null;
  if (props.active && initialType === null) {
    // Report the missing startup catalog only after this active Tab has mounted.
    // This one-shot hook tracks no later catalog or URL changes, allocates no
    // external resource, and needs no cleanup.
    onMount(() => {
      toast.showError(
        "Could not restore preset type from URL",
        new Error(
          "preset_type is missing. Restored the first preset catalog for this page.",
        ),
      );
    });
  }
  // Null until somebody names a catalog: which catalogs exist is a backend
  // directory listing, so a URL value cannot be judged before it arrives.
  const [presetType, setPresetType] = createSignal<PresetType | null>(
    initialType,
  );
  const initialPreset = props.active ? search.get("preset_subset") : null;
  assert(
    initialPreset === null || initialPreset.length > 0,
    "preset_subset must not be empty.",
  );
  const [selected, setSelected] = createSignal<PresetSelected>(
    props.active ? { kind: "waiting-default", presetType: initialType } : null,
  );
  const [highlightedPreset, setHighlightedPreset] = createSignal<string | null>(
    initialPreset,
  );
  const catalogs = createQuery(() => ({
    ...api.presets.catalogs(),
    enabled: props.active,
  }));

  /**
   * Selects the one listed catalog these controls are showing.
   *
   * Null while the listing is unknown, and null when the backend offers no
   * catalog at all. No preset can be selected in either state, and neither is
   * repaired here. A `preset_type` the listing does not contain is
   * a broken URL and throws to the surrounding boundary, which is the same
   * outcome an unsupported value had when the set was compiled in.
   */
  const activeCatalog = createMemo(() => {
    const listed = catalogs.data;
    if (listed === undefined) {
      return null;
    }
    const requested = presetType();
    if (requested === null) {
      return listed[0] ?? null;
    }
    const found = listed.find((catalog) => catalog.id === requested);
    assert(found !== undefined, `Unsupported preset_type: ${requested}.`);
    return found;
  });
  const effectivePreset = createMemo(() => {
    const highlighted = highlightedPreset();
    if (highlighted !== null) {
      return highlighted;
    }
    return activeCatalog()?.default_preset ?? null;
  });
  const selectedValue = createMemo(() => {
    const current = selected();
    if (current === null || "kind" in current) {
      return null;
    }
    return current;
  });

  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null && !("kind" in current)) {
        props.onSelected(current.project_id, current.preset_subset);
      } else {
        const preset = effectivePreset();
        if (activeCatalog() === null || preset === null) {
          setSelected({ kind: "waiting-default", presetType: presetType() });
        } else {
          selectPreset(preset);
        }
      }
    },
  );

  /**
   * Completes only an explicitly waiting Preset selection from its live catalog.
   *
   * The effect observes active state, the tagged selection, the requested
   * preset kind, the listed catalog answering it, and the effective catalog
   * default. Once a concrete catalog and preset both exist it performs the
   * external URL/selection command and replaces the command with complete
   * parameters, making itself inert. It creates no external subscription and is
   * disposed with the eternal Tab.
   */
  createEffect(
    on(
      [
        () => props.active,
        selected,
        presetType,
        activeCatalog,
        effectivePreset,
      ] as const,
      ([active, current, type, catalog, preset]) => {
        if (
          !active ||
          current === null ||
          !("kind" in current) ||
          current.kind !== "waiting-default" ||
          current.presetType !== type
        ) {
          return;
        }
        if (catalog !== null && preset !== null) {
          selectPreset(preset);
        }
      },
    ),
  );

  /**
   * Selects and serializes one concrete preset subset for the current kind.
   *
   * The value must come from the visible validated catalog; no missing/default
   * placeholder is stored as user selection. The catalog it belongs to is the
   * shown one, so callers must not call this before the listing arrives.
   */
  function selectPreset(preset: string): void {
    const catalog = activeCatalog();
    assert(
      catalog !== null,
      "Selecting a preset requires a listed preset catalog.",
    );
    setHighlightedPreset(preset);
    setSelected({
      project_id: catalog.id,
      tab: "preset",
      preset_subset: preset,
    });
    props.onSelected(catalog.id, preset);
  }

  return (
    <>
      <form
        class="tab-panel"
        hidden={!props.active}
        onSubmit={(event) => {
          event.preventDefault();
          const preset = effectivePreset();
          assert(
            preset !== null,
            "Loading a Preset requires a selected preset.",
          );
          selectPreset(preset);
        }}
      >
        <fieldset class="tab-choices preset-tabs preset-kind-tabs">
          <legend>Preset type</legend>
          <For each={catalogs.data}>
            {(catalog) => (
              <button
                type="button"
                classList={{ "is-active": activeCatalog()?.id === catalog.id }}
                aria-pressed={activeCatalog()?.id === catalog.id}
                onClick={() => {
                  setPresetType(catalog.id);
                  setHighlightedPreset(null);
                  setSelected({
                    kind: "waiting-default",
                    presetType: catalog.id,
                  });
                }}
              >
                {catalog.name}
              </button>
            )}
          </For>
          <MetadataRefresh
            label="Refresh presets"
            fetching={catalogs.isFetching}
            error={catalogs.error}
            onRefetch={() => void catalogs.refetch({ cancelRefetch: false })}
          />
        </fieldset>
        <Show when={activeCatalog()} keyed>
          {(catalog) => (
            <fieldset class="tab-choices preset-tabs preset-subset-tabs">
              <legend>Presets</legend>
              <For each={catalog.groups}>
                {(group) => (
                  <button
                    type="button"
                    classList={{ "is-active": effectivePreset() === group.id }}
                    aria-pressed={effectivePreset() === group.id}
                    onClick={() => selectPreset(group.id)}
                  >
                    {group.display_name}
                  </button>
                )}
              </For>
            </fieldset>
          )}
        </Show>
        <button
          class="load-button"
          type="submit"
          disabled={activeCatalog() === null || effectivePreset() === null}
        >
          Load
        </button>
      </form>
      <MetadataStatusPortal
        active={props.active}
        kind="presets"
        target={props.metadataTarget}
        loading={catalogs.isPending || catalogs.isFetching}
        error={catalogs.error}
        loadingText="Loading presets..."
        errorTitle="Failed to load presets"
        onRetry={() => void catalogs.refetch()}
      />
      <Show when={selectedValue()} keyed>
        {(selection) => (
          <ChangeSet
            active={props.active}
            engine={props.engine}
            params={selection}
            view={props.view}
            fileTreeOpen={props.fileTreeOpen}
            debugHudOpen={props.debugHudOpen}
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
            onFileTreeOpenChange={props.onFileTreeOpenChange}
            onDebugHudOpenChange={props.onDebugHudOpenChange}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Renders one active Tab's compact metadata state in AppHeader.
 *
 * The query and retry operation remain in the Tab. Portal changes physical DOM
 * placement only; inactive or settled observers contribute no header content.
 */
function MetadataStatusPortal(props: MetadataStatusPortalProps): JSX.Element {
  return (
    <Show when={props.target} keyed>
      {(target) => (
        <Show when={props.active && (props.loading || props.error !== null)}>
          <Portal mount={target}>
            <div
              class={`summary-group summary-group-status summary-status-${props.kind}`}
            >
              <Show
                when={!props.loading ? props.error : null}
                keyed
                fallback={<span>{props.loadingText}</span>}
              >
                {(error) => (
                  <ErrorPopover
                    title={props.errorTitle}
                    error={error}
                    onRetry={props.onRetry}
                    trigger={<span>{props.errorTitle}</span>}
                    triggerClass="compact-error-trigger summary-error-trigger"
                    triggerLabel={`Show ${props.errorTitle.toLowerCase()}`}
                  />
                )}
              </Show>
            </div>
          </Portal>
        </Show>
      )}
    </Show>
  );
}

/**
 * Renders one metadata refresh control with persistent local failure visibility.
 *
 * Fetching disables and rotates the ordinary button. Failure replaces it with a
 * red trigger that opens complete error details; retry occurs only in the popover.
 */
function MetadataRefresh(props: MetadataRefreshProps): JSX.Element {
  return (
    <Show
      when={!props.fetching ? props.error : null}
      keyed
      fallback={
        <button
          type="button"
          class="field-icon-button metadata-refresh-button"
          aria-label={props.label}
          title={props.label}
          disabled={props.fetching}
          onClick={props.onRefetch}
        >
          <RefreshCw
            class="field-icon"
            classList={{ spinning: props.fetching }}
            aria-hidden="true"
          />
        </button>
      }
    >
      {(error) => (
        <ErrorPopover
          title={props.label}
          error={error}
          onRetry={props.onRefetch}
          trigger={<RefreshCw class="field-icon" aria-hidden="true" />}
          triggerClass="field-icon-button metadata-refresh-button is-error"
          triggerLabel={`Show ${props.label.toLowerCase()} error`}
        />
      )}
    </Show>
  );
}

/**
 * Gates the smallest repo-dependent action on shared repository metadata.
 *
 * The rest of the active Tab stays mounted. Users may select or remove a marked
 * repository, and every error remains compact locally with complete popover detail.
 */
function RepoGate(props: RepoGateProps): JSX.Element {
  const queryClient = useQueryClient();
  const repos = createQuery(() => ({
    ...api.repos.list(),
    enabled: props.active,
  }));
  const repositories = createMemo<RepositoryState>(() => {
    if (repos.error !== null) {
      return { state: "failed", error: repos.error };
    }
    if (repos.isPending) {
      return { state: "pending" };
    }
    return {
      state: "available",
      repos: expect(
        repos.data,
        "A settled repository query requires data or an explicit error.",
      ),
    };
  });
  const repositoryError = createMemo(() => {
    const current = repositories();
    return current.state === "failed" ? current.error : null;
  });
  const availableRepositories = createMemo(() => {
    const current = repositories();
    return current.state === "available" ? current.repos : null;
  });
  const removeRepo = createMutation(() => ({
    ...api.repos.remove(),
    /**
     * Refreshes repository metadata after successful gatekeeper removal.
     *
     * TanStack invokes this only after backend success. The callback invalidates
     * the canonical list; RepoGate derives its next presentation from that query.
     */
    onSuccess() {
      void queryClient.invalidateQueries({
        queryKey: api.repos.list().queryKey,
      });
    },
  }));

  return (
    <section class="repo-picker" aria-label="Marked repositories">
      <div class="repo-picker-heading">
        <h2>Choose a repo</h2>
        <p>Select a marked repository before loading repo-backed diffs.</p>
      </div>
      <Show when={repositoryError()} keyed>
        {(error) => (
          <ErrorPopover
            title="Failed to load marked repositories"
            error={error}
            onRetry={() => void repos.refetch()}
            trigger={<span>Failed to load marked repos</span>}
            triggerClass="repo-picker-error compact-error-trigger"
            triggerLabel="Show marked repository error"
          />
        )}
      </Show>
      <Show
        when={removeRepo.error !== null && removeRepo.variables !== undefined}
      >
        <ErrorPopover
          title="Failed to remove repository"
          error={removeRepo.error}
          onRetry={() => {
            const projectId = expect(
              removeRepo.variables,
              "Repository removal error is missing its project ID.",
            );
            removeRepo.mutate(projectId);
          }}
          trigger={<span>Failed to remove marked repo</span>}
          triggerClass="repo-picker-error compact-error-trigger"
          triggerLabel="Show repository removal error"
        />
      </Show>
      <MetadataStatusPortal
        active={props.active}
        kind="repo-removal"
        target={props.metadataTarget}
        loading={removeRepo.isPending}
        error={null}
        loadingText="Removing marked repo..."
        errorTitle="Failed to remove repository"
        onRetry={() => {
          const projectId = expect(
            removeRepo.variables,
            "Repository removal retry is missing its project ID.",
          );
          removeRepo.mutate(projectId);
        }}
      />
      <Show when={repositories().state === "pending"}>
        <p class="repo-picker-loading">Loading marked repos...</p>
      </Show>
      <Show when={availableRepositories()} keyed>
        {(marks) => (
          <div class="repo-list">
            <For each={marks}>
              {(repo) => (
                <div class="repo-option-row">
                  <button
                    type="button"
                    class="repo-option"
                    onClick={() => props.onSelect(repo.id)}
                  >
                    <span class="repo-option-name">{repo.name}</span>
                    <span class="repo-option-path">{repo.path}</span>
                  </button>
                  <button
                    type="button"
                    class="repo-remove-button"
                    title={`Remove ${repo.name}`}
                    aria-label={`Remove ${repo.name}`}
                    disabled={
                      removeRepo.isPending && removeRepo.variables === repo.id
                    }
                    onClick={() => {
                      // Repository removal remains an explicit user-confirmed action.
                      if (
                        window.confirm(
                          `Remove ${repo.name} from marked repositories?`,
                        )
                      ) {
                        removeRepo.mutate(repo.id);
                      }
                    }}
                  >
                    <Trash2 class="repo-remove-icon" aria-hidden="true" />
                  </button>
                </div>
              )}
            </For>
          </div>
        )}
      </Show>
    </section>
  );
}
