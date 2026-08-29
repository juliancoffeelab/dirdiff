/**
 * Renders persistent workspace controls and ChangeSet Portal destinations.
 *
 * `AppHeader` observes the canonical repository list, performs explicit mark
 * removal, and reports validated repository, engine, view, and Profile choices
 * to the workspace. Its status, summary, and metadata elements remain mounted so
 * the active Tab and ChangeSet can render into stable physical targets.
 *
 * Workspace selection remains controlled by `App`. Portal contributors retain
 * their own data, and the header never reconstructs ChangeSet status or summary.
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
import { assert, expect } from "../utils";
import { Profile, type StoredProfile } from "./Profile";
import type { DiffViewMode } from "./App";

/**
 * Exhaustive user-visible labels for every backend diff engine.
 *
 * The Record type makes a new engine fail compilation until header selection can
 * present it; values remain presentation and never enter API parameters.
 */
const engineLabels: Record<DiffEngine, string> = {
  dirdiff: "Dirdiff",
  git: "Git",
  difftastic: "Difftastic",
  gumtree: "GumTree",
  tokendiff: "Tokendiff",
};

/**
 * Exhaustive user-visible labels for the client text presentation modes.
 *
 * Select displays these labels while reporting the exact `DiffViewMode` value.
 */
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
  /**
   * Confirmed Profile shown and used by the Profile control, or genuine absence.
   *
   * AppHeader forwards it without storing identity or preferences.
   */
  selectedProfile: StoredProfile | null;
  /**
   * Global repository identity selected by the workspace, or genuine absence.
   *
   * Its display name is always resolved from current canonical repository data.
   */
  selectedRepoId: ProjectId | null;
  /**
   * Backend file renderer currently selected across every Tab.
   *
   * AppHeader presents the exhaustive label and reports changes, but it never
   * places the engine inside manifest or Room identity.
   */
  engine: DiffEngine;
  /**
   * Client split or inline presentation currently selected across every Tab.
   *
   * It controls rendered text layout only and never changes backend parameters.
   */
  view: DiffViewMode;
  /**
   * Accepts a Profile after a confirmed Profile operation.
   *
   * `profile` is the complete identity returned by the backend. The caller stores
   * and passes it back through `selectedProfile`; AppHeader keeps no local copy.
   */
  onProfileSelected: (profile: StoredProfile) => void;
  /**
   * Clears the confirmed Profile after explicit logout.
   *
   * It runs only from Profile controls. The caller returns `null` through
   * `selectedProfile` without changing repository or Tab state.
   */
  onProfileForgotten: () => void;
  /**
   * Accepts activation of a repository available in the canonical list.
   *
   * `projectId` is parsed from the exact Select option and validated against the
   * current list. The callback does not run when the existing selection is chosen;
   * the caller updates workspace state and passes the accepted ID back.
   */
  onRepoSelected: (projectId: ProjectId) => void;
  /**
   * Reports backend-confirmed removal of one repository mark.
   *
   * `projectId` is the exact removed identity. The callback runs after mutation
   * success so the caller may clear a matching workspace selection; AppHeader
   * separately invalidates canonical repository data.
   */
  onRepoRemoved: (projectId: ProjectId) => void;
  /**
   * Accepts a validated engine option different from the current engine.
   *
   * The caller stores it and returns it through `engine`; AppHeader performs no
   * manifest or File request merely because selection changed.
   */
  onEngineSelected: (engine: DiffEngine) => void;
  /**
   * Accepts a validated presentation option different from the current view.
   *
   * The caller returns the accepted value through `view`. The callback changes no
   * backend identity or query data.
   */
  onViewSelected: (view: DiffViewMode) => void;
  /**
   * Receives the mounted stable outlet for active ChangeSet loading status.
   *
   * The ref callback runs when Solid mounts the element. The caller stores the
   * element for Portals and must not append competing content directly.
   */
  onChangeSetStatusTarget: (element: HTMLDivElement) => void;
  /**
   * Receives the mounted stable outlet for the active ChangeSet summary.
   *
   * The ref callback hands over the exact header element once mounted; active
   * ChangeSet presentation reaches it through the caller's outlet accessor.
   */
  onChangeSetSummaryTarget: (element: HTMLDivElement) => void;
  /**
   * Receives the mounted stable outlet for repository, Tab, and Profile metadata.
   *
   * The ref callback establishes the shared Portal destination. AppHeader does
   * not render domain metadata into it itself.
   */
  onMetadataStatusTarget: (element: HTMLElement) => void;
};

/**
 * Exposes the two stable physical AppHeader outlets to the active ChangeSet.
 *
 * Each accessor returns a mounted element or throws when called before Header has
 * registered it. The contract carries no ChangeSet data or status setters.
 */
