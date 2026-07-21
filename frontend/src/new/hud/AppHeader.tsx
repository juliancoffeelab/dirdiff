/**
 * Defines the persistent application header and its repository selector.
 *
 * The module exports AppHeader and the repository/query presentation contract used
 * by the Header and RepoGate. AppHeader renders the brand, Profile, global repo,
 * engine and view controls, metadata status, and stable ChangeSet outlet targets.
 * It observes canonical repository data and performs repository removal commands.
 * It does not own workspace selection or ChangeSet status and summary data.
 */
import { Show, createMemo, createSignal, type JSX } from "solid-js";
import {
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { Trash2 } from "lucide-solid";
import {
  api,
  type DiffEngine,
  type ProjectId,
  type RepoMark,
} from "../api/api";
import { Select, type SelectOption } from "../comp/Select";
import { ErrorPopover } from "../comp/Toasts";
import { Profile, type StoredProfile } from "./Profile";
import type { DiffViewMode } from "./App";

const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
  difftastic: "Difftastic",
  gumtree: "GumTree",
};

const viewLabels: Record<DiffViewMode, string> = {
  split: "Split",
  inline: "Inline",
};

/**
 * Defines all workspace values and commands required by AppHeader.
 *
 * The selected repository may genuinely be absent. Every setter is explicit;
 * AppHeader cannot mutate workspace state generically or construct DiffParams.
 */
type AppHeaderProps = {
  onSwitchFrontend: () => void;
  selectedProfile: StoredProfile | null;
  selectedRepoId: ProjectId | null;
  engine: DiffEngine;
  view: DiffViewMode;
  onProfileSelected: (profile: StoredProfile) => void;
  onProfileForgotten: () => void;
  onRepoSelected: (projectId: ProjectId) => void;
  onRepoRemoved: (projectId: ProjectId) => void;
  onEngineSelected: (engine: DiffEngine) => void;
  onViewSelected: (view: DiffViewMode) => void;
  onChangeSetStatusTarget: (element: HTMLDivElement) => void;
  onChangeSetSummaryTarget: (element: HTMLDivElement) => void;
  onMetadataStatusTarget: (element: HTMLElement) => void;
};

/**
 * Exposes the two stable physical AppHeader outlets to the active ChangeSet.
 *
 * Each accessor returns a mounted element or throws when called before Header has
 * registered it. The contract carries no ChangeSet data or status setters.
 */
export type AppHeaderOutlets = {
  status: () => HTMLDivElement;
  summary: () => HTMLDivElement;
};

/**
 * Represents the complete observable state of canonical repository metadata.
 *
 * Pending and failed variants deliberately contain no repository collection, so
 * consumers cannot treat absent or retained data as current choices. Available is
 * the only variant that permits resolving, selecting, or removing repositories;
 * its array may be genuinely empty after a successful backend response.
 */
export type RepositoryState =
  | { state: "pending" }
  | { state: "failed"; error: Error }
  | { state: "available"; repos: readonly RepoMark[] };

/**
 * Defines the complete inputs for the private Header repository selector.
 *
 * Repository data remains the canonical query result. The selected numeric ID
 * and explicit selection/removal commands are supplied by App.
 */
type RepoSelectProps = {
  repositories: RepositoryState;
  selectedRepoId: ProjectId | null;
  onOpen: () => void;
  onSelect: (projectId: ProjectId) => void;
  onRemove: (projectId: ProjectId) => void;
};

/**
 * Renders the sticky global application header.
 *
 * Callers provide workspace values from App and explicit commands. Repository
 * backend data stays in TanStack Query, while stable outlet elements receive
 * active ChangeSet and metadata Portal contributions retained by Tabs and Profile.
 */
