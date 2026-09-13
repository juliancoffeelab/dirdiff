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
import {
  For,
  Show,
  createMemo,
  createSignal,
  createUniqueId,
  type JSX,
} from "solid-js";
import {
  createMutation,
  createQuery,
  useQueryClient,
} from "@tanstack/solid-query";
import { SlidersHorizontal, Trash2 } from "lucide-solid";
import {
  api,
  type DiffEngine,
  DiffEngineSchema,
  GitAlgorithmSchema,
  GitColorMovedSchema,
  GitColorMovedWsSchema,
  GumTreeMatcherSchema,
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
  git: "Git",
  difftastic: "Difftastic",
  gumtree: "GumTree",
  tokendiff: "Tokendiff",
  dirdiff: "Dirdiff",
  diffsitter: "Diffsitter",
};

/** Header labels for the Git algorithm, with explanations in the choice menu. */
const gitAlgorithms = [
  {
    value: "myers",
    label: "Myers",
    explanation: "Standard greedy diff.",
  },
  {
    value: "minimal",
    label: "Minimal",
    explanation: "Spend more time finding the smallest diff.",
  },
  {
    value: "patience",
    label: "Patience",
    explanation: "Match lines with the patience algorithm.",
  },
  {
    value: "histogram",
    label: "Histogram",
    explanation: "Extend patience using low-occurrence common lines.",
  },
] as const;

/** Matcher descriptions follow CompositeMatchers and AutoMatchers in GumTree. */
const gumtreeMatchers = [
  {
    value: "gumtree-simple",
    label: "Simple",
    explanation:
      "Match similar code structures, then align their remaining children.",
  },
  {
    value: "gumtree-classic",
    label: "Classic",
    explanation:
      "Original GumTree matcher. Use tree edit distance to recover unmatched nodes.",
  },
  {
    value: "gumtree-hybrid",
    label: "Hybrid",
    explanation:
      "Use tree edit distance for small structures and simpler matching for larger ones.",
  },
  {
    value: "gumtree-simple-stable",
    label: "Stable",
    explanation:
      "Like Simple, but pair similar structures only when each is the other's best match.",
  },
  {
    value: "gumtree-simple-auto",
    label: "Auto",
    explanation:
      "Try 25 Stable configurations in parallel and keep the fewest edits. Uses more CPU.",
  },
] as const;

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
   * Returns the absolute onboarding URL for a complete supported active Tab.
   *
   * `null` means the current Tab is Preset or has no complete selected value.
   * AppHeader forwards the accessor without reading or retaining its result.
   *
   * # Returns
   *
   * - Absolute onboarding endpoint for the complete active supported Tab.
   * - `null` for Preset or an active workflow without a complete selection.
   */
  agentOnboardUrl: () => string | null;
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
              agentOnboardUrl={props.agentOnboardUrl}
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
                props.onEngineSelected(DiffEngineSchema.parse(value));
              }}
              optionAction={null}
            />
            <EngineOptions
              engine={props.engine}
              selectedProfile={props.selectedProfile}
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

/**
 * Defines the complete inputs required by EngineOptions.
 *
 * It receives the selected engine and profile identity to read and persist engine
 * preferences. The browser manages popup visibility through the native popover.
 */
type EngineOptionsProps = {
  /** Backend diff engine currently selected in AppHeader. */
  engine: DiffEngine;
  /** Confirmed Profile shown and used by preferences, or genuine absence. */
  selectedProfile: StoredProfile | null;
};

/**
 * Renders the persistent engine options trigger and popup configuration menu.
 *
 * The component observes canonical preferences for Git algorithm, whitespace,
 * word and moved-code output, Difftastic comments, and GumTree matcher choices.
 * Engines without options show an explicit empty state. Saving never copies
 * preferences into component state.
 *
 * @param props Component properties containing current engine and profile selection.
 * The native popover handles dismissal and focus; opening positions it below the trigger.
 *
 * @returns Rendered trigger button and native options popover.
 */
