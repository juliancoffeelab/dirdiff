import { batch, createSignal } from "solid-js";
import {
  type BranchSelection,
  type DefaultBaseSelection,
  PresetTypeSchema,
  deleteRepoMark,
  fetchPresets,
  fetchRepoDefaults,
  fetchRepoRefs,
  fetchRepos,
  saveRepoMainBranch,
  type PresetCatalogs,
  type PresetType,
  type RepoDefaults,
  type ProjectId,
  type RepoMark,
  type RepoRefs,
} from "../api";
import { type BranchSelectionDraft, type ControlsState } from "../fileUtils";
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
): BranchSelection | null {
  if (!branchSelectionParamPresent(search, prefix)) {
    return null;
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
 * Convert /api/repo-defaults base resolution into branch-review controls.
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

function selectedBranchDraft(selection: BranchSelection): BranchSelectionDraft {
  return { state: "selected", value: selection };
}

function missingBranchDraft(): BranchSelectionDraft {
  return { state: "missing" };
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

/**
 * Build control state directly from URL params and already-known repo defaults.
 *
 * Repo defaults are intentionally optional here. First render must not wait on
 * `/api/repo-defaults`, so missing branch defaults stay tagged as missing until
 * explicit metadata loading supplies them or the user types a value.
 */
export function initialControlsFromUrl(
  repoDefaults: RepoDefaults | null,
): ControlsState {
  const search = new URLSearchParams(window.location.search);
  const requestedLeft = searchValue(search, "left", "head");
  const requestedRight = searchValue(search, "right", "worktree");
  const baseSelection = branchSelectionFromSearch(search, "base");
  // URL-provided branch selections are user intent and win over repo defaults.
  // Defaults are only allowed to fill fields that are absent from the URL.
  const baseSelectionDraft =
    baseSelection !== null
      ? selectedBranchDraft(baseSelection)
      : repoDefaults === null
        ? missingBranchDraft()
        : selectedBranchDraft(
            baseSelectionFromDefault(repoDefaults.default_base_selection),
          );
  const reviewSelection = branchSelectionFromSearch(search, "review");
  const reviewSelectionDraft =
    reviewSelection !== null
      ? selectedBranchDraft(reviewSelection)
      : repoDefaults === null
        ? missingBranchDraft()
        : selectedBranchDraft(repoDefaults.preferred_review_selection);
  const requestedMode = search.get("mode") as ControlsState["mode"] | null;
  const requestedPresetType = search.get("project_id");
  let presetType: PresetType = PresetTypeSchema.enum.diff;
  const parsedPresetType = PresetTypeSchema.safeParse(requestedPresetType);
  if (parsedPresetType.success) {
    presetType = parsedPresetType.data;
  }
  const preset = searchValue(search, "preset_subset", "");
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
      baseSelection: baseSelectionDraft,
      reviewSelection: reviewSelectionDraft,
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
    baseSelection: baseSelectionDraft,
    reviewSelection: reviewSelectionDraft,
    pullRequestUrl,
  };
}

type RepoResourcesOptions = {
  addErrorToast: (title: string, error: unknown) => void;
};

/**
 * Owns repository discovery and repo-local metadata.
 *
 * This primitive provides the selected project id, marked repo list, repo defaults,
 * repo refs, loading/error state, and repo-selection actions. It may update the
 * URL when a repo is selected, because project_id is part of the repo-selection
 * contract.
 *
 * It does not start diffs, own controls, or mutate loaded diff state. App owns
 * that cross-domain workflow because it combines repo metadata, controls, diff
 * params state, and URL state.
 */
export function createRepoResources(options: RepoResourcesOptions) {
  const [selectedProjectId, setSelectedProjectId] =
    createSignal<ProjectId | null>(null);
  const [repoSelectionError, setRepoSelectionError] = createSignal("");
  const [repoList, setRepoList] = createSignal<RepoMark[] | null>(null);
  const [reposPending, setReposPending] = createSignal(true);
  const [reposError, setReposError] = createSignal<unknown>(null);
  const [repoDefaults, setRepoDefaults] = createSignal<RepoDefaults | null>(
    null,
  );
  const [repoDefaultsPending, setRepoDefaultsPending] = createSignal(false);
  const [repoDefaultsError, setRepoDefaultsError] = createSignal<unknown>(null);
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
  let repoDefaultsRequest: Promise<RepoDefaults | null> | null = null;
  let repoRefsRequest: Promise<RepoRefs | null> | null = null;
  let presetCatalogsRequest: Promise<PresetCatalogs | null> | null = null;

  function urlStartsInRepoIndependentMode(): boolean {
    const search = new URLSearchParams(window.location.search);
    return (
      search.get("mode") === "preset" || search.get("tab") === "pull-request"
    );
  }

  function projectIdFromUrl(availableRepos: RepoMark[]): ProjectId | null {
    if (urlStartsInRepoIndependentMode()) {
      // Preset and PR startup flows are self-contained. A stale project_id from an
      // older URL must not create a header error or block those workflows; if the
      // user wants a repo-backed diff, selecting a repo later will write a fresh
      // project_id into the URL.
      setRepoSelectionError("");
      return null;
    }
    const parsedProjectId = projectIdParamFromUrl();
    if (parsedProjectId === null) {
      setSelectedProjectId(null);
      setRepoSelectionError("");
      return null;
    }
    const repo = availableRepos.find(
      (candidate) => candidate.id === parsedProjectId,
    );
    if (repo === undefined) {
      setSelectedProjectId(null);
      setRepoSelectionError(`Invalid project_id: ${parsedProjectId}`);
      return null;
    }
    setSelectedProjectId(parsedProjectId);
    setRepoSelectionError("");
    return parsedProjectId;
  }

  function projectIdParamFromUrl(): ProjectId | null {
    const rawProjectId = new URLSearchParams(window.location.search).get(
      "project_id",
    );
    if (rawProjectId === null) {
      return null;
    }
    const parsedProjectId = Number(rawProjectId);
    if (!Number.isInteger(parsedProjectId) || parsedProjectId <= 0) {
      setRepoSelectionError(`Invalid project_id: ${rawProjectId}`);
      return null;
    }
    return parsedProjectId;
  }

  async function loadReposFromUrl(): Promise<ProjectId | null> {
    setReposPending(true);
    setReposError(null);
    try {
      const availableRepos = await fetchRepos();
      setRepoList(availableRepos);
      setReposPending(false);
      return projectIdFromUrl(availableRepos);
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

  async function loadRepoDefaults(
    projectId: ProjectId,
  ): Promise<RepoDefaults | null> {
    if (repoDefaultsRequest !== null) {
      return repoDefaultsRequest;
    }
    setRepoDefaults(null);
    setRepoDefaultsError(null);
    setRepoDefaultsPending(true);
    let request: Promise<RepoDefaults | null> = Promise.resolve(null);
    request = (async () => {
      try {
        const defaults = await fetchRepoDefaults(projectId);
        if (selectedProjectId() !== projectId) {
          // Repo switches do not cancel in-flight fetches. Drop stale metadata at
          // the resource boundary so App never patches the wrong repo draft.
          return null;
        }
        batch(() => {
          setRepoDefaults(defaults);
          setRepoDefaultsPending(false);
        });
        return defaults;
      } catch (error) {
        if (selectedProjectId() !== projectId) {
          // A stale failure should not surface as an error for the newly selected
          // repo; it belongs to a request the UI has already moved past.
          return null;
        }
        batch(() => {
          setRepoDefaultsError(error);
          setRepoDefaultsPending(false);
        });
        options.addErrorToast("Failed to load repo defaults", error);
        return null;
      } finally {
        if (repoDefaultsRequest === request) {
          repoDefaultsRequest = null;
        }
      }
    })();
    repoDefaultsRequest = request;
    return repoDefaultsRequest;
  }

  async function loadRepoRefs(projectId: ProjectId): Promise<RepoRefs | null> {
    if (repoRefsRequest !== null) {
      return repoRefsRequest;
    }
    setRepoRefs(null);
    setRepoRefsError(null);
    setRepoRefsPending(true);
    let request: Promise<RepoRefs | null> = Promise.resolve(null);
    request = (async () => {
      try {
        const refs = await fetchRepoRefs(projectId);
        if (selectedProjectId() !== projectId) {
          // Ref choices are suggestions only, but stale suggestions are still
          // misleading. Keep them scoped to the selected project id.
          return null;
        }
        batch(() => {
          setRepoRefs(refs);
          setRepoRefsPending(false);
        });
        return refs;
      } catch (error) {
        if (selectedProjectId() !== projectId) {
          // Ignore stale ref failures for the same reason as stale defaults:
          // the visible repo has changed, so this request no longer applies.
          return null;
        }
        batch(() => {
          setRepoRefsError(error);
          setRepoRefsPending(false);
        });
        options.addErrorToast("Failed to load repo refs", error);
        return null;
      } finally {
        if (repoRefsRequest === request) {
          repoRefsRequest = null;
        }
      }
    })();
    repoRefsRequest = request;
    return repoRefsRequest;
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
    const projectId = selectedProjectId();
    if (projectId === null) {
      throw new Error("Cannot save main branch without a selected repo.");
    }
    setMainBranchSaving(true);
    try {
      const saved = await saveRepoMainBranch(projectId, selection);
      setRepoDefaults((current) =>
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

  async function removeRepo(projectId: ProjectId): Promise<void> {
    try {
      await deleteRepoMark(projectId);
      batch(() => {
        setRepoList((current) =>
          current === null
            ? current
            : current.filter((repo) => repo.id !== projectId),
        );
        if (selectedProjectId() === projectId) {
          history.replaceState({}, "", `/${window.location.hash}`);
          setSelectedProjectId(null);
          setRepoDefaults(null);
          setRepoDefaultsError(null);
          setRepoDefaultsPending(false);
          repoDefaultsRequest = null;
          setRepoRefs(null);
          setRepoRefsError(null);
          setRepoRefsPending(false);
          repoRefsRequest = null;
          setPresetCatalogs(null);
          presetCatalogsRequest = null;
          setPresetCatalogsError(null);
          setPresetCatalogsPending(false);
        }
      });
    } catch (error) {
      options.addErrorToast("Failed to remove marked repo", error);
      throw error;
    }
  }

  const selectRepo = (repo: RepoMark) => {
    const params = new URLSearchParams();
    params.set("project_id", String(repo.id));
    history.replaceState(
      {},
      "",
      `/?${params.toString()}${window.location.hash}`,
    );
    batch(() => {
      setSelectedProjectId(repo.id);
      setRepoSelectionError("");
      setRepoDefaults(null);
      setRepoDefaultsError(null);
      setRepoDefaultsPending(false);
      repoDefaultsRequest = null;
      setRepoRefs(null);
      setRepoRefsError(null);
      setRepoRefsPending(false);
      repoRefsRequest = null;
      setPresetCatalogs(null);
      presetCatalogsRequest = null;
      setPresetCatalogsError(null);
      setPresetCatalogsPending(false);
    });
  };

  const selectProjectId = (projectId: ProjectId) => {
    batch(() => {
      setSelectedProjectId(projectId);
      setRepoSelectionError("");
      setRepoDefaults(null);
      setRepoDefaultsError(null);
      setRepoDefaultsPending(false);
      repoDefaultsRequest = null;
      setRepoRefs(null);
      setRepoRefsError(null);
      setRepoRefsPending(false);
      repoRefsRequest = null;
      setPresetCatalogs(null);
      presetCatalogsRequest = null;
      setPresetCatalogsError(null);
      setPresetCatalogsPending(false);
    });
  };

  return {
    selectedProjectId,
    repoSelectionError,
    repoList,
    reposPending,
    reposError,
    repoDefaults,
    repoDefaultsPending,
    repoDefaultsError,
    repoRefs,
    presetCatalogs,
    presetCatalogsPending,
    presetCatalogsError,
    repoRefsPending,
    repoRefsError,
    mainBranchSaving,
    loadReposFromUrl,
    refreshRepos,
    loadRepoDefaults,
    loadRepoRefs,
    loadPresetCatalogs,
    reloadPresetCatalogs,
    saveMainBranch,
    removeRepo,
    selectRepo,
    selectProjectId,
  };
}

export type RepoResources = ReturnType<typeof createRepoResources>;
