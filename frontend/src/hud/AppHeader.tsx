import {
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
  GitWordBoundaryModeSchema,
  GumTreeMatcherSchema,
  type ProjectId,
  type RepoMark,
} from "../api/api";
import { Checkbox } from "../comp/Checkbox";
import { Select, type SelectOption } from "../comp/Select";
import { ErrorPopover } from "../comp/Toasts";
import { assert, expect } from "../utils";
import { Profile, type StoredProfile } from "./Profile";
import type { DiffViewMode } from "./App";

const engineLabels: Record<DiffEngine, string> = {
  git: "Git",
  difftastic: "Difftastic",
  gumtree: "GumTree",
  tokendiff: "Tokendiff",
  dirdiff: "Dirdiff",
  diffsitter: "Diffsitter",
};

const gitAlgorithms: readonly SelectOption[] = [
  {
    value: "myers",
    label: "Myers",
    description: "Standard greedy diff. Git's default.",
  },
  {
    value: "minimal",
    label: "Minimal",
    description: "Spend more time finding the smallest diff.",
  },
  {
    value: "patience",
    label: "Patience",
    description: "Match lines with the patience algorithm.",
  },
  {
    value: "histogram",
    label: "Histogram",
    description: "Extend patience using low-occurrence common lines.",
  },
];

const gitColorMovedModes: readonly SelectOption[] = [
  { value: "no", label: "no", description: "Do not highlight moved code." },
  {
    value: "plain",
    label: "plain",
    description: "Highlight individual removed and added lines that match.",
  },
  {
    value: "blocks",
    label: "blocks",
    description:
      "Highlight greedy moved blocks with at least 20 alphanumeric characters.",
  },
  {
    value: "zebra",
    label: "zebra",
    description: "Alternate colors between adjacent detected blocks.",
  },
  {
    value: "dimmed-zebra",
    label: "dimmed-zebra",
    description: "Alternate blocks, dim interiors and emphasize boundaries.",
  },
  {
    value: "default",
    label: "default",
    description: "Use Git's default mode, currently zebra.",
  },
];

const gitColorMovedWsModes: readonly SelectOption[] = [
  { value: "no", label: "no", description: "Require exact whitespace." },
  {
    value: "ignore-space-at-eol",
    label: "ignore-space-at-eol",
    description: "Ignore trailing whitespace differences.",
  },
  {
    value: "ignore-space-change",
    label: "ignore-space-change",
    description:
      "Ignore changes in existing whitespace amount and trailing space.",
  },
  {
    value: "ignore-all-space",
    label: "ignore-all-space",
    description: "Ignore all whitespace, including newly added spaces.",
  },
  {
    value: "allow-indentation-change",
    label: "allow-indentation-change",
    description:
      "Match ignoring whitespace, then group lines with the same indentation change.",
  },
];

const gitWordBoundaryModes: readonly SelectOption[] = [
  {
    value: "default",
    label: "Default",
    description: "Every run of non-whitespace is one word.",
  },
  {
    value: "language",
    label: "Language",
    description:
      "Git's built-in diff driver for the file suffix, or Default without one.",
  },
  {
    value: "custom",
    label: "Custom",
    description:
      "The POSIX extended regex below (--word-diff-regex); Default until one is entered.",
  },
];

const gumtreeMatchers: readonly SelectOption[] = [
  {
    value: "gumtree-simple",
    label: "Simple",
    description:
      "Match similar code structures, then align their remaining children. GumTree's default.",
  },
  {
    value: "gumtree-classic",
    label: "Classic",
    description:
      "Original GumTree matcher. Use tree edit distance to recover unmatched nodes.",
  },
  {
    value: "gumtree-hybrid",
    label: "Hybrid",
    description:
      "Use tree edit distance for small structures and simpler matching for larger ones.",
  },
  {
    value: "gumtree-simple-stable",
    label: "Stable",
    description:
      "Like Simple, but pair similar structures only when each is the other's best match.",
  },
  {
    value: "gumtree-simple-auto",
    label: "Auto",
    description:
      "Try 25 Stable configurations in parallel and keep the fewest edits. Uses more CPU.",
  },
];

const viewLabels: Record<DiffViewMode, string> = {
  split: "Split",
  inline: "Inline",
};

type AppHeaderProps = {
  agentOnboardUrl: () => string | null;
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
  onEngineOptionsToggle: (toggle: () => void) => void;
  onMetadataStatusTarget: (element: HTMLElement) => void;
};

export type AppHeaderOutlets = {
  status: () => HTMLDivElement;
  summary: () => HTMLDivElement;
  toggleEngineOptions: () => void;
};

export type RepositoryState =
  | {
      state: "pending";
    }
  | {
      state: "failed";
      error: Error;
    }
  | {
      state: "available";
      repos: readonly RepoMark[];
    };

