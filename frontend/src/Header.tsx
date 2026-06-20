import { Show } from "solid-js";
import type { DiffEngine, Preferences, RepoId, RepoMark, Summary } from "./api";
import type { DiffViewMode } from "./DiffGrid";
import { diffViewLabels, engineLabels } from "./fileUtils";
import { Profile } from "./Profile";
import { Select } from "./Select";

export function Header(props: {
  preferences: Preferences | null;
  preferencesPending: boolean;
  preferencesError: string | null;
  repos: RepoMark[] | null;
  selectedRepoId: RepoId | null;
  engine: DiffEngine;
  viewMode: DiffViewMode;
  summary: Summary;
  onPreferencesSaved: (preferences: Preferences) => void;
  onReloadPreferences: () => Promise<void> | void;
  onHeaderMount: (element: HTMLElement) => void;
  onRepoListOpen: () => void;
  onRepoChange: (repo: RepoMark) => void;
  onEngineChange: (engine: DiffEngine) => void;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <header ref={props.onHeaderMount} class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <div class="app-brand">
            <h1>dirdiff</h1>
            <Profile
              preferences={props.preferences}
              preferencesPending={props.preferencesPending}
              preferencesError={props.preferencesError}
              onPreferencesSaved={props.onPreferencesSaved}
              onReloadPreferences={props.onReloadPreferences}
            />
          </div>
          <Show when={props.repos !== null}>
            <RepoSelect
              repos={loadedRepos(props.repos)}
              selectedRepoId={props.selectedRepoId}
              onRepoListOpen={props.onRepoListOpen}
              onRepoChange={props.onRepoChange}
            />
          </Show>
          <div class="header-actions">
            <EngineSelect
              engine={props.engine}
              onEngineChange={props.onEngineChange}
            />
            <DiffViewSelect
              viewMode={props.viewMode}
              onViewModeChange={props.onViewModeChange}
            />
          </div>
        </div>
      </div>
      <SummaryView summary={props.summary} />
    </header>
  );
}

function EngineSelect(props: {
  engine: DiffEngine;
  onEngineChange: (engine: DiffEngine) => void;
}) {
  return (
    <Select
      class="header-engine-select"
      label="Engine"
      valueLabel={engineLabels[props.engine]}
      options={[
        { value: "dirdiff", label: engineLabels.dirdiff },
        { value: "git", label: engineLabels.git },
        { value: "difftastic", label: engineLabels.difftastic },
        { value: "gumtree", label: engineLabels.gumtree },
      ]}
      selectedValue={props.engine}
      onChange={(nextEngine) => {
        if (nextEngine === "dirdiff") {
          props.onEngineChange(nextEngine);
          return;
        }
        if (nextEngine === "git") {
          props.onEngineChange(nextEngine);
          return;
        }
        if (nextEngine === "difftastic") {
          props.onEngineChange(nextEngine);
          return;
        }
        if (nextEngine === "gumtree") {
          props.onEngineChange(nextEngine);
          return;
        }
        throw new Error(`Unsupported diff engine: ${nextEngine}.`);
      }}
    />
  );
}

function RepoSelect(props: {
  repos: RepoMark[];
  selectedRepoId: RepoId | null;
  onRepoListOpen: () => void;
  onRepoChange: (repo: RepoMark) => void;
}) {
  const handleRepoChange = (nextRepoIdRaw: string) => {
    const nextRepoId = Number(nextRepoIdRaw);
    if (!Number.isInteger(nextRepoId)) {
      throw new Error(`Invalid repo id selected: ${nextRepoIdRaw}.`);
    }
    const repo = props.repos.find((candidate) => candidate.id === nextRepoId);
    if (repo === undefined) {
      throw new Error(`Unknown repo id selected: ${nextRepoId}.`);
    }
    if (repo.id === props.selectedRepoId) {
      return;
    }
    props.onRepoChange(repo);
  };

  const selectedRepoName = () => {
    if (props.selectedRepoId === null) {
      return "Choose repo";
    }
    const repo = props.repos.find(
      (candidate) => candidate.id === props.selectedRepoId,
    );
    if (repo === undefined) {
      throw new Error(`Unknown selected repo id: ${props.selectedRepoId}.`);
    }
    return repo.name;
  };

  return (
    <Select
      class="header-engine-select repo-select"
      label="Repo"
      valueLabel={selectedRepoName()}
      options={props.repos.map((repo) => ({
        value: String(repo.id),
        label: repo.name,
      }))}
      selectedValue={
        props.selectedRepoId === null ? "" : String(props.selectedRepoId)
      }
      onOpen={props.onRepoListOpen}
      onChange={handleRepoChange}
    />
  );
}

function DiffViewSelect(props: {
  viewMode: DiffViewMode;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <Select
      class="header-engine-select view-select"
      label="View"
      valueLabel={diffViewLabels[props.viewMode]}
      options={[
        { value: "split", label: diffViewLabels.split },
        { value: "inline", label: diffViewLabels.inline },
      ]}
      selectedValue={props.viewMode}
      onChange={(nextViewMode) => {
        if (nextViewMode !== "split" && nextViewMode !== "inline") {
          throw new Error(`Unsupported diff view mode: ${nextViewMode}.`);
        }
        props.onViewModeChange(nextViewMode);
      }}
    />
  );
}

function SummaryView(props: { summary: Summary }) {
  const hasNotebookCells = () =>
    typeof props.summary.changed_cells === "number";

  return (
    <section class="summary" aria-label="Diff summary">
      <SummaryMetric
        label="Files"
        added={props.summary.added_files}
        changed={props.summary.updated_files}
        removed={props.summary.removed_files}
      />
      <SummaryMetric
        label="Lines"
        added={props.summary.added_lines}
        changed={props.summary.modified_lines}
        removed={props.summary.removed_lines}
        moved={props.summary.moved_lines}
      />
      <Show when={hasNotebookCells()}>
        <SummaryMetric
          label="Cells"
          added={summaryCellMetric(props.summary, "added_cells")}
          changed={summaryCellMetric(props.summary, "modified_cells")}
          removed={summaryCellMetric(props.summary, "removed_cells")}
        />
      </Show>
    </section>
  );
}

function loadedRepos(repos: RepoMark[] | null): RepoMark[] {
  if (repos === null) {
    throw new Error("Repo select rendered before repos loaded.");
  }
  return repos;
}

function summaryCellMetric(
  summary: Summary,
  key: "added_cells" | "modified_cells" | "removed_cells",
): number {
  const value = summary[key];
  if (typeof value !== "number") {
    throw new Error(`Summary is missing ${key}.`);
  }
  return value;
}

function SummaryMetric(props: {
  label: string;
  added: number;
  changed: number;
  removed: number;
  moved?: number;
}) {
  const metricClass = () => props.label.toLowerCase();

  return (
    <div class={`summary-group summary-group-${metricClass()}`}>
      <strong>{props.label}</strong>
      <span class="delta added">+ {props.added}</span>
      <span class="delta changed">~ {props.changed}</span>
      <span class="delta removed">- {props.removed}</span>
      <Show when={props.moved !== undefined}>
        <span class="delta moved">* {props.moved}</span>
      </Show>
    </div>
  );
}
