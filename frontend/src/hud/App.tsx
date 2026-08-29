/**
 * Holds application identity and reconstructable workspace state.
 *
 * `App` keeps the selected Profile alive across workspace resets. Its keyed
 * Workspace holds the URL-backed Tab, repository, engine, view, FileTree, and
 * DebugHud choices and writes a complete browser URL before reconstruction.
 * Eternal Tabs retain their own completed selections below this boundary.
 *
 * Backend entities stay in TanStack Query, and ChangeSet-local expansion, Help,
 * and History state stay with the ChangeSet that uses them.
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
import { assert, expect } from "../utils";
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
  | {
      /**
       * Marks genuine absence of a globally selected repository.
       *
       * No project identity may be read from this arm or substituted from a Tab.
       */
      state: "missing";
    }
  | {
      /**
       * Marks a globally selected backend repository identity.
       *
       * Repository metadata remains in TanStack data rather than this state arm.
       */
      state: "selected";
      /**
       * Positive repository identity shared by repository-backed Tabs.
       *
       * The value is serialized as browser `repo_id`; API definitions map it to
       * `project_id` only when constructing backend parameters.
       */
      projectId: ProjectId;
    };

/**
 * Contains the complete small client-side workspace entity.
 *
 * Every field has one explicit storage location and persistence mapping. FileTree
 * and DebugHud visibility apply to every Tab in this workspace. The record excludes
 * backend data, Tab selections, live input, ChangeSet-local state, and profile identity.
 */
type WorkspaceState = {
  /**
   * Top-level Tab currently visible and serialized in canonical URL state.
   *
   * Switching it does not destroy retained Tab selections or ChangeSets.
   */
  activeTab: TabId;
  /**
   * Genuine absence or exact globally selected repository identity.
   *
   * Repository-backed Tabs gate their selection workflows from this state while
   * backend metadata remains canonical query data.
   */
  repo: RepoSelection;
  /**
   * Backend diff implementation used for file rendering across every Tab.
   *
   * It is URL-backed but remains separate from manifest and Room identity.
   */
  engine: DiffEngine;
  /**
   * Shared split or inline text presentation for every mounted ChangeSet.
   *
   * The value changes only client rendering and never backend query parameters.
   */
  view: DiffViewMode;
  /**
   * Whether the ChangeSet FileTree is visible across all Tabs in this workspace.
   *
   * Individual File expansion remains in each ChangeSet and is not represented here.
   */
  fileTreeOpen: boolean;
  /**
   * Whether the mounted ChangeSet exposes its diagnostic HUD.
   *
   * The setting is workspace presentation state and creates no backend behavior.
   */
  debugHudOpen: boolean;
};

/**
 * Defines the required inputs of one reconstructable Workspace.
 *
 * Profile identity survives workspace reset, while `onReset` replaces browser URL
 * state and destroys this complete mounted subtree without replacing providers.
 */
type WorkspaceProps = {
  /**
   * Confirmed browser Profile used for review authorship, or genuine absence.
   *
   * The value outlives workspace reconstruction and never contains preferences.
   */
  selectedProfile: StoredProfile | null;
  /**
   * Accepts one Profile after login, registration, rename, or explicit selection.
   *
   * `profile` is the complete confirmed identity. The callback updates the
   * application-lifetime selection and persists it; Workspace must continue to
   * receive that accepted value through `selectedProfile` after reactive update.
   */
  onProfileSelected: (profile: StoredProfile) => void;
  /**
   * Clears the confirmed Profile after the user explicitly logs out or forgets it.
   *
   * The callback receives no inferred replacement. After it returns, Workspace
   * receives `null` through `selectedProfile` while its repository and Tabs stay
   * mounted.
   */
  onProfileForgotten: () => void;
  /**
   * Replaces canonical URL state and reconstructs the complete Workspace subtree.
   *
   * `search` contains every global value and Tab selection that should survive.
   * The callback writes it before changing the keyed identity; providers and
   * application-lifetime Profile state remain mounted around the replacement.
   */
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
  assert(
    Number.isInteger(projectId) && projectId > 0,
    `repo_id must be a positive integer, received ${raw}.`,
  );
  return { state: "selected", projectId };
}

/**
 * Parses the workspace engine from canonical browser state.
 *
 * A genuinely empty URL selects Tokendiff. Populated URLs must name a supported
 * engine rather than silently requesting a different backend engine.
 */
function initialEngine(search: URLSearchParams): DiffEngine {
  const engine = search.get("engine");
  if (engine === null) {
    assert(search.size === 0, "A nonempty workspace URL requires engine.");
    return "tokendiff";
  }
  assert(
    engine === "dirdiff" ||
      engine === "git" ||
      engine === "difftastic" ||
      engine === "gumtree" ||
      engine === "tokendiff",
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
 *
 * @param search Canonical browser parameters mutated in place.
 * @param prefix Base or review namespace receiving the selection fields.
 * @param selection Complete local or remote branch value to serialize.
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
 *
 * @param repoId Selected repository to retain, or `null` for genuine absence.
 * @param tab Top-level Tab to activate in the reconstructed workspace.
 * @param engine File renderer to retain independently of Tab selection.
 * @param view Client-only text presentation to retain.
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
    return expect(
      changeSetStatusTarget(),
      "The AppHeader ChangeSet status outlet is not mounted.",
    );
  }

  /**
   * Returns the mounted AppHeader summary outlet for active ChangeSet content.
   *
   * Calling this before AppHeader registration is an application-order error;
   * consumers must otherwise receive a concrete physical Portal mount.
   */
  function summaryOutlet(): HTMLDivElement {
    return expect(
      changeSetSummaryTarget(),
      "The AppHeader ChangeSet summary outlet is not mounted.",
    );
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
   * tracking it. TanStack handles request deduplication, freshness, cancellation, and
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
   *
   * # Returns
   *
   * - `ProjectId`: The selected repository identity.
   * - `null`: No repository is selected. Callers pass the absence through to
   *   repo-independent Tabs and keep repository-required Tabs at their gate.
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
   *
   * @param left Confirmed old-side Git ref.
   * @param right Confirmed new-side Git ref.
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
   *
   * @param base Complete selected base-side branch.
   * @param review Complete selected review-side branch.
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
   *
   * @param presetType Validated catalog identity selected as preset kind.
   * @param preset Exact selected group identity within that catalog.
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