export function AppHeader(props: AppHeaderProps): JSX.Element {
  const queryClient = useQueryClient();
  const [metadataTarget, setMetadataTarget] = createSignal<HTMLElement | null>(
    null,
  );
  const repos = createQuery(() => ({ ...api.repos.list() }));
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
  const removeRepo = createMutation(() => ({
    ...api.repos.remove(),
    /**
     * Applies the successful repository-removal result to shared UI state.
     *
     * TanStack supplies the exact removed `ProjectId`. The callback starts
     * invalidation of canonical repository metadata and then notifies Workspace;
     * failures never enter this callback and remain handled by the mutation cache.
     */
    onSuccess(_result, projectId: ProjectId) {
      void queryClient.invalidateQueries({
        queryKey: api.repos.list().queryKey,
      });
      props.onRepoRemoved(projectId);
    },
  }));

  /**
   * Confirms and removes one repository after explicit user consent.
   *
   * The backend project ID is required. Cancellation leaves backend and selection
   * unchanged; success invalidates the shared list and notifies App.
   */
  function confirmRemoveRepo(projectId: ProjectId): void {
    const current = repositories();
    if (current.state !== "available") {
      throw new Error("Removing a repository requires available metadata.");
    }
    const repo = current.repos.find((candidate) => candidate.id === projectId);
    if (repo === undefined) {
      throw new Error(`Cannot remove unknown repository ${projectId}.`);
    }
    if (window.confirm(`Remove ${repo.name} from marked repositories?`)) {
      removeRepo.mutate(projectId);
    }
  }

  return (
    <header class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <div class="app-brand">
            <h1>
              <button
                type="button"
                class="app-brand-switch app-brand-switch-new"
                title="Switch to v_old"
                aria-label="Switch to v_old"
                onClick={props.onSwitchFrontend}
              >
                dirdiff
              </button>
            </h1>
            <Profile
              selected={props.selectedProfile}
              metadataTarget={metadataTarget()}
              onSelected={props.onProfileSelected}
              onForgotten={props.onProfileForgotten}
            />
          </div>
          <RepoSelect
            repositories={repositories()}
            selectedRepoId={props.selectedRepoId}
            onOpen={() => {
              // Opening warms the canonical list under TanStack freshness rules.
              void queryClient.prefetchQuery(api.repos.list());
            }}
            onSelect={props.onRepoSelected}
            onRemove={confirmRemoveRepo}
          />
          <div class="header-actions">
            <Select
              class="header-engine-select"
              label="Engine"
              valueLabel={engineLabels[props.engine]}
              options={Object.entries(engineLabels).map(([value, label]) => ({
                value,
                label,
              }))}
              selectedValue={props.engine}
              disabled={false}
              onOpen={null}
              onChange={(value) => {
                if (
                  value !== "dirdiff" &&
                  value !== "git" &&
                  value !== "difftastic" &&
                  value !== "gumtree"
                ) {
                  throw new Error(`Unsupported diff engine: ${value}.`);
                }
                props.onEngineSelected(value);
              }}
              optionAction={null}
            />
            <Select
              class="header-engine-select view-select"
              label="View"
              valueLabel={viewLabels[props.view]}
              options={[
                { value: "split", label: viewLabels.split },
                { value: "inline", label: viewLabels.inline },
              ]}
              selectedValue={props.view}
              disabled={false}
              onOpen={null}
              onChange={(value) => {
                if (value !== "split" && value !== "inline") {
                  throw new Error(`Unsupported diff view: ${value}.`);
                }
                props.onViewSelected(value);
              }}
              optionAction={null}
            />
          </div>
        </div>
      </div>
      <section class="summary" aria-label="Diff summary">
        <Show when={repositories().state === "pending"}>
          <div class="summary-group summary-group-status summary-status-marked">
            <span>Loading marked repos...</span>
          </div>
        </Show>
        <Show when={repositoryError()} keyed>
          {(error) => (
            <div class="summary-group summary-group-status summary-status-marked">
              <ErrorPopover
                title="Failed to load marked repositories"
                error={error}
                onRetry={() => void repos.refetch()}
                trigger={<span>Marked repos failed</span>}
                triggerClass="compact-error-trigger summary-error-trigger"
                triggerLabel="Show marked repository error"
              />
            </div>
          )}
        </Show>
        <Show when={removeRepo.isPending}>
          <div class="summary-group summary-group-status summary-status-repo-removal">
            <span>Removing marked repo...</span>
          </div>
        </Show>
        <Show
          when={removeRepo.error !== null && removeRepo.variables !== undefined}
        >
          <div class="summary-group summary-group-status summary-status-repo-removal">
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
              trigger={<span>Repo removal failed</span>}
              triggerClass="compact-error-trigger summary-error-trigger"
              triggerLabel="Show repository removal error"
            />
          </div>
        </Show>
        <div
          ref={props.onChangeSetStatusTarget}
          id="change-set-status"
          class="app-header-portal-target"
        />
        <div
          ref={props.onChangeSetSummaryTarget}
          id="change-set-summary"
          class="app-header-portal-target"
        />
        <div
          ref={(element) => {
            // Share this physical Portal target without moving metadata queries or state.
            setMetadataTarget(element);
            props.onMetadataStatusTarget(element);
          }}
          id="workspace-metadata-status"
          class="app-header-portal-target"
        />
      </section>
    </header>
  );
}

