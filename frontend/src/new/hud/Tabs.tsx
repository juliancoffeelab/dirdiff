/**
 * Defines the five eternal application Tabs and their selection workflows.
 *
 * The module exports TabId, TabStrip, and Tabs. Private controls own their local
 * workflow, observe only canonical metadata queries, and return complete selected
 * values that Tabs combine with App-owned repo and engine into DiffParams. It does
 * not own global workspace state, backend response copies, or ChangeSet internals.
 */
import {
  type Accessor,
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  on,
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
  type DiffEngine,
  type HeadDiffParams,
  type PreparedPullRequest,
  type PresetDiffParams,
  type PresetType,
  type ProjectId,
  type RefChoices,
  type RefsDiffParams,
  type RepoMark,
} from "../api/api";
import { AutocompleteInput } from "../comp/AutocompleteInput";
import { ErrorPopover, UnexpectedErrorBoundary } from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets } from "./AppHeader";
import { ChangeSet } from "./ChangeSet";

/**
 * Identifies one user-visible application Tab.
 *
 * The value selects a mounted control workflow and URL tab field. It must not be
 * used as a backend mode without the owning Tab constructing complete DiffParams.
 */
export type TabId =
  | "head"
  | "refs"
  | "branch-review"
  | "pull-request"
  | "preset";

const tabIds: readonly TabId[] = [
  "head",
  "refs",
  "branch-review",
  "pull-request",
  "preset",
];

const tabLabels: Record<TabId, string> = {
  head: "Diff against HEAD",
  refs: "Compare refs",
  "branch-review": "Branch review",
  "pull-request": "PR",
  preset: "Preset",
};

const presetTypes: readonly PresetType[] = [
  "diff",
  "fold",
  "gumtree",
  "scroll",
];
const presetLabels: Record<PresetType, string> = {
  diff: "Diff Presets",
  fold: "Fold Presets",
  gumtree: "GumTree Presets",
  scroll: "Scroll Presets",
};
const builtinDescriptions: Record<string, string> = {
  HEAD: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
};

/**
 * Runs one reconciliation command when an eternal Tab becomes active again.
 *
 * The owned effect explicitly observes only `active`, ignores the initial mount,
 * and invokes `reactivate` solely on a false-to-true transition. Solid disposes
 * the effect with the Tab; no external subscription or cleanup exists. Because
 * `on` untracks the callback, Tab selection reads and writes cannot accidentally
 * become dependencies or create duplicate activation commands.
 */
