import { batch, createMemo, createSignal } from "solid-js";
import {
  fetchRepoRefs,
  fetchRepos,
  type DiffEngine,
  type RefChoices,
  type RepoId,
  type RepoMark,
  type RepoRefs,
} from "../api";
import { type ControlsState, initialControls, initialEngine } from "../model";

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
  const [repoRefsPending, setRepoRefsPending] = createSignal(false);
  const [repoRefsError, setRepoRefsError] = createSignal<unknown>(null);

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
    });
  };

  return {
    selectedRepoId,
    repoSelectionError,
    repoList,
    reposPending,
    reposError,
    repoRefs,
    repoRefsPending,
    repoRefsError,
    repoPickerRepos,
    refChoices,
    loadReposFromUrl,
    initializeRepo,
    selectRepo,
  };
}

export type RepoResources = ReturnType<typeof createRepoResources>;
