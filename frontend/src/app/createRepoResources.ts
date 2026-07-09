import { batch, createMemo, createSignal } from "solid-js";
import {
  type BranchSelection,
  type DefaultBaseSelection,
  DiffEngineSchema,
  PresetTypeSchema,
  fetchPresets,
  fetchRepoRefs,
  fetchRepos,
  saveRepoMainBranch,
  type PresetCatalogs,
  type PresetType,
  type DiffEngine,
  type RefChoices,
  type RepoId,
  type RepoMark,
  type RepoRefs,
} from "../api";
import { type ControlsState } from "../fileUtils";
import type { ControlsTab } from "../fileUtils";

function searchValue(
  search: URLSearchParams,
  name: string,
  fallback: string,
): string {
  const value = search.get(name);
  if (value !== null && value.length > 0) {
    return value;
  }
  return fallback;
}

/** Return whether a URL contains any field for one branch selection. */
function branchSelectionParamPresent(
  search: URLSearchParams,
  prefix: string,
): boolean {
  return (
    search.has(`${prefix}_source`) ||
    search.has(`${prefix}_remote`) ||
    search.has(`${prefix}_branch`)
  );
}

/** Parse split branch-selection URL params used to seed branch-review controls. */
function branchSelectionFromSearch(
  search: URLSearchParams,
  prefix: string,
  fallback: BranchSelection,
): BranchSelection {
  if (!branchSelectionParamPresent(search, prefix)) {
    return fallback;
  }
  const source = search.get(`${prefix}_source`);
  const branch = search.get(`${prefix}_branch`);
  if (source !== "local" && source !== "remote") {
    throw new Error(`${prefix}_source must be local or remote.`);
  }
  if (branch === null || branch.length === 0) {
    throw new Error(`${prefix}_branch is required.`);
  }
  if (source === "local") {
    return { source, branch };
  }
  const remote = search.get(`${prefix}_remote`);
  if (remote === null || remote.length === 0) {
    throw new Error(`${prefix}_remote is required for remote selections.`);
  }
  return { source, remote, branch };
}

/**
 * Convert /api/repo-refs default-base resolution into branch-review controls.
 * If Git cannot resolve a safe default, the base input starts empty.
 */
function baseSelectionFromDefault(
  defaultBaseSelection: DefaultBaseSelection,
): BranchSelection {
  if ("source" in defaultBaseSelection) {
    return defaultBaseSelection;
  }
  return { source: "local", branch: "" };
}

const modeSides = {
  files: ["index", "worktree"],
  staged: ["head", "index"],
  head: ["head", "worktree"],
} as const;
const defaultRefsSides = ["head~1", "head"] as const;

/** Infer mode from URL shape when mode is missing or from an old top-level tab. */
function inferMode(search: URLSearchParams, left: string, right: string) {
  if (
    branchSelectionParamPresent(search, "base") ||
    branchSelectionParamPresent(search, "review")
  ) {
    return "branch-review" as const;
  }
  return left === "head" && right === "worktree" ? "head" : "refs";
}

/** Resolve the top-level mode used by initialControls for first render. */
function resolveTopLevelMode(
  mode: ControlsState["mode"] | null,
  search: URLSearchParams,
  left: string,
  right: string,
): ControlsState["mode"] {
  if (
    mode === "refs" ||
    mode === "branch-review" ||
    mode === "head" ||
    mode === "preset"
  ) {
    return mode;
  }
  if (mode === "files" || mode === "staged") {
    return "head";
  }
  return inferMode(search, left, right);
}

/** Build first-render control state from URL params plus repo ref metadata. */
function initialControls(repoRefs: RepoRefs): ControlsState {
  const search = new URLSearchParams(window.location.search);
  const requestedLeft = searchValue(search, "left", "head");
  const requestedRight = searchValue(search, "right", "worktree");
  const baseSelection = branchSelectionFromSearch(
    search,
    "base",
    baseSelectionFromDefault(repoRefs.default_base_selection),
  );
  const reviewSelection = branchSelectionFromSearch(
    search,
    "review",
    repoRefs.preferred_review_selection,
  );
  const requestedMode = search.get("mode") as ControlsState["mode"] | null;
  const requestedPresetType = search.get("preset_type");
  let presetType: PresetType = PresetTypeSchema.enum.diff;
  const parsedPresetType = PresetTypeSchema.safeParse(requestedPresetType);
  if (parsedPresetType.success) {
    presetType = parsedPresetType.data;
  }
  const preset = searchValue(search, "preset", "");
  const mode = resolveTopLevelMode(
    requestedMode,
    search,
    requestedLeft,
    requestedRight,
  );
  const requestedTab = search.get("tab");
  const tab: ControlsTab =
    requestedTab === "pull-request"
      ? "pull-request"
      : mode === "refs" ||
          mode === "branch-review" ||
          mode === "head" ||
          mode === "preset"
        ? mode
        : "head";
  const pullRequestUrl = searchValue(search, "pull_request_url", "");
  const [defaultLeft, defaultRight] =
    mode === "refs" ? defaultRefsSides : modeSides.head;
  const left = searchValue(search, "left", defaultLeft);
  const right = searchValue(search, "right", defaultRight);

  if (mode in modeSides) {
    const [modeLeft, modeRight] = modeSides[mode as keyof typeof modeSides];
    return {
      tab,
      mode,
      left: modeLeft,
      right: modeRight,
      presetType,
      preset,
      baseSelection,
      reviewSelection,
      pullRequestUrl,
    };
  }

  return {
    tab,
    mode,
    left,
    right,
    presetType,
    preset,
    baseSelection,
    reviewSelection,
    pullRequestUrl,
  };
}