function onTabReactivated(
  active: Accessor<boolean>,
  reactivate: () => void,
): void {
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
 * owns no Tab lifetime or URL behavior.
 */
type TabStripProps = {
  active: TabId;
  onSelect: (tab: TabId) => void;
};

/**
 * Defines every workspace value and explicit command consumed by eternal Tabs.
 *
 * Repository absence is genuine and gates only repo-backed workflows. Browser
 * URL updates remain explicit per workflow; Tabs receive no generic App setter.
 */
type TabsProps = {
  active: TabId;
  repoId: ProjectId | null;
  engine: DiffEngine;
  view: DiffViewMode;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onRepoSelected: (projectId: ProjectId) => void;
  onHeadSelected: () => void;
  onRefsSelected: (left: string, right: string) => void;
  onBranchReviewSelected: (
    base: BranchSelection,
    review: BranchSelection,
  ) => void;
  onPresetSelected: (presetType: PresetType, preset: string) => void;
  onPullRequestSelected: (
    pullRequestUrl: string,
    base: BranchSelection,
    review: BranchSelection,
  ) => void;
  onPullRequestPrepared: (prepared: PreparedPullRequest) => void;
};

/**
 * Defines shared inputs for one mounted private Tab owner.
 *
 * Active controls and expensive ChangeSet content depend on `active`; the outer
 * Tab remains mounted. Engine changes update derived DiffParams without replacing
 * selected workflow values.
 */
type TabProps = {
  active: boolean;
  repoId: ProjectId | null;
  engine: DiffEngine;
  view: DiffViewMode;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onRepoSelected: (projectId: ProjectId) => void;
};

/**
 * Defines shared required inputs after a RepoGate has narrowed repository state.
 *
 * The project ID is concrete, so canonical query definitions never receive a
 * placeholder identity. The parent workspace still owns engine and activation.
 */
type RepoTabProps = {
  active: boolean;
  projectId: ProjectId;
  engine: DiffEngine;
  view: DiffViewMode;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
};

/**
 * Defines the complete lifecycle inputs of a metadata refresh control.
 *
 * A genuine error is nullable. The supplied refetch operation owns TanStack
 * behavior; the control owns only idle, spinning, and failed presentation.
 */
type MetadataRefreshProps = {
  label: string;
  fetching: boolean;
  error: Error | null;
  onRefetch: () => void;
};

/**
 * Defines one active metadata observer's compact AppHeader projection.
 *
 * The owning Tab supplies genuine pending/error state and retry behavior. The
 * projection carries presentation only and never moves query ownership.
 */
type MetadataStatusPortalProps = {
  target: HTMLElement | null;
  active: boolean;
  kind: "defaults" | "refs" | "pull-request" | "presets" | "repo-removal";
  loading: boolean;
  error: Error | null;
  loadingText: string;
  errorTitle: string;
  onRetry: () => void;
};

/**
 * Defines RepoGate's required workspace selection inputs.
 *
 * Gate receives no repository data because it observes the shared canonical
 * query itself. Selection returns only a validated numeric project ID.
 */
type RepoGateProps = {
  active: boolean;
  metadataTarget: HTMLElement | null;
  onSelect: (projectId: ProjectId) => void;
};

/**
 * Renders the persistent top-level Tab buttons.
 *
 * Activation reports exactly one TabId. Buttons remain visually identical to
 * the established mode selector and do not mount or unmount Tab components.
 */
export function TabStrip(props: TabStripProps): JSX.Element {
  return (
    <fieldset class="mode-tabs">
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
 * Mounts all five Tab owners for one workspace lifetime.
 *
 * Only the active control panel is displayed and observes active metadata, while
 * every Tab retains local interaction and selected values. Complete selections
 * mount stable ChangeSet boundaries; no backend data is copied between Tabs.
 */
export function Tabs(props: TabsProps): JSX.Element {
  return (
    <>
      <div class="tab-owner" hidden={props.active !== "head"}>
        <UnexpectedErrorBoundary title="Head Tab failed">
          <HeadTab
            active={props.active === "head"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onHeadSelected}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-owner" hidden={props.active !== "refs"}>
        <UnexpectedErrorBoundary title="Refs Tab failed">
          <RefsTab
            active={props.active === "refs"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onRefsSelected}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-owner" hidden={props.active !== "branch-review"}>
        <UnexpectedErrorBoundary title="Branch Review Tab failed">
          <BranchReviewTab
            active={props.active === "branch-review"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onBranchReviewSelected}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-owner" hidden={props.active !== "pull-request"}>
        <UnexpectedErrorBoundary title="Pull Request Tab failed">
          <PullRequestTab
            active={props.active === "pull-request"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPullRequestSelected}
            onPrepared={props.onPullRequestPrepared}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-owner" hidden={props.active !== "preset"}>
        <UnexpectedErrorBoundary title="Preset Tab failed">
          <PresetTab
            active={props.active === "preset"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPresetSelected}
          />
        </UnexpectedErrorBoundary>
      </div>
    </>
  );
}

/**
 * Parses one browser URL branch selection without inventing missing fields.
 *
 * Callers provide the selection prefix. Complete local or remote values are
 * returned; absence returns null and malformed partial state throws visibly.
 */
function branchSelectionFromUrl(
  prefix: "base" | "review",
): BranchSelection | null {
  const search = new URLSearchParams(window.location.search);
  const source = search.get(`${prefix}_source`);
  const branch = search.get(`${prefix}_branch`);
  const remote = search.get(`${prefix}_remote`);
  if (source === null && branch === null && remote === null) {
    return null;
  }
  if (source !== "local" && source !== "remote") {
    throw new Error(`${prefix}_source must be local or remote.`);
  }
  if (branch === null || branch.length === 0) {
    throw new Error(`${prefix}_branch is required.`);
  }
  if (source === "local") {
    return { source, branch };
  }
  if (remote === null || remote.length === 0) {
    throw new Error(`${prefix}_remote is required for a remote branch.`);
  }
  return { source, remote, branch };
}

/**
 * Returns one genuinely complete branch selection or explicit absence.
 *
 * Controls may render empty local/remote values while metadata is pending or the
 * user is editing. Only nonblank branches, and nonblank remotes for remote
 * selections, may become selected DiffParams or mutation inputs.
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
 * Renders Head controls and derives the fixed complete Head DiffParams.
 *
 * Repository absence gates only this action. The selected Head value is fixed,
 * while engine remains reactive and does not replace the mounted ChangeSet.
 */
function HeadTab(props: TabProps & { onSelected: () => void }): JSX.Element {
  const [selected, setSelected] = createSignal<object | null>(
    props.active ? {} : null,
  );
  onTabReactivated(
    () => props.active,
    () => {
      if (selected() === null) {
        setSelected({});
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
          setSelected({});
          props.onSelected();
        }}
      >
        <Show
          when={props.repoId !== null}
          fallback={
            <RepoGate
              active={props.active}
              metadataTarget={props.metadataTarget}
              onSelect={props.onRepoSelected}
            />
          }
        >
          <button class="load-button" type="submit">
            Load
          </button>
        </Show>
      </form>
      <Show when={selected()} keyed>
        {(_selection) => (
          <Show when={props.repoId} keyed>
            {(projectId) => (
              <ChangeSet
                active={props.active}
                params={
                  {
                    project_id: projectId,
                    engine: props.engine,
                    mode: "head",
                    left: "head",
                    right: "worktree",
                    show_untracked: true,
                  } satisfies HeadDiffParams
                }
                view={props.view}
                appHeaderOutlets={props.appHeaderOutlets}
              />
            )}
          </Show>
        )}
      </Show>
    </>
  );
}

/**
 * Renders the repo gate or the required-repository Refs owner.
 *
 * The explicit null gate ensures the query-owning child never receives a missing
 * or placeholder project ID. Workspace reset owns repository identity lifetime.
 */
function RefsTab(
  props: TabProps & { onSelected: (left: string, right: string) => void },
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
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
        />
      )}
    </Show>
  );
}

