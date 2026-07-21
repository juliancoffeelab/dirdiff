/**
 * Defines the five eternal application Tabs and their selection workflows.
 *
 * The module exports TabId, TabStrip, and Tabs. Private controls store their local
 * workflow state, observe only canonical metadata queries, and return complete
 * selected values. Tabs combines those values with the repository and engine stored
 * by Workspace to form DiffParams. Tabs does not store global workspace state, backend
 * response copies, or ChangeSet internals.
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
import {
  ErrorPopover,
  UnexpectedErrorBoundary,
  useToasts,
} from "../comp/Toasts";
import type { DiffViewMode } from "./App";
import type { AppHeaderOutlets, RepositoryState } from "./AppHeader";
import { ChangeSet } from "./ChangeSet";
import type { StoredProfile } from "./Profile";
import { assert, expect } from "../utils";

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
 * Calls one explicit reactivation operation when an eternal Tab becomes active again.
 *
 * The effect explicitly observes only `active`, ignores the initial mount,
 * and invokes `reactivate` solely on a false-to-true transition. Solid disposes
 * the effect with the Tab; no external subscription or cleanup exists. Because
 * `on` untracks the callback, Tab selection reads and writes cannot accidentally
 * become dependencies or trigger duplicate activations.
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
 * stores no Tab lifetime or URL behavior.
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
  selectedProfile: StoredProfile | null;
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
  onToggleView: () => void;
};

/**
 * Defines shared inputs for one mounted private Tab.
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
  selectedProfile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onRepoSelected: (projectId: ProjectId) => void;
  onToggleView: () => void;
};

/**
 * Defines shared required inputs after a RepoGate has narrowed repository state.
 *
 * The project ID is concrete, so canonical query definitions never receive a
 * placeholder identity. App still supplies engine and activation.
 */
type RepoTabProps = {
  active: boolean;
  projectId: ProjectId;
  engine: DiffEngine;
  view: DiffViewMode;
  selectedProfile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onToggleView: () => void;
};

/**
 * Defines the complete presentation inputs of a metadata refresh control.
 *
 * The caller supplies current fetching and failure data together with the exact
 * refresh operation. The control stores no query state and chooses no refetch policy.
 */
type MetadataRefreshProps = {
  label: string;
  fetching: boolean;
  error: Error | null;
  onRefetch: () => void;
};

/**
 * Defines one active metadata observer's compact AppHeader presentation.
 *
 * The Tab supplies genuine pending/error state and retry behavior. This value
 * carries presentation only and never moves query data.
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
        <UnexpectedErrorBoundary title="Head Tab failed">
          <HeadTab
            active={props.active === "head"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onHeadSelected}
            onToggleView={props.onToggleView}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "refs"}>
        <UnexpectedErrorBoundary title="Refs Tab failed">
          <RefsTab
            active={props.active === "refs"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onRefsSelected}
            onToggleView={props.onToggleView}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "branch-review"}>
        <UnexpectedErrorBoundary title="Branch Review Tab failed">
          <BranchReviewTab
            active={props.active === "branch-review"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onBranchReviewSelected}
            onToggleView={props.onToggleView}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "pull-request"}>
        <UnexpectedErrorBoundary title="Pull Request Tab failed">
          <PullRequestTab
            active={props.active === "pull-request"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPullRequestSelected}
            onPrepared={props.onPullRequestPrepared}
            onToggleView={props.onToggleView}
          />
        </UnexpectedErrorBoundary>
      </div>
      <div class="tab-content" hidden={props.active !== "preset"}>
        <UnexpectedErrorBoundary title="Preset Tab failed">
          <PresetTab
            active={props.active === "preset"}
            repoId={props.repoId}
            engine={props.engine}
            view={props.view}
            selectedProfile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            metadataTarget={props.metadataTarget}
            onRepoSelected={props.onRepoSelected}
            onSelected={props.onPresetSelected}
            onToggleView={props.onToggleView}
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
 */
function branchPairFromUrl(): {
  base: BranchSelection;
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
                profile={props.selectedProfile}
                appHeaderOutlets={props.appHeaderOutlets}
                onToggleView={props.onToggleView}
              />
            )}
          </Show>
        )}
      </Show>
    </>
  );
}