function initialEngine(): DiffEngine {
  const engine = new URLSearchParams(window.location.search).get("engine");
  const parsedEngine = DiffEngineSchema.safeParse(engine);
  if (parsedEngine.success) {
    return parsedEngine.data;
  }
  return DiffEngineSchema.enum.dirdiff;
}

/**
 * Initial controls and engine inferred after repo refs are available.
 *
 * Repo initialization stops at this value on purpose. Starting the diff is a
 * cross-domain workflow owned by App, because it combines repo refs, controls,
 * diff params state, and URL state.
 */
export type InitialRepoDiff = {
  controls: ControlsState;
  engine: DiffEngine;
};

type RepoResourcesOptions = {
  addErrorToast: (title: string, error: unknown) => void;
};

/**
 * Owns repository discovery and repo-local ref metadata.
 *
 * This primitive provides the selected repo id, marked repo list, repo refs,
 * loading/error state, and repo-selection actions. It may update the URL when a
 * repo is selected, because repo_id is part of the repo-selection contract.
 *
 * It does not start diffs, own controls, or mutate loaded diff state. Instead,
 * initializeRepo returns the initial controls/engine that App can hand to the
 * diff primitive.
 */
export function createRepoResources(options: RepoResourcesOptions) {
  const [selectedRepoId, setSelectedRepoId] = createSignal<RepoId | null>(null);
  const [repoSelectionError, setRepoSelectionError] = createSignal("");
  const [repoList, setRepoList] = createSignal<RepoMark[] | null>(null);
  const [reposPending, setReposPending] = createSignal(true);
  const [reposError, setReposError] = createSignal<unknown>(null);
  const [repoRefs, setRepoRefs] = createSignal<RepoRefs | null>(null);
  const [presetCatalogs, setPresetCatalogs] =
    createSignal<PresetCatalogs | null>(null);
  const [presetCatalogsPending, setPresetCatalogsPending] = createSignal(false);
  const [presetCatalogsError, setPresetCatalogsError] =
    createSignal<unknown>(null);
  const [repoRefsPending, setRepoRefsPending] = createSignal(false);
  const [repoRefsError, setRepoRefsError] = createSignal<unknown>(null);
  const [mainBranchSaving, setMainBranchSaving] = createSignal(false);
  let repoRefreshRequest: Promise<RepoMark[] | null> | null = null;
  let presetCatalogsRequest: Promise<PresetCatalogs | null> | null = null;

  const repoPickerRepos = createMemo(() => {
    const repos = repoList();
    if (repos === null) {
      return null;
    }
    if (selectedRepoId() !== null) {
      return null;
    }
    return repos;
  });

  const refChoices = (): RefChoices => {
    const value = repoRefs();
    if (value === null) {
      throw new Error("Ref choices require loaded repo refs.");
    }
    return value.ref_choices;
  };

  function repoIdFromUrl(availableRepos: RepoMark[]): RepoId | null {
    const rawRepoId = new URLSearchParams(window.location.search).get(
      "repo_id",
    );
    if (rawRepoId === null) {
      setSelectedRepoId(null);
      setRepoSelectionError("");
      return null;
    }
    const parsedRepoId = Number(rawRepoId);
    if (!Number.isInteger(parsedRepoId) || parsedRepoId <= 0) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      return null;
    }
    const repo = availableRepos.find(
      (candidate) => candidate.id === parsedRepoId,
    );
    if (repo === undefined) {
      setSelectedRepoId(null);
      setRepoSelectionError(`Invalid repo_id: ${rawRepoId}`);
      return null;
    }
    setSelectedRepoId(parsedRepoId);
    setRepoSelectionError("");
    return parsedRepoId;
  }

  async function loadReposFromUrl(): Promise<RepoId | null> {
    setReposPending(true);
    setReposError(null);
    try {
      const availableRepos = await fetchRepos();
      setRepoList(availableRepos);
      setReposPending(false);
      return repoIdFromUrl(availableRepos);
    } catch (error) {
      batch(() => {
        setReposError(error);
        setReposPending(false);
      });
      options.addErrorToast("Failed to load marked repos", error);
      return null;
    }
  }

  async function refreshRepos(): Promise<void> {
    if (repoRefreshRequest !== null) {
      await repoRefreshRequest;
      return;
    }
    repoRefreshRequest = (async () => {
      try {
        const availableRepos = await fetchRepos();
        batch(() => {
          setRepoList(availableRepos);
          setReposError(null);
        });
        return availableRepos;
      } catch (error) {
        setReposError(error);
        options.addErrorToast("Failed to refresh marked repos", error);
        return null;
      } finally {
        repoRefreshRequest = null;
      }
    })();
    await repoRefreshRequest;
  }

  async function initializeRepo(
    repoId: RepoId,
  ): Promise<InitialRepoDiff | null> {
    setRepoRefs(null);
    setRepoRefsError(null);
    setRepoRefsPending(true);
    try {
      const refs = await fetchRepoRefs(repoId);
      if (selectedRepoId() !== repoId) {
        return null;
      }
      const engine = initialEngine();
      const controls = initialControls(refs);
      batch(() => {
        setRepoRefs(refs);
        setRepoRefsPending(false);
      });
      return { controls, engine };
    } catch (error) {
      if (selectedRepoId() !== repoId) {
        return null;
      }
      batch(() => {
        setRepoRefsError(error);
        setRepoRefsPending(false);
      });
      throw error;
    }
  }

  async function fetchPresetCatalogs(options_: {
    errorTitle: string;
    refresh: boolean;
    swallowError: boolean;
  }): Promise<PresetCatalogs | null> {
    const loadedCatalogs = presetCatalogs();
    if (!options_.refresh && loadedCatalogs !== null) {
      return loadedCatalogs;
    }
    if (presetCatalogsRequest !== null) {
      return presetCatalogsRequest;
    }
    batch(() => {
      setPresetCatalogsPending(true);
      setPresetCatalogsError(null);
    });
    presetCatalogsRequest = (async () => {
      try {
        const catalogs = await fetchPresets();
        batch(() => {
          setPresetCatalogs(catalogs);
          setPresetCatalogsPending(false);
        });
        return catalogs;
      } catch (error) {
        batch(() => {
          setPresetCatalogsError(error);
          setPresetCatalogsPending(false);
        });
        options.addErrorToast(options_.errorTitle, error);
        if (!options_.swallowError) {
          throw error;
        }
        return null;
      } finally {
        presetCatalogsRequest = null;
      }
    })();
    return presetCatalogsRequest;
  }

  async function loadPresetCatalogs(): Promise<PresetCatalogs | null> {
    return fetchPresetCatalogs({
      errorTitle: "Failed to load presets",
      refresh: false,
      swallowError: true,
    });
  }

  async function reloadPresetCatalogs(): Promise<PresetCatalogs> {
    const catalogs = await fetchPresetCatalogs({
      errorTitle: "Failed to reload presets",
      refresh: true,
      swallowError: false,
    });
    if (catalogs === null) {
      const error = new Error("Preset catalog refresh returned no catalogs.");
      options.addErrorToast("Failed to reload presets", error);
      throw error;
    }
    return catalogs;
  }

  async function saveMainBranch(selection: BranchSelection): Promise<void> {
    const repoId = selectedRepoId();
    if (repoId === null) {
      throw new Error("Cannot save main branch without a selected repo.");
    }
    setMainBranchSaving(true);
    try {
      const saved = await saveRepoMainBranch(repoId, selection);
      setRepoRefs((current) =>
        current === null
          ? current
          : {
              ...current,
              default_base_selection: saved.selection,
            },
      );
    } catch (error) {
      options.addErrorToast("Failed to save main branch", error);
      throw error;
    } finally {
      setMainBranchSaving(false);
    }
  }

  const selectRepo = (repo: RepoMark) => {
    const params = new URLSearchParams();
    params.set("repo_id", String(repo.id));
    history.replaceState(
      {},
      "",
      `/?${params.toString()}${window.location.hash}`,
    );
    batch(() => {
      setSelectedRepoId(repo.id);
      setRepoSelectionError("");
      setRepoRefs(null);
      setPresetCatalogs(null);
      presetCatalogsRequest = null;
      setPresetCatalogsError(null);
      setPresetCatalogsPending(false);
    });
  };

  const selectRepoId = (repoId: RepoId) => {
    batch(() => {
      setSelectedRepoId(repoId);
      setRepoSelectionError("");
      setRepoRefs(null);
      setPresetCatalogs(null);
      presetCatalogsRequest = null;
      setPresetCatalogsError(null);
      setPresetCatalogsPending(false);
    });
  };

  return {
    selectedRepoId,
    repoSelectionError,
    repoList,
    reposPending,
    reposError,
    repoRefs,
    presetCatalogs,
    presetCatalogsPending,
    presetCatalogsError,
    repoRefsPending,
    repoRefsError,
    mainBranchSaving,
    repoPickerRepos,
    refChoices,
    loadReposFromUrl,
    refreshRepos,
    initializeRepo,
    loadPresetCatalogs,
    reloadPresetCatalogs,
    saveMainBranch,
    selectRepo,
    selectRepoId,
  };
}

export type RepoResources = ReturnType<typeof createRepoResources>;
