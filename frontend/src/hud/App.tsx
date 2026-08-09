/**
 * Defines the top-level application shell and global workspace state.
 *
 * The module exports App and the DiffViewMode contract shared with AppHeader.
 * App stores the selected profile and workspace reset identity. Workspace stores
 * the active Tab, selected repository, engine, view, FileTree visibility, and
 * DebugHud visibility and implements URL-backed reconstruction. Neither stores
 * Tab selections, backend query data, ChangeSet-local state, or component input.
 */
import { Show, createSignal, onMount, type JSX } from "solid-js";
import { createStore } from "solid-js/store";
import { useQueryClient } from "@tanstack/solid-query";
import {
  api,
  type BranchSelection,
  type DiffEngine,
  type PreparedPullRequest,
  type PresetType,
  type ProjectId,
  type PullRequestDiffParams,
} from "../api/api";
import { assert } from "../utils";
import { AppHeader, type AppHeaderOutlets } from "./AppHeader";
import { loadStoredProfile, type StoredProfile } from "./Profile";
import { TabStrip, Tabs, type TabId } from "./Tabs";

/**
 * Selects the shared text-diff presentation used by every Tab.
 *
 * This client-only value is URL-backed and never participates in DiffParams or
 * backend query identity.
 */
export type DiffViewMode = "split" | "inline";

/**
 * Represents genuine absence or one globally selected repository.
 *
 * The selected variant stores only the numeric backend identity. Repository name,
 * path, metadata, and loading state remain canonical TanStack Query data.
 */
type RepoSelection =
  | { state: "missing" }
  | { state: "selected"; projectId: ProjectId };

/**
 * Contains the complete small client-side workspace entity.
 *
 * Every field has one explicit storage location and persistence mapping. FileTree
 * and DebugHud visibility apply to every Tab in this workspace. The record excludes
 * backend data, Tab selections, live input, ChangeSet-local state, and profile identity.
 */
type WorkspaceState = {
  activeTab: TabId;
  repo: RepoSelection;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
};

/**
 * Defines the required inputs of one reconstructable Workspace.
 *
 * Profile identity survives workspace reset, while `onReset` replaces browser URL
 * state and destroys this complete mounted subtree without replacing providers.
 */
type WorkspaceProps = {
  selectedProfile: StoredProfile | null;
  onProfileSelected: (profile: StoredProfile) => void;
  onProfileForgotten: () => void;
  onReset: (search: URLSearchParams) => void;
};

/**
 * Parses the active Tab from canonical browser URL state.
 *
 * A valid explicit `tab` selects its matching Tab, and a genuinely empty query
 * starts at Head. A populated query must identify its Tab.
 */
function initialTab(search: URLSearchParams): TabId {
  const tab = search.get("tab");
  if (tab === null) {
    assert(search.size === 0, "A nonempty workspace URL requires tab.");
    return "head";
  }
  assert(
    tab === "head" ||
      tab === "refs" ||
      tab === "branch-review" ||
      tab === "pull-request" ||
      tab === "preset",
    `Unsupported URL tab: ${tab}.`,
  );
  return tab;
}

/**
 * Parses the globally selected numeric repository from browser `repo_id`.
 *
 * Absence is a valid missing selection. Malformed, nonpositive, or noninteger
 * values throw rather than being confused with API `project_id` or a preset kind.
 */
function initialRepo(search: URLSearchParams): RepoSelection {
  const raw = search.get("repo_id");
  if (raw === null) {
    return { state: "missing" };
  }
  const projectId = Number(raw);
  if (!Number.isInteger(projectId) || projectId <= 0) {
    throw new Error(`repo_id must be a positive integer, received ${raw}.`);
  }
  return { state: "selected", projectId };
}

/**
 * Parses the workspace engine from canonical browser state.
 *
 * A genuinely empty URL selects Dirdiff. Populated URLs must name a supported
 * engine rather than silently requesting a different backend engine.
 */
function initialEngine(search: URLSearchParams): DiffEngine {
  const engine = search.get("engine");
  if (engine === null) {
    assert(search.size === 0, "A nonempty workspace URL requires engine.");
    return "dirdiff";
  }
  assert(
    engine === "dirdiff" ||
      engine === "git" ||
      engine === "difftastic" ||
      engine === "gumtree",
    `Unsupported URL diff engine: ${engine}.`,
  );
  return engine;
}

/**
 * Parses inline/split presentation from canonical browser state.
 *
 * A genuinely empty URL selects inline. Populated URLs must name a supported view;
 * absence and unsupported explicit values are visible URL contract errors.
 */