/**
 * Defines the complete presentation contract shared by both Refs control states.
 *
 * Values and choices are realtime inputs. Completion changes only caller-owned
 * control values, while `action` is either Load or the required repository gate.
 */
type RefsControlsProps = {
  active: boolean;
  left: string;
  right: string;
  choices: ReturnType<typeof refsChoices>;
  panelAction: JSX.Element | null;
  action: JSX.Element;
  onEditNotification: (() => void) | null;
  onLeftDone: (value: string) => void;
  onRightDone: (value: string) => void;
  onSubmit: (() => void) | null;
};

/**
 * Renders the free-form old/new ref inputs and their repo-dependent action.
 *
 * It owns no input, query, or selected state. Missing metadata is represented by
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
        placeholder="head~1"
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
        placeholder="head"
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
 * The component owns only temporary free-form input for this workspace. It starts
 * no repo query, constructs no DiffParams, and substitutes RepoGate for Load.
 */
function RefsWithoutRepo(
  props: Pick<TabProps, "active" | "metadataTarget" | "onRepoSelected">,
): JSX.Element {
  const [left, setLeft] = createSignal("head~1");
  const [right, setRight] = createSignal("head");

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
  onSelected: (left: string, right: string) => void;
};

/**
 * Renders free-form ref controls backed by shared autocomplete metadata.
 *
 * Both inputs remain usable without refs data. A separate selected entity controls
 * ChangeSet lifetime; live/default input does not become selected until activation
 * or explicit completion.
 */