type RepoSelectProps = {
  repositories: RepositoryState;
  selectedRepoId: ProjectId | null;
  onOpen: () => void;
  onSelect: (projectId: ProjectId) => void;
  onRemove: (projectId: ProjectId) => void;
};

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
    onSuccess(_result, projectId: ProjectId) {
      void queryClient.invalidateQueries({
        queryKey: api.repos.list().queryKey,
      });
      props.onRepoRemoved(projectId);
    },
  }));

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
                description: null,
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
              registerToggle={props.onEngineOptionsToggle}
            />
            <Select
              class="header-engine-select view-select"
              label="View"
              valueLabel={viewLabels[props.view]}
              options={[
                { value: "split", label: viewLabels.split, description: null },
                {
                  value: "inline",
                  label: viewLabels.inline,
                  description: null,
                },
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

function RepoSelect(props: RepoSelectProps): JSX.Element {
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
          description: null,
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

type EngineOptionsProps = {
  engine: DiffEngine;
  selectedProfile: StoredProfile | null;
  registerToggle: (toggle: () => void) => void;
};

function EngineOptions(props: EngineOptionsProps): JSX.Element {
  const optionsId = createUniqueId();
  const queryClient = useQueryClient();

  const preferences = createQuery(() =>
    api.profile.preferences(props.selectedProfile?.id ?? null),
  );
  const savePreferences = createMutation(() =>
    api.profile.savePreferences(queryClient),
  );

  function hasOptions(): boolean {
    return (
      props.engine === "git" ||
      props.engine === "difftastic" ||
      props.engine === "gumtree"
    );
  }

  let trigger!: HTMLButtonElement;

  function togglePopover(): void {
    const popup = expect(
      document.getElementById(optionsId),
      "The options trigger requires its mounted popover.",
    );
    const button = trigger.getBoundingClientRect();
    popup.style.top = `${button.bottom + 4}px`;
    popup.style.left = `${button.left}px`;
    popup.togglePopover();
  }
  props.registerToggle(togglePopover);

  function busy(): boolean {
    return (
      preferences.isPending ||
      preferences.error !== null ||
      savePreferences.isPending
    );
  }

  function save(
    patch: Partial<
      Omit<Parameters<typeof savePreferences.mutate>[0], "profileId">
    >,
  ): void {
    const current = expect(
      preferences.data,
      "Changing an option requires loaded preferences.",
    );
    savePreferences.mutate({
      profileId: props.selectedProfile?.id ?? null,
      aggressiveFolds: current.aggressive_folds,
      difftasticIgnoreComments: current.difftastic_ignore_comments,
      gumtreeMatcher: current.gumtree_matcher,
      gitAlgorithm: current.git_algorithm,
      gitIgnoreSpaceChange: current.git_ignore_space_change,
      gitWordDiff: current.git_word_diff,
      gitWordBoundary: current.git_word_boundary,
      gitWordDiffRegex: current.git_word_diff_regex,
      gitColorMoved: current.git_color_moved,
      gitColorMovedWs: current.git_color_moved_ws,
      ...patch,
    });
  }

  return (
    <div class="engine-options-menu">
      <button
        ref={trigger}
        type="button"
        class="engine-options-trigger"
        aria-label={`${engineLabels[props.engine]} engine options`}
        title={`${engineLabels[props.engine]} options (o)`}
        onClick={togglePopover}
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
                  <div class="engine-options-section">
                    <Select
                      class="engine-options-select"
                      label="Algorithm"
                      valueLabel={
                        expect(
                          gitAlgorithms.find(
                            (algo) => algo.value === prefs().git_algorithm,
                          ),
                          "Loaded preferences name a known Git algorithm.",
                        ).label
                      }
                      options={gitAlgorithms}
                      selectedValue={prefs().git_algorithm}
                      disabled={busy()}
                      onOpen={null}
                      onChange={(value) =>
                        save({ gitAlgorithm: GitAlgorithmSchema.parse(value) })
                      }
                      optionAction={null}
                    />
                  </div>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <Checkbox
                      class="engine-options-checkbox"
                      label="Word diff"
                      description={
                        <>
                          Highlight changed words (
                          <code>--word-diff=porcelain</code>). Git does not emit
                          moved-line colors in this mode.
                        </>
                      }
                      checked={prefs().git_word_diff}
                      busy={busy()}
                      onToggle={(next) => save({ gitWordDiff: next })}
                    />
                    <Select
                      class="engine-options-select"
                      label="Word boundary"
                      valueLabel={prefs().git_word_boundary}
                      options={gitWordBoundaryModes}
                      selectedValue={prefs().git_word_boundary}
                      // Git consults the boundary only while splitting words.
                      // The value persists and applies once Word diff is on.
                      disabled={busy() || !prefs().git_word_diff}
                      onOpen={null}
                      onChange={(value) =>
                        save({
                          gitWordBoundary:
                            GitWordBoundaryModeSchema.parse(value),
                        })
                      }
                      optionAction={null}
                    />
                    <label class="engine-options-input">
                      <span class="ui-select-label">Regex</span>
                      {/* Uncontrolled on purpose: preferences hold the saved
                          pattern and the element holds the text being typed.
                          Enter or leaving the field saves it, an empty field
                          as null. The browser never compiles it. */}
                      <input
                        type="text"
                        spellcheck={false}
                        autocomplete="off"
                        placeholder="[^[:space:]]+"
                        value={prefs().git_word_diff_regex ?? ""}
                        disabled={
                          busy() ||
                          !prefs().git_word_diff ||
                          prefs().git_word_boundary !== "custom"
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Enter") {
                            event.preventDefault();
                            event.currentTarget.blur();
                          }
                        }}
                        onBlur={(event) => {
                          const text = event.currentTarget.value;
                          const regex = text === "" ? null : text;
                          if (regex !== prefs().git_word_diff_regex) {
                            save({ gitWordDiffRegex: regex });
                          }
                        }}
                      />
                    </label>
                    <Show when={!prefs().git_word_diff}>
                      <span class="engine-options-inactive-note">
                        Word boundary is inactive while Word diff is off.
                      </span>
                    </Show>
                    <Show
                      when={
                        prefs().git_word_diff &&
                        prefs().git_word_boundary !== "custom"
                      }
                    >
                      <span class="engine-options-inactive-note">
                        Regex is inactive while Word boundary is not Custom.
                      </span>
                    </Show>
                  </div>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <Select
                      class="engine-options-select"
                      label="Moved code"
                      valueLabel={prefs().git_color_moved}
                      options={gitColorMovedModes}
                      selectedValue={prefs().git_color_moved}
                      // Git suppresses moved-line colors in word-diff mode. The
                      // value persists and applies again once Word diff is off.
                      disabled={busy() || prefs().git_word_diff}
                      onOpen={null}
                      onChange={(value) =>
                        save({
                          gitColorMoved: GitColorMovedSchema.parse(value),
                        })
                      }
                      optionAction={null}
                    />
                    <Select
                      class="engine-options-select"
                      label="Move whitespace"
                      valueLabel={prefs().git_color_moved_ws}
                      options={gitColorMovedWsModes}
                      selectedValue={prefs().git_color_moved_ws}
                      // Git ignores this flag without move detection, and word
                      // diff disables move detection. The value persists.
                      disabled={
                        busy() ||
                        prefs().git_word_diff ||
                        prefs().git_color_moved === "no"
                      }
                      onOpen={null}
                      onChange={(value) =>
                        save({
                          gitColorMovedWs: GitColorMovedWsSchema.parse(value),
                        })
                      }
                      optionAction={null}
                    />
                    <Show when={prefs().git_word_diff}>
                      <span class="engine-options-inactive-note">
                        Moved code is inactive while Word diff is on.
                      </span>
                    </Show>
                    <Show
                      when={
                        !prefs().git_word_diff &&
                        prefs().git_color_moved === "no"
                      }
                    >
                      <span class="engine-options-inactive-note">
                        Move whitespace is inactive while Moved code is no.
                      </span>
                    </Show>
                  </div>
                  <div class="engine-options-popover-divider" />
                  <div class="engine-options-section">
                    <Checkbox
                      class="engine-options-checkbox"
                      label="Ignore space changes"
                      description={
                        <>
                          Ignore amount of whitespace and trailing space
                          differences (<code>--ignore-space-change</code>).
                        </>
                      }
                      checked={prefs().git_ignore_space_change}
                      busy={busy()}
                      onToggle={(next) => save({ gitIgnoreSpaceChange: next })}
                    />
                  </div>
                </Show>
                <Show when={props.engine === "difftastic"}>
                  <div class="engine-options-section">
                    <Checkbox
                      class="engine-options-checkbox"
                      label="Ignore comments"
                      description={
                        <>
                          Exclude comments from change detection (
                          <code>--ignore-comments</code>).
                        </>
                      }
                      checked={prefs().difftastic_ignore_comments}
                      busy={busy()}
                      onToggle={(next) =>
                        save({ difftasticIgnoreComments: next })
                      }
                    />
                  </div>
                </Show>
                <Show when={props.engine === "gumtree"}>
                  <div class="engine-options-section">
                    <Select
                      class="engine-options-select"
                      label="Matcher"
                      valueLabel={
                        expect(
                          gumtreeMatchers.find(
                            (matcher) =>
                              matcher.value === prefs().gumtree_matcher,
                          ),
                          "Loaded preferences name a known GumTree matcher.",
                        ).label
                      }
                      options={gumtreeMatchers}
                      selectedValue={prefs().gumtree_matcher}
                      disabled={busy()}
                      onOpen={null}
                      onChange={(value) =>
                        save({
                          gumtreeMatcher: GumTreeMatcherSchema.parse(value),
                        })
                      }
                      optionAction={null}
                    />
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
