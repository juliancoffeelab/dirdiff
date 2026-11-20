import { Show, createSignal, onMount, type JSX } from "solid-js";
import { createStore } from "solid-js/store";
import { useQueryClient } from "@tanstack/solid-query";
import {
  api,
  type BranchSelection,
  type DiffEngine,
  DiffEngineSchema,
  type PreparedPullRequest,
  type PresetType,
  type ProjectId,
} from "../api/api";
import { assert, expect } from "../utils";
import { AppHeader, type AppHeaderOutlets } from "./AppHeader";
import { loadStoredProfile, type StoredProfile } from "./Profile";
import { TabStrip, Tabs, type TabId } from "./Tabs";

export type DiffViewMode = "split" | "inline";

type RepoSelection =
  | {
      state: "missing";
    }
  | {
      state: "selected";
      projectId: ProjectId;
    };

type WorkspaceState = {
  activeTab: TabId;
  repo: RepoSelection;
  engine: DiffEngine;
  view: DiffViewMode;
  fileTreeOpen: boolean;
  debugHudOpen: boolean;
};

type WorkspaceProps = {
  selectedProfile: StoredProfile | null;
  onProfileSelected: (profile: StoredProfile) => void;
  onProfileForgotten: () => void;
  onReset: (search: URLSearchParams) => void;
};

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

function initialEngine(search: URLSearchParams): DiffEngine {
  const engine = search.get("engine");
  if (engine === null) {
    assert(search.size === 0, "A nonempty workspace URL requires engine.");
    return "tokendiff";
  }
  return DiffEngineSchema.parse(engine);
}

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

export function App(): JSX.Element {
  const [workspaceIdentity, setWorkspaceIdentity] = createSignal<object>({});
  const [selectedProfile, setSelectedProfile] =
    createSignal<StoredProfile | null>(loadStoredProfile());

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
  // Registered once by AppHeader during setup; read by the ChangeSet `o` hotkey.
  const [engineOptionsToggle, setEngineOptionsToggle] = createSignal<
    (() => void) | null
  >(null);

  function statusOutlet(): HTMLDivElement {
    return expect(
      changeSetStatusTarget(),
      "The AppHeader ChangeSet status outlet is not mounted.",
    );
  }

  function summaryOutlet(): HTMLDivElement {
    return expect(
      changeSetSummaryTarget(),
      "The AppHeader ChangeSet summary outlet is not mounted.",
    );
  }

  const appHeaderOutlets: AppHeaderOutlets = {
    status: statusOutlet,
    summary: summaryOutlet,
    toggleEngineOptions: () =>
      expect(
        engineOptionsToggle(),
        "The AppHeader engine options toggle is not registered.",
      )(),
  };

  onMount(() => {
    if (workspace.repo.state === "selected") {
      void queryClient.prefetchQuery(api.repos.refs(workspace.repo.projectId));
      void queryClient.prefetchQuery(
        api.repos.defaults(workspace.repo.projectId),
      );
    }
  });

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

  function selectedRepoId(): ProjectId | null {
    return workspace.repo.state === "selected"
      ? workspace.repo.projectId
      : null;
  }

  function selectionSearch(tab: TabId): URLSearchParams {
    return resetSearch(selectedRepoId(), tab, workspace.engine, workspace.view);
  }

  function agentOnboardUrl(): string | null {
    const search = new URLSearchParams(window.location.search);
    const tab = initialTab(search);
    const repoId = selectedRepoId();
    if (tab === "preset" || repoId === null) {
      return null;
    }

    // One table distinguishes fields proving a complete HUD selection from the
    // smaller set the onboarding endpoint consumes.
    const fields = {
      head: { required: [], copied: [] },
      refs: { required: ["left", "right"], copied: ["left", "right"] },
      "branch-review": {
        required: [
          "base_source",
          "base_branch",
          "review_source",
          "review_branch",
        ],
        copied: [
          "base_source",
          "base_remote",
          "base_branch",
          "review_source",
          "review_remote",
          "review_branch",
        ],
      },
      "pull-request": {
        required: ["pull_request_url", "left_commit", "right_commit"],
        copied: ["pull_request_url"],
      },
    }[tab];
    if (
      fields.required.some((name) => {
        const value = search.get(name);
        return value === null || value.trim().length === 0;
      })
    ) {
      return null;
    }

    if (
      tab === "branch-review" &&
      (["base", "review"] as const).some((prefix) => {
        const source = search.get(`${prefix}_source`);
        const remote = search.get(`${prefix}_remote`);
        return (
          (source !== "local" && source !== "remote") ||
          (source === "local"
            ? remote !== null
            : remote === null || remote.trim().length === 0)
        );
      })
    ) {
      return null;
    }

    const onboardOrigin = import.meta.env.DEV
      ? expect(
          import.meta.env.VITE_DIRDIFF_BACKEND_ORIGIN,
          "Development agent onboarding requires the paired backend origin.",
        )
      : window.location.origin;
    const onboard = new URL("/api/agent/onboard", onboardOrigin);
    onboard.searchParams.set("tab", tab);
    if (tab !== "pull-request") {
      onboard.searchParams.set("project_id", String(repoId));
    }
    for (const name of fields.copied) {
      const value = search.get(name);
      if (value !== null) {
        onboard.searchParams.set(name, value);
      }
    }
    return onboard.href;
  }

  function selectTab(tab: TabId): void {
    if (tab === workspace.activeTab) {
      return;
    }
    replaceSelectionSearch(selectionSearch(tab));
    setWorkspace("activeTab", tab);
  }

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

  function selectEngine(engine: DiffEngine): void {
    setWorkspace("engine", engine);
    const search = new URLSearchParams(window.location.search);
    search.set("engine", engine);
    replaceSearch(search);
  }

  function selectView(view: DiffViewMode): void {
    setWorkspace("view", view);
    const search = new URLSearchParams(window.location.search);
    search.set("view", view);
    replaceSearch(search);
  }

  function selectHead(): void {
    const search = selectionSearch("head");
    search.set("left", "HEAD");
    search.set("right", "worktree");
    replaceSelectionSearch(search);
  }

  function selectRefs(left: string, right: string): void {
    const search = selectionSearch("refs");
    search.set("left", left);
    search.set("right", right);
    replaceSelectionSearch(search);
  }

  function selectBranchReview(
    base: BranchSelection,
    review: BranchSelection,
  ): void {
    const search = selectionSearch("branch-review");
    writeBranchSelection(search, "base", base);
    writeBranchSelection(search, "review", review);
    replaceSelectionSearch(search);
  }

  function selectPreset(presetType: PresetType, preset: string): void {
    const search = selectionSearch("preset");
    search.set("preset_type", presetType);
    search.set("preset_subset", preset);
    replaceSelectionSearch(search);
  }

  function selectPreparedPullRequest(selection: PreparedPullRequest): void {
    const search = selectionSearch("pull-request");
    search.set("pull_request_url", selection.pull_request_url);
    search.set("left_commit", selection.left_commit);
    search.set("right_commit", selection.right_commit);
    search.set("pr_base_branch", selection.base_branch);
    search.set("pr_head_branch", selection.head_branch);
    replaceSelectionSearch(search);
  }

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
    search.set("pr_base_branch", prepared.base_branch);
    search.set("pr_head_branch", prepared.head_branch);
    props.onReset(search);
  }

  return (
    <main class="app-shell">
      <AppHeader
        agentOnboardUrl={agentOnboardUrl}
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
        onEngineOptionsToggle={(toggle) => setEngineOptionsToggle(() => toggle)}
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