function RefsRepoTab(props: RefsRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const search = new URLSearchParams(window.location.search);
  const initialLeft = props.active ? search.get("left") : null;
  const initialRight = props.active ? search.get("right") : null;
  if (initialLeft !== null && initialLeft.trim().length === 0) {
    throw new Error("The selected old ref must not be blank.");
  }
  if (initialRight !== null && initialRight.trim().length === 0) {
    throw new Error("The selected new ref must not be blank.");
  }
  const [left, setLeft] = createSignal(initialLeft ?? "head~1");
  const [right, setRight] = createSignal(initialRight ?? "head");
  const [selected, setSelected] = createSignal<{
    left: string;
    right: string;
  } | null>(props.active ? { left: left(), right: right() } : null);
  const refs = createQuery(() => ({
    ...api.repos.refs(props.projectId),
    enabled: props.active,
  }));
  const choices = createMemo(() => refsChoices(refs.data?.ref_choices ?? null));
  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current === null) {
        loadRefs();
      } else {
        props.onSelected(current.left, current.right);
      }
    },
  );

  /**
   * Stores and serializes the complete two-ref selection.
   *
   * Both current component values are meaningful selected values, not query data.
   */
  function loadRefs(): void {
    if (left().trim().length === 0 || right().trim().length === 0) {
      throw new Error("Loading refs requires nonblank old and new refs.");
    }
    const next = { left: left(), right: right() };
    setSelected(next);
    props.onSelected(next.left, next.right);
  }

  /**
   * Returns the refs observer's panel-local refresh presentation.
   *
   * Both autocomplete panels may render this projection; they share one observer,
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
          <button class="load-button" type="submit">
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
            params={
              {
                project_id: props.projectId,
                engine: props.engine,
                mode: "refs",
                ...selection,
              } satisfies RefsDiffParams
            }
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Projects one refs entity into domain-independent grouped autocomplete choices.
 *
 * The projection preserves backend order and adds only established display labels
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
      description: builtinDescriptions[value] ?? null,
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
 * Renders the repo gate or required-repository Branch Review owner.
 *
 * The explicit null gate supplies one concrete project ID, so refs/defaults
 * queries keep canonical identities and no placeholder query is invented.
 */