/**
 * Renders the repo gate or the required-repository Refs Tab.
 *
 * The explicit null gate ensures the query-owning child never receives a missing
 * or placeholder project ID. Workspace reset replaces repository identity.
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
          selectedProfile={props.selectedProfile}
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
          onToggleView={props.onToggleView}
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
  const toast = useToasts();

  /**
   * Parses one complete initial Refs pair from the active browser URL.
   *
   * A fresh or inactive Tab starts with the canonical pair. Partial and blank URL
   * pairs are one invalid entity: the user is notified after mount and the whole
   * pair is reconstructed from defaults without rewriting browser state.
   */
  function parseInitialRefs(): { left: string; right: string } {
    if (!props.active) {
      return { left: "HEAD~1", right: "HEAD" };
    }
    const search = new URLSearchParams(window.location.search);
    const left = search.get("left");
    const right = search.get("right");
    if (left === null && right === null) {
      return { left: "HEAD~1", right: "HEAD" };
    }
    if (
      left === null ||
      left.trim().length === 0 ||
      right === null ||
      right.trim().length === 0
    ) {
      onMount(() => {
        toast.showError(
          "Could not restore refs from URL",
          new Error(
            "The URL must provide both nonblank refs. Restored HEAD~1 and HEAD for this page.",
          ),
        );
      });
      return { left: "HEAD~1", right: "HEAD" };
    }
    return { left, right };
  }

  const initial = parseInitialRefs();
  const [left, setLeft] = createSignal(initial.left);
  const [right, setRight] = createSignal(initial.right);
  const [selected, setSelected] = createSignal<{
    left: string;
    right: string;
  } | null>(props.active ? initial : null);
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
      if (current === null) {
        if (loadReady()) {
          loadRefs();
        }
      } else {
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
    if (currentLeft.trim().length === 0 || currentRight.trim().length === 0) {
      throw new Error("Loading refs requires nonblank old and new refs.");
    }
    const next = { left: currentLeft, right: currentRight };
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
            params={
              {
                project_id: props.projectId,
                engine: props.engine,
                mode: "refs",
                ...selection,
              } satisfies RefsDiffParams
            }
            view={props.view}
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
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
 * Renders the repo gate or required-repository Branch Review Tab.
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
          selectedProfile={props.selectedProfile}
          appHeaderOutlets={props.appHeaderOutlets}
          metadataTarget={props.metadataTarget}
          onSelected={props.onSelected}
          onToggleView={props.onToggleView}
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
 * activation, or contains a complete immutable selected pair.
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
 * default, or contains one complete immutable kind/subset pair.
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
 * Realtime selections and refs remain with the caller. Query-backed actions are
 * explicit nullable slots; `action` is either Load or the repository gate.
 */
type BranchReviewControlsProps = {
  active: boolean;
  base: BranchSelection;
  review: BranchSelection;
  defaultRemote: string;
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
 * Untouched inputs derive realtime defaults. Activation requests a default-backed
 * selection, while explicit Load snapshots current control values. Later edits do
 * not alter the mounted ChangeSet until another completion.
 */
function BranchReviewRepoTab(props: BranchReviewRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  let initialBranches: ReturnType<typeof branchPairFromUrl> = null;
  if (props.active) {
    try {
      initialBranches = branchPairFromUrl();
    } catch (error) {
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
  const [selected, setSelected] = createSignal<BranchReviewSelected>(
    props.active
      ? initialBranches !== null
        ? { kind: "values", ...initialBranches }
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
  const selectedValues = createMemo(() => {
    const current = selected();
    if (current === null || current.kind !== "values") {
      return null;
    }
    return current;
  });
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
   * changes the tag to `values`, and therefore makes itself inert. It stores no
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
      throw new Error(
        "Branch Review submission requires complete base and review selections.",
      );
    }
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
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
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
  sourceLabel: string;
  branchLabel: string;
  branchPlaceholder: string;
  selection: BranchSelection;
  defaultRemote: string;
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
   * Entering remote mode uses the repository default remote. When defaults provide
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
  const toast = useToasts();
  let pullRequestInput!: HTMLInputElement;
  const initialUrl = props.active ? (search.get("pull_request_url") ?? "") : "";
  const [url, setUrl] = createSignal(initialUrl);
  let initialBranches: ReturnType<typeof branchPairFromUrl> = null;
  if (props.active) {
    try {
      initialBranches = branchPairFromUrl();
    } catch (error) {
      onMount(() => {
        toast.showError("Could not restore pull request from URL", error);
      });
    }
  }
  const [selected] = createSignal<SelectedPullRequest | null>(
    props.active && initialBranches !== null
      ? {
          pullRequestUrl: initialUrl,
          ...initialBranches,
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
                profile={props.selectedProfile}
                appHeaderOutlets={props.appHeaderOutlets}
                onToggleView={props.onToggleView}
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
  const toast = useToasts();
  const initialType = props.active ? search.get("preset_type") : null;
  if (props.active && initialType === null) {
    onMount(() => {
      toast.showError(
        "Could not restore preset type from URL",
        new Error(
          "preset_type is missing. Restored Diff Presets for this page.",
        ),
      );
    });
  }
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
    if (highlighted !== null) {
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
   * inert. It creates no external subscription and is disposed with the eternal Tab.
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
            profile={props.selectedProfile}
            appHeaderOutlets={props.appHeaderOutlets}
            onToggleView={props.onToggleView}
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
    if (repos.data === undefined) {
      throw new Error(
        "A settled repository query requires data or an explicit error.",
      );
    }
    return { state: "available", repos: repos.data };
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
