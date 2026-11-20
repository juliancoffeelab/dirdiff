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
  PreparedPullRequestSchema,
  type RefChoices,
  type RefsDiffParams,
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

const builtinDescriptions: Record<BuiltinRef, string> = {
  HEAD: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
};

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

type TabStripProps = {
  active: TabId;
  onSelect: (tab: TabId) => void;
};

type TabsProps = {
  active: TabId;
  repoId: ProjectId | null;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
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
  onPullRequestSelected: (selection: PreparedPullRequest) => void;
  onPullRequestPrepared: (prepared: PreparedPullRequest) => void;
  onToggleView: () => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
};

type TabProps = {
  active: boolean;
  repoId: ProjectId | null;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  selectedProfile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onRepoSelected: (projectId: ProjectId) => void;
  onToggleView: () => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
};

type RepoTabProps = {
  active: boolean;
  projectId: ProjectId;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
  selectedProfile: StoredProfile | null;
  appHeaderOutlets: AppHeaderOutlets;
  metadataTarget: HTMLElement | null;
  onToggleView: () => void;
  onFileTreeOpenChange: (open: boolean) => void;
  onDebugHudOpenChange: (open: boolean) => void;
};

type MetadataRefreshProps = {
  label: string;
  fetching: boolean;
  error: Error | null;
  onRefetch: () => void;
};

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

type RepoGateProps = {
  active: boolean;
  metadataTarget: HTMLElement | null;
  onSelect: (projectId: ProjectId) => void;
};

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

function HeadTab(
  props: TabProps & {
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
        const [selected, setSelected] = createSignal<HeadDiffParams | null>(
          props.active
            ? {
                project_id: projectId,
                tab: "head",
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

function RefsTab(
  props: TabProps & {
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

type RefsRepoTabProps = RepoTabProps & {
  onSelected: (left: string, right: string) => void;
};

function RefsRepoTab(props: RefsRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();

  function parseInitialRefs(): RefsDiffParams {
    if (!props.active) {
      return {
        project_id: props.projectId,
        tab: "refs",
        show_untracked: false,
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
        show_untracked: false,
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
      // Report only after the active Tab has mounted.
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
        show_untracked: false,
        left: "HEAD~1",
        right: "HEAD",
      };
    }
    return {
      project_id: props.projectId,
      tab: "refs",
      show_untracked: false,
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
      show_untracked: false,
      left: currentLeft,
      right: currentRight,
    };
    setSelected(next);
    props.onSelected(next.left, next.right);
  }

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
          // Editing warms canonical refs; TanStack freshness avoids request
          // spam.
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

type BranchReviewRepoTabProps = RepoTabProps & {
  onSelected: (base: BranchSelection, review: BranchSelection) => void;
};

type PresetSelected =
  | {
      kind: "waiting-default";
      presetType: PresetType | null;
    }
  | PresetDiffParams
  | null;

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

function BranchReviewRepoTab(props: BranchReviewRepoTabProps): JSX.Element {
  const queryClient = useQueryClient();
  const toast = useToasts();
  let initialBranches: ReturnType<typeof branchPairFromUrl> = null;
  if (props.active) {
    try {
      initialBranches = branchPairFromUrl();
    } catch (error) {
      // Report only after the active Tab has mounted.
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
        props.onSelected(current.base, current.review);
      }
    },
  );

  function selectBranchReview(
    baseSelection: BranchSelection,
    reviewSelection: BranchSelection,
  ): void {
    setSelected({
      project_id: props.projectId,
      tab: "branch-review",
      base: baseSelection,
      review: reviewSelection,
    });
    props.onSelected(baseSelection, reviewSelection);
  }

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

function PullRequestTab(
  props: TabProps & {
    onSelected: (selection: PreparedPullRequest) => void;
    onPrepared: (prepared: PreparedPullRequest) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const toast = useToasts();
  let pullRequestInput!: HTMLInputElement;
  const initialUrl = props.active ? (search.get("pull_request_url") ?? "") : "";
  const [url, setUrl] = createSignal(initialUrl);
  // Retain capture values only in exact HTTP params. Branch names accompany
  // them for display and URL restoration, and never enter the manifest query.
  let initialSelection: {
    params: PullRequestDiffParams;
    base_branch: string;
    head_branch: string;
  } | null = null;
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
        const prepared = PreparedPullRequestSchema.parse({
          project_id: props.repoId,
          pull_request_url: initialUrl,
          left_commit: leftCommit,
          right_commit: rightCommit,
          base_branch: search.get("pr_base_branch"),
          head_branch: search.get("pr_head_branch"),
        });
        initialSelection = {
          params: {
            tab: "pull-request",
            project_id: prepared.project_id,
            url: prepared.pull_request_url,
            left_commit: prepared.left_commit,
            right_commit: prepared.right_commit,
          },
          base_branch: prepared.base_branch,
          head_branch: prepared.head_branch,
        };
      }
    } catch (error) {
      // Report only after the active Tab has mounted.
      onMount(() => {
        toast.showError("Could not restore pull request from URL", error);
      });
    }
  }
  const [selected] = createSignal<typeof initialSelection>(initialSelection);
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
        props.onSelected({
          project_id: current.params.project_id,
          pull_request_url: current.params.url,
          left_commit: current.params.left_commit,
          right_commit: current.params.right_commit,
          base_branch: current.base_branch,
          head_branch: current.head_branch,
        });
      }
    },
  );

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
        <Show when={selected()} keyed>
          {(selection) => (
            <dl class="pull-request-details" aria-label="Prepared pull request">
              <div class="summary-group">
                <dt>Base branch</dt>
                <dd title={selection.base_branch}>{selection.base_branch}</dd>
              </div>
              <div class="summary-group">
                <dt>Branch to review</dt>
                <dd title={selection.head_branch}>{selection.head_branch}</dd>
              </div>
              <div class="summary-group">
                <dt>Head</dt>
                <dd>
                  <code title={selection.params.right_commit}>
                    {selection.params.right_commit.slice(0, 10)}
                  </code>
                </dd>
              </div>
              <div class="summary-group">
                <dt>Merge base</dt>
                <dd>
                  <code title={selection.params.left_commit}>
                    {selection.params.left_commit.slice(0, 10)}
                  </code>
                </dd>
              </div>
            </dl>
          )}
        </Show>
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
          {prepare.isPending
            ? "Loading..."
            : selected()?.params.url === url().trim()
              ? "Refresh PR"
              : "Load"}
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
            params={selection.params}
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

function PresetTab(
  props: TabProps & {
    onSelected: (presetType: PresetType, preset: string) => void;
  },
): JSX.Element {
  const search = new URLSearchParams(window.location.search);
  const toast = useToasts();
  const initialType = props.active ? search.get("preset_type") : null;
  if (props.active && initialType === null) {
    // Report only after the active Tab has mounted.
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
        props.onSelected(current.catalog, current.subset);
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

  function selectPreset(preset: string): void {
    const catalog = activeCatalog();
    assert(
      catalog !== null,
      "Selecting a preset requires a listed preset catalog.",
    );
    setHighlightedPreset(preset);
    setSelected({
      catalog: catalog.id,
      tab: "preset",
      subset: preset,
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