function BranchReviewTab(
  props: TabProps & {
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
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
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
  onSelected: (base: BranchSelection, review: BranchSelection) => void;
};

/**
 * Represents whether Branch Review has no selection, awaits defaults requested by
 * activation, or owns a complete immutable selected pair.
 *
 * Live control edits are deliberately absent. Only the values variant may produce
 * DiffParams; waiting exists so asynchronous defaults cannot imply selection alone.
 */
type BranchReviewSelected =
  | { kind: "waiting-defaults" }
  | { kind: "values"; base: BranchSelection; review: BranchSelection }
  | null;

/**
 * Represents whether Preset has no selection, awaits its requested catalog
 * default, or owns one complete immutable kind/subset pair.
 *
 * Catalog arrival alone cannot create a ChangeSet without the waiting command.
 * Live kind and highlighted control state are not selected DiffParams.
 */
type PresetSelected =
  | { kind: "waiting-default"; presetType: PresetType }
  | { kind: "value"; presetType: PresetType; preset: string }
  | null;

/**
 * Stores one authoritative prepared Pull Request selection.
 *
 * The URL and both structured branches come from the same backend preparation.
 * Live Pull Request input is excluded so later edits cannot alter this identity.
 */
type SelectedPullRequest = {
  pullRequestUrl: string;
  base: BranchSelection;
  review: BranchSelection;
};

/**
 * Defines the shared presentational contract of Branch Review controls.
 *
 * Realtime selections and refs remain caller-owned. Query-backed actions are
 * explicit nullable slots; `action` is either Load or the repository gate.
 */
type BranchReviewControlsProps = {
  active: boolean;
  base: BranchSelection;
  review: BranchSelection;
  refs: RefChoices | null;
  baseFieldAction: (() => JSX.Element) | null;
  panelAction: (() => JSX.Element) | null;
  error: Error | null;
  action: JSX.Element;
  onErrorRetry: () => void;
  onEditNotification: () => void;
  onBaseSelection: (selection: BranchSelection) => void;
  onReviewSelection: (selection: BranchSelection) => void;
  onSubmit: () => void;
};

/**
 * Renders both structured branch inputs and their smallest dependent actions.
 *
 * It owns no query, defaults, mutation, selected value, or URL state. Null refs
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
 * Renders structured branch controls with canonical refs/defaults ownership.
 *
 * Untouched inputs derive realtime defaults. Activation requests a default-backed
 * selection, while explicit Load snapshots current control values. Later edits do
 * not alter the mounted ChangeSet until another completion.
 */
function BranchReviewRepoTab(props: BranchReviewRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const initialBase = props.active ? branchSelectionFromUrl("base") : null;
  const initialReview = props.active ? branchSelectionFromUrl("review") : null;
  const [baseEdit, setBaseEdit] = createSignal<BranchSelection | null>(
    initialBase,
  );
  const [reviewEdit, setReviewEdit] = createSignal<BranchSelection | null>(
    initialReview,
  );
  const [selected, setSelected] = createSignal<BranchReviewSelected>(
    props.active
      ? initialBase !== null && initialReview !== null
        ? { kind: "values", base: initialBase, review: initialReview }
        : { kind: "waiting-defaults" }
      : null,
  );
  const refs = createQuery(() => ({
    ...api.repos.refs(props.projectId),
    enabled: props.active,
  }));
  const defaults = createQuery(() => ({
    ...api.repos.defaults(props.projectId),
    enabled: props.active,
  }));
  const base = createMemo<BranchSelection | null>(() => {
    const edited = baseEdit();
    if (edited !== null) {
      return edited;
    }
    if (defaults.data === undefined) {
      return null;
    }
    const value = defaults.data.default_base_selection;
    // Heuristic failure stays an empty editable local branch, not a fake default.
    return "source" in value ? value : { source: "local", branch: "" };
  });
  const review = createMemo(
    () => reviewEdit() ?? defaults.data?.preferred_review_selection ?? null,
  );
  const baseControl = createMemo<BranchSelection>(
    () => base() ?? { source: "local", branch: "" },
  );
  const reviewControl = createMemo<BranchSelection>(
    () => review() ?? { source: "local", branch: "" },
  );
  const selectedValues = createMemo(() => {
    const current = selected();
    if (current === null || current.kind !== "values") {
      return null;
    }
    return current;
  });
  const saveMainBranch = createMutation(() => ({
    ...api.repos.saveMainBranch(),
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
      if (current !== null && current.kind === "values") {
        props.onSelected(current.base, current.review);
      } else {
        const baseSelection = selectedBranch(base());
        const reviewSelection = selectedBranch(review());
        if (baseSelection === null || reviewSelection === null) {
          setSelected({ kind: "waiting-defaults" });
        } else {
          selectBranchReview(baseSelection, reviewSelection);
        }
      }
    },
  );

  /**
   * Completes only an explicitly waiting Branch Review selection from metadata.
   *
   * The effect observes active state, the tagged selection, and realtime default
   * derivations. It is inert unless this Tab requested a default-backed selection;
   * once both sides become complete it performs the external URL/selection command,
   * changes the tag to `values`, and therefore makes itself inert. It owns no
   * subscription requiring cleanup and is disposed with the eternal Tab.
   */
  createEffect(
    on(
      [() => props.active, selected, base, review] as const,
      ([active, current, baseValue, reviewValue]) => {
        if (
          !active ||
          current === null ||
          current.kind !== "waiting-defaults"
        ) {
          return;
        }
        const baseSelection = selectedBranch(baseValue);
        const reviewSelection = selectedBranch(reviewValue);
        if (baseSelection !== null && reviewSelection !== null) {
          selectBranchReview(baseSelection, reviewSelection);
        }
      },
    ),
  );

  /**
   * Replaces the Tab's immutable selected pair and serializes it through App.
   *
   * Callers provide two complete structured selections. This is the sole path that
   * changes the current Branch Review ChangeSet identity.
   */
  function selectBranchReview(
    baseSelection: BranchSelection,
    reviewSelection: BranchSelection,
  ): void {
    setSelected({
      kind: "values",
      base: baseSelection,
      review: reviewSelection,
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
    if (baseSelection === null || reviewSelection === null) {
      return;
    }
    setBaseEdit(baseSelection);
    setReviewEdit(reviewSelection);
    selectBranchReview(baseSelection, reviewSelection);
  }

  /**
   * Returns the shared refs observer's panel-local refresh presentation.
   *
   * Every branch/remote autocomplete receives its own projection of the same state
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
      const variables = saveMainBranch.variables;
      if (variables === undefined) {
        throw new Error("Main-branch save error is missing its command input.");
      }
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
          const current = selectedBranch(base());
          if (current === null) {
            throw new Error("Saving main branch requires a base selection.");
          }
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
      <Show when={selectedValues()} keyed>
        {(selection) => (
          <ChangeSet
            active={props.active}
            params={
              {
                project_id: props.projectId,
                engine: props.engine,
                mode: "branch-review",
                base_selection: selection.base,
                review_selection: selection.review,
              } satisfies BranchReviewDiffParams
            }
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Defines the complete inputs for one structured source/branch field pair.
 *
 * The caller owns the selected BranchSelection; the component owns no duplicate
 * domain state and reports complete local or remote variants after interaction.
 */
type BranchSelectionFieldsProps = {
  sourceLabel: string;
  branchLabel: string;
  branchPlaceholder: string;
  selection: BranchSelection;
  refs: RefChoices | null;
  fieldAction: (() => JSX.Element) | null;
  panelAction: (() => JSX.Element) | null;
  onEditNotification: () => void;
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
    const remote = selection.remote;
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
   * Entering remote mode selects the first known remote or an explicit empty
   * value when metadata is unavailable; the user remains free to edit it.
   */
  function toggleSource(): void {
    if (props.selection.source === "remote") {
      props.onSelection({ source: "local", branch: props.selection.branch });
      return;
    }
    props.onSelection({
      source: "remote",
      remote: props.refs?.remotes[0] ?? "",
      branch: props.selection.branch,
    });
  }

  return (
    <>
      <AutocompleteInput
        class="branch-source-field"
        label={props.sourceLabel}
        seed={props.selection.source === "remote" ? props.selection.remote : ""}
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
          if (selection.source !== "remote") {
            throw new Error("Remote completion requires a remote selection.");
          }
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
    onSelected: (
      pullRequestUrl: string,
      base: BranchSelection,
      review: BranchSelection,
    ) => void;
    onPrepared: (prepared: PreparedPullRequest) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  let pullRequestInput!: HTMLInputElement;
  const initialUrl = props.active ? (search.get("pull_request_url") ?? "") : "";
  const [url, setUrl] = createSignal(initialUrl);
  const initialBase = props.active ? branchSelectionFromUrl("base") : null;
  const initialReview = props.active ? branchSelectionFromUrl("review") : null;
  const [selected] = createSignal<SelectedPullRequest | null>(
    props.active && initialBase !== null && initialReview !== null
      ? {
          pullRequestUrl: initialUrl,
          base: initialBase,
          review: initialReview,
        }
      : null,
  );
  if (selected() !== null && initialUrl.length === 0) {
    throw new Error("A selected pull request requires pull_request_url.");
  }
  if (selected() !== null && props.repoId === null) {
    throw new Error("A selected pull request requires repo_id.");
  }
  const prepare = createMutation(() => ({
    ...api.pullRequest.prepare(),
    onSuccess(prepared: PreparedPullRequest) {
      props.onPrepared(prepared);
    },
  }));

  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null) {
        props.onSelected(current.pullRequestUrl, current.base, current.review);
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
          <Show when={props.repoId} keyed>
            {(projectId) => (
              <ChangeSet
                active={props.active}
                params={
                  {
                    project_id: projectId,
                    engine: props.engine,
                    mode: "branch-review",
                    base_selection: selection.base,
                    review_selection: selection.review,
                  } satisfies BranchReviewDiffParams
                }
                view={props.view}
                appHeaderOutlets={props.appHeaderOutlets}
              />
            )}
          </Show>
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
    onSelected: (presetType: PresetType, preset: string) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const initialType = props.active ? search.get("preset_type") : null;
  if (
    initialType !== null &&
    initialType !== "diff" &&
    initialType !== "fold" &&
    initialType !== "gumtree" &&
    initialType !== "scroll"
  ) {
    throw new Error(`Unsupported URL preset_type: ${initialType}.`);
  }
  const [presetType, setPresetType] = createSignal<PresetType>(
    initialType === "fold" ||
      initialType === "gumtree" ||
      initialType === "scroll"
      ? initialType
      : "diff",
  );
  const initialPreset = props.active ? search.get("preset_subset") : null;
  if (initialPreset !== null && initialPreset.length === 0) {
    throw new Error("preset_subset must not be empty.");
  }
  const [selected, setSelected] = createSignal<PresetSelected>(
    props.active
      ? initialPreset === null
        ? { kind: "waiting-default", presetType: presetType() }
        : { kind: "value", presetType: presetType(), preset: initialPreset }
      : null,
  );
  const [highlightedPreset, setHighlightedPreset] = createSignal<string | null>(
    initialPreset,
  );
  const catalogs = createQuery(() => ({
    ...api.presets.catalogs(),
    enabled: props.active,
  }));
  const effectivePreset = createMemo(() => {
    const highlighted = highlightedPreset();
    if (highlighted !== null && highlighted.length > 0) {
      return highlighted;
    }
    return catalogs.data?.[presetType()].default_preset ?? null;
  });
  const selectedValue = createMemo(() => {
    const current = selected();
    if (current === null || current.kind !== "value") {
      return null;
    }
    return current;
  });

  onTabReactivated(
    () => props.active,
    () => {
      const current = selected();
      if (current !== null && current.kind === "value") {
        props.onSelected(current.presetType, current.preset);
      } else {
        const preset = effectivePreset();
        if (preset === null) {
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
   * The effect observes active state, the tagged selection, current preset kind,
   * and effective catalog default. Once a concrete preset appears it performs the
   * external URL/selection command and changes the tag to `value`, making itself
   * inert. It owns no external subscription and is disposed with the eternal Tab.
   */
  createEffect(
    on(
      [() => props.active, selected, presetType, effectivePreset] as const,
      ([active, current, type, preset]) => {
        if (
          !active ||
          current === null ||
          current.kind !== "waiting-default" ||
          current.presetType !== type
        ) {
          return;
        }
        if (preset !== null) {
          selectPreset(preset);
        }
      },
    ),
  );

  /**
   * Selects and serializes one concrete preset subset for the current kind.
   *
   * The value must come from the visible validated catalog; no missing/default
   * placeholder is stored as user selection.
   */
  function selectPreset(preset: string): void {
    const type = presetType();
    setHighlightedPreset(preset);
    setSelected({ kind: "value", presetType: type, preset });
    props.onSelected(type, preset);
  }

  return (
    <>
      <form
        class="tab-panel"
        hidden={!props.active}
        onSubmit={(event) => {
          event.preventDefault();
          const preset = effectivePreset();
          if (preset !== null) {
            selectPreset(preset);
          }
        }}
      >
        <fieldset class="mode-tabs preset-tabs preset-kind-tabs">
          <legend>Preset type</legend>
          <For each={presetTypes}>
            {(kind) => (
              <button
                type="button"
                classList={{ "is-active": presetType() === kind }}
                aria-pressed={presetType() === kind}
                onClick={() => {
                  setPresetType(kind);
                  setHighlightedPreset(null);
                  setSelected({ kind: "waiting-default", presetType: kind });
                }}
              >
                {presetLabels[kind]}
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
        <Show when={catalogs.data?.[presetType()]} keyed>
          {(catalog) => (
            <fieldset class="mode-tabs preset-tabs preset-subset-tabs">
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
        <button class="load-button" type="submit">
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
            params={
              {
                project_id: selection.presetType,
                engine: props.engine,
                mode: "preset",
                preset_subset: selection.preset,
              } satisfies PresetDiffParams
            }
            view={props.view}
            appHeaderOutlets={props.appHeaderOutlets}
          />
        )}
      </Show>
    </>
  );
}

/**
 * Projects one active owner’s compact metadata state into AppHeader.
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
  const removeRepo = createMutation(() => ({
    ...api.repos.remove(),
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
      <Show when={repos.error} keyed>
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
            const projectId = removeRepo.variables;
            if (projectId === undefined) {
              throw new Error(
                "Repository removal error is missing its project ID.",
              );
            }
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
          const projectId = removeRepo.variables;
          if (projectId === undefined) {
            throw new Error(
              "Repository removal retry is missing its project ID.",
            );
          }
          removeRepo.mutate(projectId);
        }}
      />
      <Show when={repos.isPending}>
        <p class="repo-picker-loading">Loading marked repos...</p>
      </Show>
      <Show when={repos.data} keyed>
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
