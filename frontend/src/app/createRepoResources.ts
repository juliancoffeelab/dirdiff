import { batch, createMemo, createSignal } from "solid-js";
import {
  fetchPresets,
  fetchRepoRefs,
  fetchRepos,
  type DiffEngine,
  type PresetCatalogs,
  type PresetType,
  type RefChoices,
  type RepoId,
  type RepoMark,
  type RepoRefs,
} from "../api";
import { type ControlsState } from "../fileUtils";

function nullableStringValue(
  value: string | null | undefined,
  fallback: string,
): string {
  if (value !== null && value !== undefined && value.length > 0) {
    return value;
  }
  return fallback;
}

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

function splitRemoteQualifiedRef(
  ref: string,
  remoteNames: string[],
): { remote: string; value: string } {
  const trimmedRef = ref.trim();
  for (const remoteName of [...remoteNames].sort(
    (left, right) => right.length - left.length,
  )) {
    const prefix = `${remoteName}/`;
    if (trimmedRef.startsWith(prefix)) {
      return {
        remote: remoteName,
        value: trimmedRef.slice(prefix.length),
      };
    }
  }
  return {
    remote: "",
    value: trimmedRef,
  };
}

const modeSides = {
  files: ["index", "worktree"],
  staged: ["head", "index"],
  head: ["head", "worktree"],
} as const;
const defaultRefsSides = ["head~1", "head"] as const;

function inferMode(
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
) {
  if (baseBranch.length > 0 || reviewBranch.length > 0) {
    return "branch-review" as const;
  }
  return left === "head" && right === "worktree" ? "head" : "refs";
}

function resolveTopLevelMode(
  mode: ControlsState["mode"] | null,
  left: string,
  right: string,
  baseBranch: string,
  reviewBranch: string,
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
  return inferMode(left, right, baseBranch, reviewBranch);
}

function initialControls(repoRefs: RepoRefs): ControlsState {
  const search = new URLSearchParams(window.location.search);
  const remoteNames = repoRefs.ref_choices.remote_names;
  const requestedLeft = searchValue(search, "left", "head");
  const requestedRight = searchValue(search, "right", "worktree");
  const baseBranchRef = searchValue(
    search,
    "base_branch",
    nullableStringValue(repoRefs.default_base_branch, ""),
  );
  const reviewBranchRef = searchValue(
    search,
    "review_branch",
    nullableStringValue(repoRefs.preferred_review_branch, ""),
  );
  const baseBranchParts = splitRemoteQualifiedRef(baseBranchRef, remoteNames);
  const reviewBranchParts = splitRemoteQualifiedRef(
    reviewBranchRef,
    remoteNames,
  );
  const requestedMode = search.get("mode") as ControlsState["mode"] | null;
  const requestedPresetType = search.get("preset_type");
  const presetType: PresetType =
    requestedPresetType === "fold" ? "fold" : "diff";
  const preset = searchValue(search, "preset", "");
  const mode =
    requestedMode === null
      ? "head"
      : resolveTopLevelMode(
          requestedMode,
          requestedLeft,
          requestedRight,
          baseBranchParts.value,
          reviewBranchParts.value,
        );
  const [defaultLeft, defaultRight] =
    mode === "refs" ? defaultRefsSides : modeSides.head;
  const left = searchValue(search, "left", defaultLeft);
  const right = searchValue(search, "right", defaultRight);

  if (mode in modeSides) {
    const [modeLeft, modeRight] = modeSides[mode as keyof typeof modeSides];
    return {
      mode,
      left: modeLeft,
      right: modeRight,
      presetType,
      preset,
      baseSource: baseBranchParts.remote ? "remote" : "local",
      baseRemote: baseBranchParts.remote,
      baseBranch: baseBranchParts.value,
      branchSource: reviewBranchParts.remote ? "remote" : "local",
      branchRemote: reviewBranchParts.remote,
      reviewBranch: reviewBranchParts.value,
    };
  }

  return {
    mode,
    left,
    right,
    presetType,
    preset,
    baseSource: baseBranchParts.remote ? "remote" : "local",
    baseRemote: baseBranchParts.remote,
    baseBranch: baseBranchParts.value,
    branchSource: reviewBranchParts.remote ? "remote" : "local",
    branchRemote: reviewBranchParts.remote,
    reviewBranch: reviewBranchParts.value,
  };
}

function initialEngine(): DiffEngine {
  const engine = new URLSearchParams(window.location.search).get("engine");
  if (engine === "git" || engine === "dirdiff" || engine === "difftastic") {
    return engine;
  }
  return "dirdiff";
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
    repoPickerRepos,
    refChoices,
    loadReposFromUrl,
    refreshRepos,
    initializeRepo,
    loadPresetCatalogs,
    reloadPresetCatalogs,
    selectRepo,
  };
}

export type RepoResources = ReturnType<typeof createRepoResources>;