export type AppHeaderOutlets = {
  /**
   * Returns the mounted header element receiving active ChangeSet status.
   *
   * Calling before AppHeader registers the ref is a lifecycle error; consumers
   * use the stable element only as a Portal target.
   */
  status: () => HTMLDivElement;
  /**
   * Returns the mounted header element receiving active ChangeSet statistics.
   *
   * Calling before registration is a lifecycle error. The accessor owns no
   * summary data and never creates an element.
   */
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
  | {
      /**
       * Marks the initial canonical repository read as unsettled.
       *
       * Consumers render a disabled selector and must not reuse retained choices.
       */
      state: "pending";
    }
  | {
      /**
       * Marks the canonical repository read as failed.
       *
       * This arm contains no choices, so selectors cannot operate on stale data.
       */
      state: "failed";
      /**
       * Exact query failure exposed for local details and explicit retry.
       *
       * Presentation must not convert it into an empty available collection.
       */
      error: Error;
    }
  | {
      /**
       * Marks the canonical repository response as successfully available.
       *
       * Only this arm permits repository resolution, selection, or removal.
       */
      state: "available";
      /**
       * Complete validated repository collection in backend order.
       *
       * The array may be empty after success and remains authoritative for
       * resolving any selected ID.
       */
      repos: readonly RepoMark[];
    };

/**
 * Defines the complete inputs for the private Header repository selector.
 *
 * Repository data remains the canonical query result. The selected numeric ID
 * and explicit selection/removal commands are supplied by App.
 */
type RepoSelectProps = {
  /**
   * Current canonical repository query state.
   *
   * The selector is interactive only for the available arm and never retains an
   * older collection across pending or failure.
   */
  repositories: RepositoryState;
  /**
   * Caller-controlled selected repository identity, or genuine absence.
   *
   * A present ID must occur in an available collection; otherwise rendering
   * throws instead of showing a substitute label.
   */
  selectedRepoId: ProjectId | null;
  /**
   * Handles a closed-to-open transition of the available repository Select.
   *
   * The callback runs after local popup state opens and may prefetch the canonical
   * list under TanStack freshness rules. It does not run for disabled states,
   * closing, or option activation.
   */
  onOpen: () => void;
  /**
   * Accepts activation of an available repository different from the current ID.
   *
   * `projectId` is parsed from the exact option and checked against the current
   * available collection. The caller stores it and returns it through
   * `selectedRepoId`; choosing the current value emits no callback.
   */
  onSelect: (projectId: ProjectId) => void;
  /**
   * Invokes the caller's removal operation for the exact option action activated.
   *
   * `projectId` comes from that option's validated value. RepoSelect neither
   * confirms nor mutates data itself; the caller may confirm and execute the
   * mutation while the ordinary Select choice remains unchanged.
   */
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
    assert(
      current.state === "available",
      "Removing a repository requires available metadata.",
    );
    const repo = expect(
      current.repos.find((candidate) => candidate.id === projectId),
      `Cannot remove unknown repository ${projectId}.`,
    );
    if (window.confirm(`Remove ${repo.name} from marked repositories?`)) {
      removeRepo.mutate(projectId);
    }
  }

  return (
    <header class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <div class="app-brand">
            <h1>dirdiff</h1>
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
                assert(
                  value === "dirdiff" ||
                    value === "git" ||
                    value === "difftastic" ||
                    value === "gumtree" ||
                    value === "tokendiff",
                  `Unsupported diff engine: ${value}.`,
                );
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
                assert(
                  value === "split" || value === "inline",
                  `Unsupported diff view: ${value}.`,
                );
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
                const projectId = expect(
                  removeRepo.variables,
                  "Repository removal error is missing its project ID.",
                );
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
    const repo = expect(
      repos.find((candidate) => candidate.id === props.selectedRepoId),
      `Unknown selected repository ${props.selectedRepoId}.`,
    );
    return repo.name;
  }

  /**
   * Parses one Select value against the available canonical repository collection.
   *
   * Invalid or unknown values throw. Selecting the current value produces no
   * redundant Workspace command, and unavailable query states never call this path.
   *
   * @param repos Complete current available repository collection.
   * @param value Exact string value reported by the activated Select option.
   */
  function selectRepo(repos: readonly RepoMark[], value: string): void {
    const projectId = Number(value);
    assert(
      Number.isInteger(projectId) && projectId > 0,
      `Invalid selected repository ID: ${value}.`,
    );
    assert(
      repos.some((repo) => repo.id === projectId),
      `Unknown selected repository ID: ${projectId}.`,
    );
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
          onChange={() =>
            assert(false, "An unavailable repository selector cannot change.")
          }
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