function EngineOptions(props: EngineOptionsProps): JSX.Element {
  const optionsId = createUniqueId();
  const queryClient = useQueryClient();

  const preferences = createQuery(() =>
    api.profile.preferences(props.selectedProfile?.id ?? null),
  );
  const savePreferences = createMutation(() =>
    api.profile.savePreferences(queryClient),
  );

  /** Reports option availability from the current engine, independent of preferences. */
  function hasOptions(): boolean {
    return (
      props.engine === "git" ||
      props.engine === "difftastic" ||
      props.engine === "gumtree"
    );
  }

  /**
   * Persists a chosen Git diff algorithm to the canonical preferences cache.
   *
   * @param value Validated algorithm choice.
   */
  function selectAlgorithm(
    value: (typeof gitAlgorithms)[number]["value"],
  ): void {
    const current = expect(
      preferences.data,
      "Changing an algorithm requires loaded preferences.",
    );
    savePreferences.mutate({
      profileId: props.selectedProfile?.id ?? null,
      aggressiveFolds: current.aggressive_folds,
      difftasticIgnoreComments: current.difftastic_ignore_comments,
      gumtreeMatcher: current.gumtree_matcher,
      gitAlgorithm: GitAlgorithmSchema.parse(value),
      gitIgnoreSpaceChange: current.git_ignore_space_change,
      gitWordDiff: current.git_word_diff,
      gitColorMoved: current.git_color_moved,
      gitColorMovedWs: current.git_color_moved_ws,
    });
  }

  /**
   * Inverts and persists Git's ignore-space-change preference.
   */
  function toggleWhitespace(): void {
    const current = expect(
      preferences.data,
      "Changing whitespace handling requires loaded preferences.",
    );
    savePreferences.mutate({
      profileId: props.selectedProfile?.id ?? null,
      aggressiveFolds: current.aggressive_folds,
      difftasticIgnoreComments: current.difftastic_ignore_comments,
      gumtreeMatcher: current.gumtree_matcher,
      gitAlgorithm: current.git_algorithm,
      gitIgnoreSpaceChange: !current.git_ignore_space_change,
      gitWordDiff: current.git_word_diff,
      gitColorMoved: current.git_color_moved,
      gitColorMovedWs: current.git_color_moved_ws,
    });
  }

  /** Inverts and persists Git's word-level decoration preference. */
  function toggleWordDiff(): void {
    const current = expect(
      preferences.data,
      "Changing word diff requires loaded preferences.",
    );
    savePreferences.mutate({
      profileId: props.selectedProfile?.id ?? null,
      aggressiveFolds: current.aggressive_folds,
      difftasticIgnoreComments: current.difftastic_ignore_comments,
      gumtreeMatcher: current.gumtree_matcher,
      gitAlgorithm: current.git_algorithm,
      gitIgnoreSpaceChange: current.git_ignore_space_change,
      gitWordDiff: !current.git_word_diff,
      gitColorMoved: current.git_color_moved,
      gitColorMovedWs: current.git_color_moved_ws,
    });
  }

  return (
    <div class="engine-options-menu">
      <button
        type="button"
        class="engine-options-trigger"
        popovertarget={optionsId}
        aria-label={`${engineLabels[props.engine]} engine options`}
        title={`${engineLabels[props.engine]} options`}
        onClick={(event) => {
          // Fix the top-layer popup in viewport coordinates before native opening.
          // CSS anchor tracking against the sticky header flickers during scrolling.
          // Related: https://bugzilla.mozilla.org/show_bug.cgi?id=1999954
          const popup = expect(
            document.getElementById(optionsId),
            "The options trigger requires its mounted popover.",
          );
          const button = event.currentTarget.getBoundingClientRect();
          popup.style.top = `${button.bottom + 4}px`;
          popup.style.left = `${button.left}px`;
        }}
      >
        <SlidersHorizontal
          class="engine-options-trigger-icon"
          aria-hidden="true"
        />
        <Show when={hasOptions()}>
          <span
            class="engine-options-badge"
            aria-label="Engine options available"
          />
        </Show>
      </button>
      <div
        popover="auto"
        id={optionsId}
        class="engine-options-popover"
        role="region"
        aria-label={`${engineLabels[props.engine]} options`}
      >
        <div class="engine-options-popover-header">
          <SlidersHorizontal
            class="engine-options-popover-icon"
            aria-hidden="true"
          />
          <div class="engine-options-popover-copy">
            <strong>{engineLabels[props.engine]}</strong>
            <span>Engine options</span>
          </div>
        </div>
        <div class="engine-options-popover-divider" />
        <Show
          when={hasOptions()}
          fallback={
            <div class="engine-options-empty-message">
              No options implemented for this engine
            </div>
          }
        >
          <Show when={preferences.isPending}>
            <div class="engine-options-message">Loading options...</div>
          </Show>
          <Show when={preferences.error} keyed>
            {(error) => (
              <div class="engine-options-error-block">
                <ErrorPopover
                  title="Failed to load preferences"
                  error={error}
                  onRetry={() => void preferences.refetch()}
                  trigger={<span>Failed to load preferences</span>}
                  triggerClass="compact-error-trigger"
                  triggerLabel="Show preferences error"
                />
              </div>
            )}
          </Show>
          <Show when={preferences.data}>
            {(prefs) => (
              <div class="engine-options-content">
                <Show when={props.engine === "git"}>
                  <For
                    each={[
                      {
                        field: "git_color_moved" as const,
                        label: "Moved-code mode",
                        options: [
                          ["no", "Do not highlight moved code."],
                          [
                            "plain",
                            "Highlight individual removed and added lines that match.",
                          ],
                          [
                            "blocks",
                            "Highlight greedy moved blocks with at least 20 alphanumeric characters.",
                          ],
                          [
                            "zebra",
                            "Alternate colors between adjacent detected blocks.",
                          ],
                          [
                            "dimmed-zebra",
                            "Alternate blocks, dim interiors and emphasize boundaries.",
                          ],
                          [
                            "default",
                            "Use Git's default mode, currently zebra.",
                          ],
                        ],
                      },
                      {
                        field: "git_color_moved_ws" as const,
                        label: "Whitespace during move detection",
                        options: [
                          ["no", "Require exact whitespace."],
                          [
                            "ignore-space-at-eol",
                            "Ignore trailing whitespace differences.",
                          ],
                          [
                            "ignore-space-change",
                            "Ignore changes in existing whitespace amount and trailing space.",
                          ],
                          [
                            "ignore-all-space",
                            "Ignore all whitespace, including newly added spaces.",
                          ],
                          [
                            "allow-indentation-change",
                            "Match ignoring whitespace, then group lines with the same indentation change.",
                          ],
                        ],
                      },
                    ]}
                  >
                    {(setting) => (
                      <div class="engine-options-section">
                        <span class="engine-options-section-label">
                          {setting.label}
                        </span>
                        <div
                          class="engine-algorithm-list"
                          role="radiogroup"
                          aria-label={setting.label}
                        >
                          <For each={setting.options}>
                            {([value, explanation]) => (
                              <label
                                class="engine-algorithm-item"
                                classList={{
                                  selected: prefs()[setting.field] === value,
                                }}
                              >
                                <input
                                  class="engine-algorithm-input"
                                  type="radio"
                                  name={`${optionsId}-${setting.field}`}
                                  value={value}
                                  checked={prefs()[setting.field] === value}
                                  disabled={
                                    setting.field === "git_color_moved_ws" &&
                                    prefs().git_color_moved === "no"
                                  }
                                  aria-disabled={savePreferences.isPending}
                                  onChange={(event) => {
                                    // Native activation has finished. Restore confirmed
                                    // selection before saving, so cancellation cannot
                                    // undo a synchronous anonymous preference update.
                                    const group = expect(
                                      event.currentTarget.closest(
                                        '[role="radiogroup"]',
                                      ),
                                      "Move options require their radio group.",
                                    );
                                    for (const input of group.querySelectorAll<HTMLInputElement>(
                                      'input[type="radio"]',
                                    )) {
                                      input.checked =
                                        input.value === prefs()[setting.field];
                                    }
                                    if (
                                      savePreferences.isPending ||
                                      prefs()[setting.field] === value
                                    ) {
                                      return;
                                    }
                                    const current = prefs();
                                    savePreferences.mutate({
                                      profileId:
                                        props.selectedProfile?.id ?? null,
                                      aggressiveFolds: current.aggressive_folds,
                                      gitAlgorithm: current.git_algorithm,
                                      gitIgnoreSpaceChange:
                                        current.git_ignore_space_change,
                                      gitWordDiff: current.git_word_diff,
                                      gitColorMoved: GitColorMovedSchema.parse(
                                        setting.field === "git_color_moved"
                                          ? value
                                          : current.git_color_moved,
                                      ),
                                      gitColorMovedWs:
                                        GitColorMovedWsSchema.parse(
                                          setting.field === "git_color_moved_ws"
                                            ? value
                                            : current.git_color_moved_ws,
                                        ),
                                      difftasticIgnoreComments:
                                        current.difftastic_ignore_comments,
                                      gumtreeMatcher: current.gumtree_matcher,
                                    });
                                  }}
                                />
                                <span class="engine-algorithm-item-header">
                                  <strong>{value}</strong>
                                </span>
                                <span class="engine-algorithm-item-desc">
                                  {explanation}
                                </span>
                              </label>
                            )}
                          </For>
                        </div>
                      </div>
                    )}
                  </For>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <span class="engine-options-section-label">Algorithm</span>
                    <div
                      class="engine-algorithm-list"
                      role="radiogroup"
                      aria-label="Git algorithm"
                    >
                      <For each={gitAlgorithms}>
                        {(algo) => (
                          <label
                            class="engine-algorithm-item"
                            classList={{
                              selected: prefs().git_algorithm === algo.value,
                            }}
                          >
                            <input
                              class="engine-algorithm-input"
                              type="radio"
                              name={`${optionsId}-algorithm`}
                              value={algo.value}
                              checked={prefs().git_algorithm === algo.value}
                              aria-disabled={
                                preferences.isPending ||
                                preferences.error !== null ||
                                savePreferences.isPending
                              }
                              onClick={(event) => {
                                // Keep selection tied to confirmed preferences. Native
                                // activation must not display an unsuccessful save.
                                event.preventDefault();
                                if (
                                  preferences.isPending ||
                                  preferences.error !== null ||
                                  savePreferences.isPending ||
                                  prefs().git_algorithm === algo.value
                                ) {
                                  return;
                                }
                                selectAlgorithm(algo.value);
                              }}
                            />
                            <span class="engine-algorithm-item-header">
                              <strong>{algo.label}</strong>
                              <Show when={algo.value === "myers"}>
                                <span class="engine-algorithm-default-badge">
                                  default
                                </span>
                              </Show>
                            </span>
                            <span class="engine-algorithm-item-desc">
                              {algo.explanation}
                            </span>
                          </label>
                        )}
                      </For>
                    </div>
                  </div>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <label class="engine-options-checkbox-row">
                      <span class="engine-options-checkbox-copy">
                        <strong>Word diff</strong>
                        <span>
                          Highlight changed words (
                          <code>--word-diff=porcelain</code>). Git does not emit
                          moved-line colors in this mode.
                        </span>
                      </span>
                      <span class="profile-checkbox-control">
                        <input
                          class="profile-checkbox-input"
                          type="checkbox"
                          checked={prefs().git_word_diff}
                          aria-disabled={
                            preferences.isPending ||
                            preferences.error !== null ||
                            savePreferences.isPending
                          }
                          onChange={(event) => {
                            // Restore the confirmed value after native activation;
                            // the successful save updates it through preferences.
                            event.currentTarget.checked = prefs().git_word_diff;
                            if (
                              preferences.isPending ||
                              preferences.error !== null ||
                              savePreferences.isPending
                            ) {
                              return;
                            }
                            toggleWordDiff();
                          }}
                        />
                        <span
                          class="visibility-indicator large profile-checkbox-indicator"
                          classList={{ visible: prefs().git_word_diff }}
                          aria-hidden="true"
                        />
                      </span>
                    </label>
                  </div>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <label class="engine-options-checkbox-row">
                      <span class="engine-options-checkbox-copy">
                        <strong>Ignore space changes</strong>
                        <span>
                          Ignore amount of whitespace and trailing space
                          differences (<code>--ignore-space-change</code>).
                        </span>
                      </span>
                      <span class="profile-checkbox-control">
                        <input
                          class="profile-checkbox-input"
                          type="checkbox"
                          checked={prefs().git_ignore_space_change}
                          aria-disabled={
                            preferences.isPending ||
                            preferences.error !== null ||
                            savePreferences.isPending
                          }
                          onClick={(event) => {
                            // The native check changes only when the save succeeds.
                            // aria-disabled preserves keyboard focus while saving.
                            event.preventDefault();
                            if (
                              preferences.isPending ||
                              preferences.error !== null ||
                              savePreferences.isPending
                            ) {
                              return;
                            }
                            toggleWhitespace();
                          }}
                        />
                        <span
                          class="visibility-indicator large profile-checkbox-indicator"
                          classList={{
                            visible: prefs().git_ignore_space_change,
                          }}
                          aria-hidden="true"
                        />
                      </span>
                    </label>
                  </div>
                </Show>
                <Show when={props.engine === "difftastic"}>
                  <div class="engine-options-section">
                    <label class="engine-options-checkbox-row">
                      <span class="engine-options-checkbox-copy">
                        <strong>Ignore comments</strong>
                        <span>
                          Exclude comments from change detection (
                          <code>--ignore-comments</code>).
                        </span>
                      </span>
                      <span class="profile-checkbox-control">
                        <input
                          class="profile-checkbox-input"
                          type="checkbox"
                          checked={prefs().difftastic_ignore_comments}
                          aria-disabled={
                            preferences.error !== null ||
                            savePreferences.isPending
                          }
                          onClick={(event) => {
                            // Only confirmed saves change the native check state.
                            event.preventDefault();
                            if (
                              preferences.error !== null ||
                              savePreferences.isPending
                            ) {
                              return;
                            }
                            const current = prefs();
                            savePreferences.mutate({
                              profileId: props.selectedProfile?.id ?? null,
                              aggressiveFolds: current.aggressive_folds,
                              gitAlgorithm: current.git_algorithm,
                              gitColorMoved: current.git_color_moved,
                              gitColorMovedWs: current.git_color_moved_ws,
                              gitIgnoreSpaceChange:
                                current.git_ignore_space_change,
                              gitWordDiff: current.git_word_diff,
                              difftasticIgnoreComments:
                                !current.difftastic_ignore_comments,
                              gumtreeMatcher: current.gumtree_matcher,
                            });
                          }}
                        />
                        <span
                          class="visibility-indicator large profile-checkbox-indicator"
                          classList={{
                            visible: prefs().difftastic_ignore_comments,
                          }}
                          aria-hidden="true"
                        />
                      </span>
                    </label>
                  </div>
                </Show>
                <Show when={props.engine === "gumtree"}>
                  <div class="engine-options-section">
                    <span class="engine-options-section-label">Matcher</span>
                    <div
                      class="engine-algorithm-list"
                      role="radiogroup"
                      aria-label="GumTree matcher"
                    >
                      <For each={gumtreeMatchers}>
                        {(matcher) => (
                          <label
                            class="engine-algorithm-item"
                            classList={{
                              selected:
                                prefs().gumtree_matcher === matcher.value,
                            }}
                          >
                            <input
                              class="engine-algorithm-input"
                              type="radio"
                              name={`${optionsId}-matcher`}
                              value={matcher.value}
                              checked={
                                prefs().gumtree_matcher === matcher.value
                              }
                              aria-disabled={
                                preferences.error !== null ||
                                savePreferences.isPending
                              }
                              onChange={(event) => {
                                // Restore the confirmed group before starting the save.
                                // Cancelling a click can undo a fast successful save.
                                const group = expect(
                                  event.currentTarget.closest(
                                    '[role="radiogroup"]',
                                  ),
                                  "Matcher radios require their group.",
                                );
                                for (const input of group.querySelectorAll<HTMLInputElement>(
                                  'input[type="radio"]',
                                )) {
                                  input.checked =
                                    input.value === prefs().gumtree_matcher;
                                }
                                if (
                                  preferences.error !== null ||
                                  savePreferences.isPending ||
                                  prefs().gumtree_matcher === matcher.value
                                ) {
                                  return;
                                }
                                const current = prefs();
                                savePreferences.mutate({
                                  profileId: props.selectedProfile?.id ?? null,
                                  aggressiveFolds: current.aggressive_folds,
                                  gitAlgorithm: current.git_algorithm,
                                  gitColorMoved: current.git_color_moved,
                                  gitColorMovedWs: current.git_color_moved_ws,
                                  gitIgnoreSpaceChange:
                                    current.git_ignore_space_change,
                                  gitWordDiff: current.git_word_diff,
                                  difftasticIgnoreComments:
                                    current.difftastic_ignore_comments,
                                  gumtreeMatcher: GumTreeMatcherSchema.parse(
                                    matcher.value,
                                  ),
                                });
                              }}
                            />
                            <span class="engine-algorithm-item-header">
                              <strong>{matcher.label}</strong>
                              <Show when={matcher.value === "gumtree-simple"}>
                                <span class="engine-algorithm-default-badge">
                                  default
                                </span>
                              </Show>
                            </span>
                            <span class="engine-algorithm-item-desc">
                              {matcher.explanation}
                            </span>
                          </label>
                        )}
                      </For>
                    </div>
                  </div>
                </Show>
                <Show
                  when={
                    savePreferences.variables?.profileId ===
                      (props.selectedProfile?.id ?? null) &&
                    savePreferences.error
                  }
                  keyed
                >
                  {(saveError) => (
                    <div class="engine-options-error-block">
                      <ErrorPopover
                        title="Failed to save preferences"
                        error={saveError}
                        onRetry={() =>
                          savePreferences.mutate(
                            expect(
                              savePreferences.variables,
                              "Retrying a save requires its original preferences.",
                            ),
                          )
                        }
                        trigger={<span>Failed to save preferences</span>}
                        triggerClass="compact-error-trigger"
                        triggerLabel="Show save error"
                      />
                    </div>
                  )}
                </Show>
              </div>
            )}
          </Show>
        </Show>
      </div>
    </div>
  );
}