/**
 * Renders the repository selector using canonical query data.
 *
 * The component derives display names from complete marks and reports only exact
 * numeric IDs. It stores no repository query, selection, removal, or confirmation.
 */
function RepoSelect(props: RepoSelectProps): JSX.Element {
  /**
   * Derives the selected repository display name from available canonical data.
   *
   * Callers provide the collection from RepositoryState's available variant. A
   * genuine missing selection uses the established choice label; a selected ID
   * absent from that complete collection is a contract error.
   */
  function selectedName(repos: readonly RepoMark[]): string {
    if (props.selectedRepoId === null) {
      return "Choose repo";
    }
    const repo = repos.find(
      (candidate) => candidate.id === props.selectedRepoId,
    );
    if (repo === undefined) {
      throw new Error(`Unknown selected repository ${props.selectedRepoId}.`);
    }
    return repo.name;
  }

  /**
   * Parses one Select value against the available canonical repository collection.
   *
   * Invalid or unknown values throw. Selecting the current value produces no
   * redundant Workspace command, and unavailable query states never call this path.
   */
  function selectRepo(repos: readonly RepoMark[], value: string): void {
    const projectId = Number(value);
    if (!Number.isInteger(projectId) || projectId <= 0) {
      throw new Error(`Invalid selected repository ID: ${value}.`);
    }
    if (!repos.some((repo) => repo.id === projectId)) {
      throw new Error(`Unknown selected repository ID: ${projectId}.`);
    }
    if (projectId !== props.selectedRepoId) {
      props.onSelect(projectId);
    }
  }

  return (
    <Show
      when={
        props.repositories.state === "available"
          ? props.repositories.repos
          : null
      }
      keyed
      fallback={
        <Select
          class="header-engine-select repo-select"
          label="Repo"
          valueLabel={
            props.repositories.state === "pending"
              ? "Loading repos..."
              : "Repos failed"
          }
          options={[]}
          selectedValue=""
          disabled={true}
          onOpen={null}
          onChange={() => {
            throw new Error(
              "An unavailable repository selector cannot change.",
            );
          }}
          optionAction={null}
        />
      }
    >
      {(repos) => {
        const options: SelectOption[] = repos.map((repo) => ({
          value: String(repo.id),
          label: repo.name,
        }));
        return (
          <Select
            class="header-engine-select repo-select"
            label="Repo"
            valueLabel={selectedName(repos)}
            options={options}
            selectedValue={
              props.selectedRepoId === null ? "" : String(props.selectedRepoId)
            }
            disabled={false}
            onOpen={props.onOpen}
            onChange={(value) => selectRepo(repos, value)}
            optionAction={(option) => (
              <button
                type="button"
                class="ui-select-option-action"
                title={`Remove ${option.label}`}
                aria-label={`Remove ${option.label}`}
                onClick={() => props.onRemove(Number(option.value))}
              >
                <Trash2
                  class="ui-select-option-action-icon"
                  aria-hidden="true"
                />
              </button>
            )}
          />
        );
      }}
    </Show>
  );
}