function initialView(search: URLSearchParams): DiffViewMode {
  const view = search.get("view");
  if (view === null) {
    assert(search.size === 0, "A nonempty workspace URL requires view.");
    return "inline";
  }
  assert(
    view === "inline" || view === "split",
    `Unsupported URL diff view: ${view}.`,
  );
  return view;
}

/**
 * Writes one complete branch selection into canonical browser fields.
 *
 * Local selections remove a stale remote field. Remote selections require and
 * preserve their exact remote; no backend field or parameter naming is changed.
 */
function writeBranchSelection(
  search: URLSearchParams,
  prefix: "base" | "review",
  selection: BranchSelection,
): void {
  search.set(`${prefix}_source`, selection.source);
  search.set(`${prefix}_branch`, selection.branch);
  if (selection.source === "remote") {
    search.set(`${prefix}_remote`, selection.remote);
  } else {
    search.delete(`${prefix}_remote`);
  }
}

/**
 * Creates one clean URL for a complete workspace reset.
 *
 * Only global workspace values survive. Every Tab selection and live input field
 * is removed so the reconstructed subtree cannot inherit the previous workspace.
 */
function resetSearch(
  repoId: ProjectId | null,
  tab: TabId,
  engine: DiffEngine,
  view: DiffViewMode,
): URLSearchParams {
  const search = new URLSearchParams();
  if (repoId !== null) {
    search.set("repo_id", String(repoId));
  }
  search.set("tab", tab);
  search.set("engine", engine);
  search.set("view", view);
  return search;
}

/**
 * Renders the complete visible application and stores the selected profile across workspace resets.
 *
 * Workspace reset replaces only the keyed inner subtree after writing canonical
 * URL state. QueryProvider, ToastProvider, and selected local profile remain alive.
 */
export function App(): JSX.Element {
  const [workspaceIdentity, setWorkspaceIdentity] = createSignal<object>({});
  const [selectedProfile, setSelectedProfile] =
    createSignal<StoredProfile | null>(loadStoredProfile());

  /**
   * Replaces canonical browser state and reconstructs the complete workspace.
   *
   * Callers provide every URL field to retain. This command deliberately preserves
   * providers and selected profile while destroying all workspace-local state.
   */
  function resetWorkspace(search: URLSearchParams): void {
    const query = search.toString();
    window.history.replaceState(
      null,
      "",
      query.length === 0
        ? window.location.pathname
        : `${window.location.pathname}?${query}`,
    );
    setWorkspaceIdentity({});
  }

  return (
    <Show when={workspaceIdentity()} keyed>
      {(_identity) => (
        <Workspace
          selectedProfile={selectedProfile()}
          onProfileSelected={setSelectedProfile}
          onProfileForgotten={() => setSelectedProfile(null)}
          onReset={resetWorkspace}
        />
      )}
    </Show>
  );
}

/**
 * Renders one URL-constructed workspace and stores its global workspace values.
 *
 * It exposes only explicit repo, Tab, engine, view, and workflow URL commands to
 * descendants. Repository warmups use the canonical API facade and never gate UI.
 */
