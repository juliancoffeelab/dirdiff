import { For, Show } from "solid-js";
import { Trash2 } from "lucide-solid";
import {
  DiffEngineSchema,
  type DiffEngine,
  type Preferences,
  type ProjectId,
  type RepoMark,
  type Summary,
} from "./api";
import type { LoadedFilesStatus } from "./app/createDiffResources";
import type { DiffViewMode } from "./DiffGrid";
import {
  diffViewLabels,
  engineLabels,
  type LoadState,
  type RepoListStatus,
} from "./fileUtils";
import { Profile } from "./Profile";
import { Select } from "./Select";
import type { StoredProfile } from "./storage";

type LoadingNoticeId =
  | "marked-repos"
  | "preferences"
  | "presets"
  | "repo-defaults"
  | "repo-refs";

export type LoadingAppNotice = {
  id: LoadingNoticeId;
  placement: "top";
  state: "loading";
};

export type TopDiffNotice = {
  id: "diff";
  placement: "top";
  state: LoadState;
  text: string;
};

export type InlineDiffNotice = {
  id: "diff";
  placement: "inline";
  state: LoadState;
  text: string;
};

export type AppNotice = LoadingAppNotice | TopDiffNotice | InlineDiffNotice;
type TopNotice = LoadingAppNotice | TopDiffNotice;

export function Header(props: {
  onSwitchFrontend: () => void;
  storedProfile: StoredProfile | null;
  preferences: Preferences | null;
  preferencesPending: boolean;
  preferencesError: string | null;
  repos: RepoListStatus;
  selectedProjectId: ProjectId | null;
  engine: DiffEngine;
  viewMode: DiffViewMode;
  summary: Summary;
  loadedFilesStatus: LoadedFilesStatus | null;
  notices: AppNotice[];
  onProfileSaved: (profile: StoredProfile) => void;
  onProfileForgotten: () => void;
  onPreferencesSaved: (preferences: Preferences) => void;
  onReloadPreferences: () => Promise<void> | void;
  onHeaderMount: (element: HTMLElement) => void;
  onRepoListOpen: () => void;
  onRepoChange: (repo: RepoMark) => void;
  onRepoRemove: (repo: RepoMark) => void | Promise<void>;
  onEngineChange: (engine: DiffEngine) => void;
  onViewModeChange: (viewMode: DiffViewMode) => void;
}) {
  return (
    <header ref={props.onHeaderMount} class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <div class="app-brand">
            <h1>
              <button
                type="button"
                class="app-brand-switch"
                title="Switch to v_new"
                aria-label="Switch to v_new"
                onClick={props.onSwitchFrontend}
              >
                dirdiff
              </button>
            </h1>
            <Profile
              storedProfile={props.storedProfile}
              preferences={props.preferences}
              preferencesPending={props.preferencesPending}
              preferencesError={props.preferencesError}
              onProfileSaved={props.onProfileSaved}
              onProfileForgotten={props.onProfileForgotten}
              onPreferencesSaved={props.onPreferencesSaved}
              onReloadPreferences={props.onReloadPreferences}
            />
          </div>
          <RepoSelect
            repos={props.repos}
            selectedProjectId={props.selectedProjectId}
            onRepoListOpen={props.onRepoListOpen}
            onRepoChange={props.onRepoChange}
            onRepoRemove={props.onRepoRemove}
          />
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
      <SummaryView
        summary={props.summary}
        loadedFilesStatus={props.loadedFilesStatus}
        notices={props.notices}
      />
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
        const parsedEngine = DiffEngineSchema.safeParse(nextEngine);
        if (parsedEngine.success) {
          props.onEngineChange(parsedEngine.data);
          return;
        }
        throw new Error(`Unsupported diff engine: ${nextEngine}.`);
      }}
    />
  );
}

