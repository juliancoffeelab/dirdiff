import { For, Show } from "solid-js";
import type { DiffEngine, RepoId, RepoMark, Summary } from "./api";
import type { DiffViewMode } from "./DiffGrid";
import { diffViewLabels, engineLabels } from "./model";

export function Header(props: {
  repos: RepoMark[] | null;
  selectedRepoId: RepoId | null;
  engine: DiffEngine;
  viewMode: DiffViewMode;
  summary: Summary;
  onHeaderMount: (element: HTMLElement) => void;
  onRepoChange: (repo: RepoMark) => void;
  onEngineChange: (engine: DiffEngine) => void;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <header ref={props.onHeaderMount} class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <h1>dirdiff</h1>
          <Show when={props.repos !== null}>
            <RepoSelect
              repos={loadedRepos(props.repos)}
              selectedRepoId={props.selectedRepoId}
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
    <label class="engine-select">
      <span>Engine</span>
      <select
        value={props.engine}
        onChange={(event) => {
          const nextEngine = event.currentTarget.value as DiffEngine;
          if (
            nextEngine === "dirdiff" ||
            nextEngine === "git" ||
            nextEngine === "difftastic"
          ) {
            props.onEngineChange(nextEngine);
            event.currentTarget.blur();
            return;
          }
          throw new Error(`Unsupported diff engine: ${nextEngine}.`);
        }}
      >
        <option value="dirdiff">{engineLabels.dirdiff}</option>
        <option value="git">{engineLabels.git}</option>
        <option value="difftastic">{engineLabels.difftastic}</option>
      </select>
    </label>
  );
}

function RepoSelect(props: {
  repos: RepoMark[];
  selectedRepoId: RepoId | null;
  onRepoChange: (repo: RepoMark) => void;
}) {
  const handleRepoChange = (select: HTMLSelectElement) => {
    const nextRepoId = Number(select.value);
    if (!Number.isInteger(nextRepoId)) {
      throw new Error(`Invalid repo id selected: ${select.value}.`);
    }
    const repo = props.repos.find((candidate) => candidate.id === nextRepoId);
    if (repo === undefined) {
      throw new Error(`Unknown repo id selected: ${nextRepoId}.`);
    }
    if (repo.id === props.selectedRepoId) {
      select.blur();
      return;
    }
    props.onRepoChange(repo);
    select.blur();
  };

  return (
    <label class="engine-select repo-select">
      <span>Repo</span>
      <select
        aria-label="Repo"
        value={
          props.selectedRepoId === null ? "" : String(props.selectedRepoId)
        }
        onChange={(event) => handleRepoChange(event.currentTarget)}
      >
        <option value="" disabled>
          Choose repo
        </option>
        <For each={props.repos}>
          {(repo) => <option value={repo.id}>{repo.name}</option>}
        </For>
      </select>
    </label>
  );
}

function DiffViewSelect(props: {
  viewMode: DiffViewMode;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <label class="engine-select">
      <span>View</span>
      <select
        value={props.viewMode}
        onChange={(event) => {
          const nextViewMode = event.currentTarget.value;
          if (nextViewMode !== "split" && nextViewMode !== "inline") {
            throw new Error(`Unsupported diff view mode: ${nextViewMode}.`);
          }
          props.onViewModeChange(nextViewMode);
          event.currentTarget.blur();
        }}
      >
        <option value="split">{diffViewLabels.split}</option>
        <option value="inline">{diffViewLabels.inline}</option>
      </select>
    </label>
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
}) {
  const metricClass = () => props.label.toLowerCase();

  return (
    <div class={`summary-group summary-group-${metricClass()}`}>
      <strong>{props.label}</strong>
      <span class="delta added">+ {props.added}</span>
      <span class="delta changed">~ {props.changed}</span>
      <span class="delta removed">- {props.removed}</span>
    </div>
  );
}