function Workspace(props: WorkspaceProps): JSX.Element {
  const queryClient = useQueryClient();
  const initialSearch = new URLSearchParams(window.location.search);
  const [workspace, setWorkspace] = createStore<WorkspaceState>({
    activeTab: initialTab(initialSearch),
    repo: initialRepo(initialSearch),
    engine: initialEngine(initialSearch),
    view: initialView(initialSearch),
    fileTreeOpen: false,
    debugHudOpen: false,
  });
  const [metadataTarget, setMetadataTarget] = createSignal<HTMLElement | null>(
    null,
  );
  const [changeSetStatusTarget, setChangeSetStatusTarget] =
    createSignal<HTMLDivElement | null>(null);
  const [changeSetSummaryTarget, setChangeSetSummaryTarget] =
    createSignal<HTMLDivElement | null>(null);

  /**
   * Returns the mounted AppHeader status outlet for active ChangeSet content.
   *
   * Calling this before AppHeader registration is an application-order error;
   * consumers must otherwise receive a concrete physical Portal mount.
   */
  function statusOutlet(): HTMLDivElement {
    const target = changeSetStatusTarget();
    if (target === null) {
      throw new Error("The AppHeader ChangeSet status outlet is not mounted.");
    }
    return target;
  }

  /**
   * Returns the mounted AppHeader summary outlet for active ChangeSet content.
   *
   * Calling this before AppHeader registration is an application-order error;
   * consumers must otherwise receive a concrete physical Portal mount.
   */
  function summaryOutlet(): HTMLDivElement {
    const target = changeSetSummaryTarget();
    if (target === null) {
      throw new Error("The AppHeader ChangeSet summary outlet is not mounted.");
    }
    return target;
  }

  const appHeaderOutlets: AppHeaderOutlets = {
    status: statusOutlet,
    summary: summaryOutlet,
  };

  /**
   * Warms repository metadata once for this workspace's selected identity.
   *
   * Workspace is recreated at every repository or explicit reset boundary, so
   * `onMount` intentionally snapshots the immutable initial repository instead of
   * tracking it. TanStack owns request deduplication, freshness, cancellation, and
   * cache lifetime; this hook stores no response state and needs no local cleanup.
   */
  onMount(() => {
    if (workspace.repo.state === "selected") {
      void queryClient.prefetchQuery(api.repos.refs(workspace.repo.projectId));
      void queryClient.prefetchQuery(
        api.repos.defaults(workspace.repo.projectId),
      );
    }
  });

  /**
   * Replaces the current browser query without reconstructing this workspace.
   *
   * This is used only for ordinary selected values already stored by mounted Tabs
   * or workspace controls; reset boundaries call `props.onReset` instead.
   */
  function replaceSearch(search: URLSearchParams): void {
    const query = search.toString();
    window.history.replaceState(
      null,
      "",
      query.length === 0
        ? `${window.location.pathname}${window.location.hash}`
        : `${window.location.pathname}?${query}${window.location.hash}`,
    );
  }

  /**
   * Replaces one complete Tab selection and deliberately clears its old pin.
   *
   * Selection commands provide a clean canonical URL. A hash identifies a target
   * inside the previous ChangeSet and cannot survive selecting another result.
   */
  function replaceSelectionSearch(search: URLSearchParams): void {
    const query = search.toString();
    window.history.replaceState(
      null,
      "",
      query.length === 0
        ? window.location.pathname
        : `${window.location.pathname}?${query}`,
    );
  }

  /**
   * Returns the currently selected numeric repository or genuine absence.
   *
   * Descendants receive this narrowed value instead of the workspace union. No
   * placeholder project identity is invented for repo-independent workflows.
   */
  function selectedRepoId(): ProjectId | null {
    return workspace.repo.state === "selected"
      ? workspace.repo.projectId
      : null;
  }

  /**
   * Creates a clean canonical URL for one retained Tab selection.
   *
   * Global workspace values survive while every other Tab's selection fields are
   * removed. The owning Tab then appends only its complete selected entity.
   */
  function selectionSearch(tab: TabId): URLSearchParams {
    return resetSearch(selectedRepoId(), tab, workspace.engine, workspace.view);
  }

  /**
   * Selects one eternal Tab and records only its active identity.
   *
   * The selected Tab immediately reports its retained selection, replacing fields
   * from the previously active Tab without copying any control state.
   */
  function selectTab(tab: TabId): void {
    if (tab === workspace.activeTab) {
      return;
    }
    replaceSelectionSearch(selectionSearch(tab));
    setWorkspace("activeTab", tab);
  }

  /**
   * Selects a global repository through the complete workspace reset boundary.
   *
   * The clean URL retains only global values, so every mounted Tab, input, selected
   * result, and ChangeSet is reconstructed for the new numeric `repo_id`.
   */
  function selectRepo(projectId: ProjectId): void {
    props.onReset(
      resetSearch(
        projectId,
        workspace.activeTab,
        workspace.engine,
        workspace.view,
      ),
    );
  }

  /**
   * Repairs global selection after a repository is removed.
   *
   * Removing an unrelated mark leaves this workspace unchanged. Removing the
   * selected repository reconstructs the same Tab with an explicit missing repo.
   */
  function removeRepo(projectId: ProjectId): void {
    if (
      workspace.repo.state === "selected" &&
      workspace.repo.projectId === projectId
    ) {
      props.onReset(
        resetSearch(
          null,
          workspace.activeTab,
          workspace.engine,
          workspace.view,
        ),
      );
    }
  }

  /**
   * Selects the global diff engine and updates browser workspace state.
   *
   * Mounted Tabs and outer ChangeSets survive. Their file rendering switches to
   * the new engine without changing selected values or manifest identity.
   */
  function selectEngine(engine: DiffEngine): void {
    setWorkspace("engine", engine);
    const search = new URLSearchParams(window.location.search);
    search.set("engine", engine);
    replaceSearch(search);
  }

  /**
   * Selects the global inline/split view and updates browser workspace state.
   *
   * View never changes DiffParams, selected values, or backend query identity.
   */
  function selectView(view: DiffViewMode): void {
    setWorkspace("view", view);
    const search = new URLSearchParams(window.location.search);
    search.set("view", view);
    replaceSearch(search);
  }

  /**
   * Records the fixed Head selection in canonical browser state.
   *
   * The numeric repository remains `repo_id`; backend `project_id` exists only in
   * the HeadDiffParams constructed by HeadTab.
   */
  function selectHead(): void {
    const search = selectionSearch("head");
    search.set("left", "HEAD");
    search.set("right", "worktree");
    replaceSelectionSearch(search);
  }

  /**
   * Records one complete selected refs pair in canonical browser state.
   *
   * The values are confirmed control output rather than live autocomplete text.
   */
  function selectRefs(left: string, right: string): void {
    const search = selectionSearch("refs");
    search.set("left", left);
    search.set("right", right);
    replaceSelectionSearch(search);
  }

  /**
   * Records one complete structured Branch Review selection.
   *
   * Both variants are serialized explicitly. No defaults or remote fields are
   * inferred by App, and API naming remains confined to derived DiffParams.
   */
  function selectBranchReview(
    base: BranchSelection,
    review: BranchSelection,
  ): void {
    const search = selectionSearch("branch-review");
    writeBranchSelection(search, "base", base);
    writeBranchSelection(search, "review", review);
    replaceSelectionSearch(search);
  }

  /**
   * Records one complete Preset kind/subset while retaining global `repo_id`.
   *
   * Browser `preset_type` maps to API `project_id` only inside PresetDiffParams;
   * the two URL vocabularies remain deliberately distinct.
   */
  function selectPreset(presetType: PresetType, preset: string): void {
    const search = selectionSearch("preset");
    search.set("preset_type", presetType);
    search.set("preset_subset", preset);
    replaceSelectionSearch(search);
  }

  /**
   * Restores one already prepared PR selection when its eternal Tab is revisited.
   *
   * Preparation remains the only operation allowed to change repo or commits.
   * This command merely serializes the retained complete result into a clean URL.
   */
  function selectPreparedPullRequest(selection: PullRequestDiffParams): void {
    const search = selectionSearch("pull-request");
    search.set("pull_request_url", selection.pull_request_url);
    search.set("left_commit", selection.left_commit);
    search.set("right_commit", selection.right_commit);
    replaceSelectionSearch(search);
  }

  /**
   * Applies authoritative PR preparation through one URL-backed workspace reset.
   *
   * The returned repo and commits replace conflicting workspace state. Refs for
   * that project are invalidated before the reconstructed controls observe them.
   */
  function applyPreparedPullRequest(prepared: PreparedPullRequest): void {
    void queryClient.invalidateQueries({
      queryKey: api.repos.refs(prepared.project_id).queryKey,
    });
    const search = resetSearch(
      prepared.project_id,
      "pull-request",
      workspace.engine,
      workspace.view,
    );
    search.set("pull_request_url", prepared.pull_request_url);
    search.set("left_commit", prepared.left_commit);
    search.set("right_commit", prepared.right_commit);
    props.onReset(search);
  }

  return (
    <main class="app-shell">
      <AppHeader
        selectedProfile={props.selectedProfile}
        selectedRepoId={selectedRepoId()}
        engine={workspace.engine}
        view={workspace.view}
        onProfileSelected={props.onProfileSelected}
        onProfileForgotten={props.onProfileForgotten}
        onRepoSelected={selectRepo}
        onRepoRemoved={removeRepo}
        onEngineSelected={selectEngine}
        onViewSelected={selectView}
        onChangeSetStatusTarget={setChangeSetStatusTarget}
        onChangeSetSummaryTarget={setChangeSetSummaryTarget}
        onMetadataStatusTarget={setMetadataTarget}
      />
      <section class="controls">
        <TabStrip active={workspace.activeTab} onSelect={selectTab} />
        <Tabs
          active={workspace.activeTab}
          repoId={selectedRepoId()}
          engine={workspace.engine}
          view={workspace.view}
          fileTreeOpen={workspace.fileTreeOpen}
          debugHudOpen={workspace.debugHudOpen}
          selectedProfile={props.selectedProfile}
          appHeaderOutlets={appHeaderOutlets}
          metadataTarget={metadataTarget()}
          onRepoSelected={selectRepo}
          onHeadSelected={selectHead}
          onRefsSelected={selectRefs}
          onBranchReviewSelected={selectBranchReview}
          onPresetSelected={selectPreset}
          onPullRequestSelected={selectPreparedPullRequest}
          onPullRequestPrepared={applyPreparedPullRequest}
          onToggleView={() => {
            // Workspace changes both reactive view and canonical URL.
            selectView(workspace.view === "inline" ? "split" : "inline");
          }}
          onFileTreeOpenChange={(open) => setWorkspace("fileTreeOpen", open)}
          onDebugHudOpenChange={(open) => setWorkspace("debugHudOpen", open)}
        />
      </section>
    </main>
  );
}