function RepoSelect(props: {
  repos: RepoListStatus;
  selectedProjectId: ProjectId | null;
  onRepoListOpen: () => void;
  onRepoChange: (repo: RepoMark) => void;
  onRepoRemove: (repo: RepoMark) => void | Promise<void>;
}) {
  const handleRepoChange = (nextProjectIdRaw: string) => {
    const nextProjectId = Number(nextProjectIdRaw);
    if (!Number.isInteger(nextProjectId)) {
      throw new Error(`Invalid project id selected: ${nextProjectIdRaw}.`);
    }
    if (props.repos.state === "missing") {
      throw new Error("Cannot change repo before marked repos are loaded.");
    }
    const repo = props.repos.repos.find(
      (candidate) => candidate.id === nextProjectId,
    );
    if (repo === undefined) {
      throw new Error(`Unknown project id selected: ${nextProjectId}.`);
    }
    if (repo.id === props.selectedProjectId) {
      return;
    }
    props.onRepoChange(repo);
  };

  const selectedRepoName = () => {
    if (props.selectedProjectId === null) {
      return "Choose repo";
    }
    if (props.repos.state === "missing") {
      return "Loading repo";
    }
    const repo = props.repos.repos.find(
      (candidate) => candidate.id === props.selectedProjectId,
    );
    if (repo === undefined) {
      throw new Error(
        `Unknown selected project id: ${props.selectedProjectId}.`,
      );
    }
    return repo.name;
  };

  const removeRepo = async (repo: RepoMark) => {
    if (!confirm(`Remove ${repo.name} from marked repositories?`)) {
      return;
    }
    await props.onRepoRemove(repo);
  };

  return (
    <Select
      class="header-engine-select repo-select"
      label="Repo"
      valueLabel={selectedRepoName()}
      options={
        props.repos.state === "loaded"
          ? props.repos.repos.map((repo) => ({
              value: String(repo.id),
              label: repo.name,
            }))
          : []
      }
      selectedValue={
        props.selectedProjectId === null ? "" : String(props.selectedProjectId)
      }
      onOpen={props.onRepoListOpen}
      onChange={handleRepoChange}
      optionAction={(option) => {
        if (props.repos.state === "missing") {
          throw new Error("Cannot remove repo before marked repos are loaded.");
        }
        const repo = props.repos.repos.find(
          (candidate) => String(candidate.id) === option.value,
        );
        if (repo === undefined) {
          throw new Error(`Unknown repo option: ${option.value}.`);
        }
        return (
          <button
            type="button"
            class="ui-select-option-action"
            title={`Remove ${repo.name}`}
            aria-label={`Remove ${repo.name}`}
            onClick={() => void removeRepo(repo)}
          >
            <Trash2 class="ui-select-option-action-icon" aria-hidden="true" />
          </button>
        );
      }}
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

function SummaryView(props: {
  summary: Summary;
  loadedFilesStatus: LoadedFilesStatus | null;
  notices: AppNotice[];
}) {
  const hasNotebookCells = () =>
    typeof props.summary.changed_cells === "number";
  const loadedStatusText = () => loadedFilesStatusText(props.loadedFilesStatus);
  const topLevelNotices = () => uniqueNotices(topNotices(props.notices));

  return (
    <section class="summary" aria-label="Diff summary">
      <Show when={loadedStatusText() !== null}>
        <div class="summary-group summary-group-loaded-files">
          <span>{loadedStatusText()}</span>
        </div>
      </Show>
      <For each={topLevelNotices()}>
        {(notice) => (
          <div class="summary-group summary-group-status">
            <span>{noticeText(notice)}</span>
          </div>
        )}
      </For>
      <SummaryMetric
        label="Files"
        added={props.summary.added_files}
        changed={props.summary.updated_files}
        removed={props.summary.removed_files}
      />
      <LineSummaryMetric
        added={props.summary.added_lines}
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

function LineSummaryMetric(props: { added: number; removed: number }) {
  return (
    <div class="summary-group summary-group-lines">
      <strong>Lines</strong>
      <span class="delta added">+ {props.added}</span>
      <span class="delta removed">- {props.removed}</span>
    </div>
  );
}

function loadedFilesStatusText(
  status: LoadedFilesStatus | null,
): string | null {
  if (status === null) {
    return null;
  }
  const fileWord = status.total === 1 ? "file" : "files";
  const failedText =
    status.failed > 0 ? `, failed details ${status.failed}` : "";
  return `loaded ${status.loaded}/${status.total} ${fileWord}${failedText}`;
}

function topNotices(notices: AppNotice[]): TopNotice[] {
  return notices.filter((notice) => notice.placement === "top") as TopNotice[];
}

function uniqueNotices(notices: TopNotice[]): TopNotice[] {
  const seen = new Set<string>();
  const unique: TopNotice[] = [];
  for (const notice of notices) {
    const text = noticeText(notice);
    if (seen.has(text)) {
      continue;
    }
    seen.add(text);
    unique.push(notice);
  }
  return unique;
}

function noticeText(notice: TopNotice | InlineDiffNotice): string {
  switch (notice.id) {
    case "diff":
      return notice.text;
    case "marked-repos":
      return "Loading marked repos...";
    case "preferences":
      return "Loading preferences...";
    case "presets":
      return "Loading presets...";
    case "repo-defaults":
      return "Loading repo defaults...";
    case "repo-refs":
      return "Loading refs...";
    default:
      return notice satisfies never;
  }
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
