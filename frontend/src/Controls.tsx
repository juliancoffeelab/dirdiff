import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import type { JSX } from "solid-js";
import { Save } from "lucide-solid";
import type {
  BranchSelection,
  PresetCatalogs,
  PresetType,
  RefChoices,
  RepoMark,
} from "./api";
import { RepoPicker } from "./RepoPicker";
import {
  type AutocompleteGroup,
  type BranchSelectionDraft,
  type ControlsState,
  type RepoListStatus,
  controlsTabLabels,
  presetTypeLabels,
  presetTypes,
  refSectionLabels,
  topLevelTabs,
} from "./fileUtils";

// This table intentionally mirrors the exact `/api/repo-refs` built-in values.
// Keep the lookup strict so a backend/frontend contract drift fails loudly
// instead of silently dropping autocomplete descriptions.
const builtinRefDescriptions = {
  HEAD: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
} as const;
const defaultRefsDraft = {
  left: "head~1",
  right: "head",
} as const;

type RemoteBranchSelection = Extract<BranchSelection, { source: "remote" }>;

export type RefChoicesStatus =
  | {
      /**
       * Ref metadata is unavailable. The user may still type freeform refs and
       * branches; only autocomplete/suggestions are missing in this state.
       */
      state: "missing";
    }
  | {
      /** Ref metadata is loaded and may be used for autocomplete only. */
      state: "loaded";
      value: RefChoices;
    };

export type RepoSelectionStatus =
  | {
      /**
       * No project id is selected yet. Repo-backed tabs may still show their draft
       * controls, but loading a repo diff must be blocked with a clear prompt.
       */
      state: "missing";
    }
  | {
      /** A project id is available for repo-backed diff requests. */
      state: "selected";
      projectId: number;
    };

export type PresetCatalogsStatus =
  | {
      /** Preset catalogs have not been requested or have not returned yet. */
      state: "missing";
    }
  | {
      /** Preset catalogs are loaded and can seed preset controls. */
      state: "loaded";
      value: PresetCatalogs;
    };

/** Switch a BranchSelection between local and remote while preserving branch text. */
function selectionWithSource(
  selection: BranchSelectionDraft,
  source: BranchSelection["source"],
  refChoices: RefChoices | null,
): BranchSelection {
  const currentSelection =
    selection.state === "selected" ? selection.value : null;
  if (source === "local") {
    return { source, branch: currentSelection?.branch ?? "" };
  }
  return {
    source,
    remote:
      currentSelection?.source === "remote" &&
      currentSelection.remote.length > 0
        ? currentSelection.remote
        : firstRemoteName(refChoices),
    branch: currentSelection?.branch ?? "",
  };
}

/** Update the remote name on an already-remote BranchSelection. */
function selectionWithRemote(
  selection: RemoteBranchSelection,
  remote: string,
): BranchSelection {
  return { source: "remote", remote, branch: selection.branch };
}

/** Update only the branch text while preserving the local/remote variant. */
function selectionWithBranch(
  selection: BranchSelectionDraft,
  branch: string,
): BranchSelection {
  if (selection.state === "missing" || selection.value.source === "local") {
    return { source: "local", branch };
  }
  return { ...selection.value, branch };
}

function selectedBranchDraft(selection: BranchSelection): BranchSelectionDraft {
  return { state: "selected", value: selection };
}

function branchDraftValue(
  selection: BranchSelectionDraft,
): BranchSelection | null {
  return selection.state === "selected" ? selection.value : null;
}

export function Controls(props: {
  controls: ControlsState;
  repoSelection: RepoSelectionStatus;
  repos: RepoListStatus;
  repoSelectionError: string;
  refChoices: RefChoicesStatus;
  presetCatalogs: PresetCatalogsStatus;
  presetCatalogsError: unknown;
  onPresetMode: () => Promise<PresetCatalogs | null>;
  onAgainstHead: () => void;
  onPreset: (presetType: PresetType, preset: string) => void;
  onRefs: (left: string, right: string) => void;
  onPullRequest: (url: string) => void | Promise<void>;
  onBranchReview: (
    baseSelection: BranchSelectionDraft,
    reviewSelection: BranchSelectionDraft,
  ) => void;
  mainBranchSaving: boolean;
  onSaveMainBranch: (selection: BranchSelection) => void | Promise<void>;
  onBranchSelectionEdit: (slot: "base" | "review") => void;
  /**
   * Mirrors user-edited draft state into App.
   *
   * Controls keeps a local draft for immediate typing, but App owns delayed
   * startup after `/api/repos` validates a URL project id. Without this callback,
   * delayed startup can accidentally load the mount-time URL draft after the
   * user has already changed tabs, refs, branches, preset, or PR input.
   */
  onControlsDraftChange: (controls: ControlsState) => void;
  onRepoSelect: (repo: RepoMark) => void;
  onRepoRemove: (repo: RepoMark) => void | Promise<void>;
  onRefsMode: () => void;
  onBranchReviewMode: () => void;
}) {
  const [draft, setDraft] = createSignal<ControlsState>(props.controls);
  // Keep the local draft in sync with App-owned state so typing stays immediate,
  // while delayed repo/default loads can still patch missing fields centrally.
  createEffect(() => setDraft(props.controls));

  const refChoicesOrNull = (): RefChoices | null =>
    props.refChoices.state === "loaded" ? props.refChoices.value : null;
  const presetCatalogsOrNull = (): PresetCatalogs | null =>
    props.presetCatalogs.state === "loaded" ? props.presetCatalogs.value : null;

  const repoBackedModeSelected = () => {
    const tab = draft().tab;
    // Only these tabs load Git-backed workspace diffs directly. PR first
    // prepares its own repo from the URL, and Preset uses checked-in fixture
    // catalogs, so neither tab should show the repo picker or block Load when
    // the header has no selected repo.
    return tab === "head" || tab === "refs" || tab === "branch-review";
  };
  const repoBackedLoadBlocked = () =>
    repoBackedModeSelected() && props.repoSelection.state === "missing";
  const branchReviewMissingRepo = () =>
    draft().tab === "branch-review" && props.repoSelection.state === "missing";

  const commitDraft = (nextDraft: ControlsState) => {
    setDraft(nextDraft);
    props.onControlsDraftChange(nextDraft);
  };

  const updateDraft = (patch: Partial<ControlsState>) => {
    const nextDraft = { ...draft(), ...patch };
    commitDraft(nextDraft);
  };

  const loadDefaultPresetWhenCatalogArrives = async (
    presetType: PresetType,
  ) => {
    const catalogs = await props.onPresetMode();
    if (catalogs === null) {
      return;
    }
    const currentDraft = draft();
    if (currentDraft.mode !== "preset") {
      // The user may leave the preset tab while catalogs are loading. In that
      // case the late catalog result is cache state only, not a draft update.
      return;
    }
    const nextDraft = {
      ...currentDraft,
      presetType,
      preset: catalogs[presetType].default_preset,
    };
    commitDraft(nextDraft);
    if (!repoBackedLoadBlocked()) {
      loadDraft(nextDraft);
    }
  };

  const loadDraft = (value: ControlsState) => {
    if (value.tab === "pull-request") {
      void props.onPullRequest(value.pullRequestUrl);
      return;
    }
    if (value.mode === "refs") {
      props.onRefs(value.left, value.right);
      return;
    }
    if (value.mode === "branch-review") {
      // Branch-review draft selections are allowed to be missing while rendered;
      // the diff loader is the single boundary that turns missing into an error.
      props.onBranchReview(value.baseSelection, value.reviewSelection);
      return;
    }
    if (value.mode === "preset") {
      const catalogs = presetCatalogsOrNull();
      if (catalogs === null) {
        void loadDefaultPresetWhenCatalogArrives(value.presetType);
        return;
      }
      if (value.preset.length === 0) {
        props.onPreset(
          value.presetType,
          catalogs[value.presetType].default_preset,
        );
        return;
      }
      props.onPreset(value.presetType, value.preset);
      return;
    }
    props.onAgainstHead();
  };

  const submit = (event: SubmitEvent) => {
    event.preventDefault();
    if (repoBackedLoadBlocked()) {
      return;
    }
    loadDraft(draft());
  };

  const saveMainBranchButton = () => (
    <button
      type="button"
      class="field-icon-button"
      aria-label="Save main branch"
      title="Save main branch"
      disabled={
        props.mainBranchSaving ||
        props.repoSelection.state === "missing" ||
        !mainBranchSelectionSavable(draft().baseSelection)
      }
      onClick={() => {
        const selection = draft().baseSelection;
        if (selection.state === "selected") {
          void props.onSaveMainBranch(selection.value);
        }
      }}
    >
      <Save class="field-icon" aria-hidden="true" />
    </button>
  );

  return (
    <form class="controls" onSubmit={submit}>
      <fieldset class="mode-tabs">
        <legend>View</legend>
        <For each={topLevelTabs}>
          {(tab) => (
            <button
              type="button"
              classList={{ "is-active": draft().tab === tab }}
              aria-pressed={draft().tab === tab}
              onClick={() => {
                if (tab === "pull-request") {
                  // PR mode does not require a selected repo; the URL prepare
                  // call will select one when the user explicitly loads.
                  commitDraft({ ...draft(), tab });
                  return;
                }
                if (tab === "preset") {
                  const catalogs = presetCatalogsOrNull();
                  const nextDraft =
                    catalogs === null
                      ? { ...draft(), tab, mode: "preset" as const }
                      : {
                          ...draft(),
                          tab,
                          mode: "preset" as const,
                          preset: catalogs[draft().presetType].default_preset,
                        };
                  commitDraft(nextDraft);
                  if (catalogs === null) {
                    void loadDefaultPresetWhenCatalogArrives(
                      nextDraft.presetType,
                    );
                    return;
                  }
                  if (!repoBackedLoadBlocked()) {
                    loadDraft(nextDraft);
                  }
                  return;
                }
                const nextDraft =
                  tab === "refs"
                    ? { ...draft(), tab, mode: tab, ...defaultRefsDraft }
                    : { ...draft(), tab, mode: tab };
                commitDraft(nextDraft);
                if (tab === "refs") {
                  // Refs are autocomplete metadata only. Start loading them when
                  // the user visits the tab, but keep the freeform inputs usable.
                  props.onRefsMode();
                }
                if (tab === "branch-review") {
                  // Defaults can fill missing branch drafts, and refs can power
                  // autocomplete, but the tab itself remains clickable without them.
                  props.onBranchReviewMode();
                }
                if (!repoBackedLoadBlocked()) {
                  loadDraft(nextDraft);
                }
              }}
            >
              {controlsTabLabels[tab]}
            </button>
          )}
        </For>
      </fieldset>

      <Show when={draft().tab === "refs"}>
        <AutocompleteField
          label="Old ref"
          value={draft().left}
          groups={(query) =>
            filterRefChoices(refChoicesOrNull(), query, [
              "builtins",
              "local_branches",
              "remote_branches",
            ])
          }
          onValue={(left) => updateDraft({ left })}
        />
        <AutocompleteField
          label="New ref"
          value={draft().right}
          groups={(query) =>
            filterRefChoices(refChoicesOrNull(), query, [
              "builtins",
              "local_branches",
              "remote_branches",
            ])
          }
          onValue={(right) => updateDraft({ right })}
        />
      </Show>

      <Show
        when={draft().tab === "branch-review" && !branchReviewMissingRepo()}
      >
        <BranchSourceField
          label="Base remote"
          selection={draft().baseSelection}
          refChoices={refChoicesOrNull()}
          onSelection={(baseSelection) => {
            props.onBranchSelectionEdit("base");
            updateDraft({ baseSelection: selectedBranchDraft(baseSelection) });
          }}
          action={saveMainBranchButton()}
        />
        <AutocompleteField
          label="Base branch"
          value={branchDraftValue(draft().baseSelection)?.branch ?? ""}
          placeholder="base branch"
          groups={(query) =>
            filterBranchChoices(
              refChoicesOrNull(),
              draft().baseSelection,
              query,
            )
          }
          onValue={(branch) => {
            props.onBranchSelectionEdit("base");
            updateDraft({
              baseSelection: selectedBranchDraft(
                selectionWithBranch(draft().baseSelection, branch),
              ),
            });
          }}
          action={saveMainBranchButton()}
        />
        <BranchSourceField
          label="Branch remote"
          selection={draft().reviewSelection}
          refChoices={refChoicesOrNull()}
          onSelection={(reviewSelection) => {
            props.onBranchSelectionEdit("review");
            updateDraft({
              reviewSelection: selectedBranchDraft(reviewSelection),
            });
          }}
        />
        <AutocompleteField
          label="Branch to review"
          value={branchDraftValue(draft().reviewSelection)?.branch ?? ""}
          placeholder="review branch"
          groups={(query) =>
            filterBranchChoices(
              refChoicesOrNull(),
              draft().reviewSelection,
              query,
            )
          }
          onValue={(branch) => {
            props.onBranchSelectionEdit("review");
            updateDraft({
              reviewSelection: selectedBranchDraft(
                selectionWithBranch(draft().reviewSelection, branch),
              ),
            });
          }}
        />
      </Show>

      <Show when={draft().tab === "pull-request"}>
        <label class="field pull-request-field">
          <span>Pull request</span>
          <input
            value={draft().pullRequestUrl}
            placeholder="GitHub PR or GitLab MR URL"
            spellcheck={false}
            autocomplete="off"
            onInput={(event) =>
              updateDraft({ pullRequestUrl: event.currentTarget.value })
            }
          />
        </label>
      </Show>

      <Show when={draft().tab === "preset"}>
        <Show when={props.presetCatalogsError !== null}>
          <section class="notice error">
            Failed to load presets: {String(props.presetCatalogsError)}
          </section>
        </Show>
        <fieldset class="mode-tabs preset-tabs">
          <legend>Preset type</legend>
          <For each={presetTypes}>
            {(presetType) => (
              <button
                type="button"
                onClick={() => {
                  const catalogs = presetCatalogsOrNull();
                  if (catalogs === null) {
                    void loadDefaultPresetWhenCatalogArrives(presetType);
                    return;
                  }
                  const catalog = catalogs[presetType];
                  const nextDraft = {
                    ...draft(),
                    presetType,
                    preset: catalog.default_preset,
                  };
                  commitDraft(nextDraft);
                  loadDraft(nextDraft);
                }}
                classList={{
                  "is-active": draft().presetType === presetType,
                }}
                aria-pressed={draft().presetType === presetType}
              >
                {presetTypeLabels[presetType]}
              </button>
            )}
          </For>
        </fieldset>
        <Show when={presetCatalogsOrNull()}>
          {(catalogs) => (
            <fieldset class="mode-tabs preset-tabs">
              <legend>Presets</legend>
              <For each={catalogs()[draft().presetType].groups}>
                {(group) => (
                  <button
                    type="button"
                    onClick={() => {
                      const nextDraft = { ...draft(), preset: group.id };
                      commitDraft(nextDraft);
                      loadDraft(nextDraft);
                    }}
                    classList={{ "is-active": draft().preset === group.id }}
                    aria-pressed={draft().preset === group.id}
                  >
                    {group.display_name}
                  </button>
                )}
              </For>
            </fieldset>
          )}
        </Show>
      </Show>

      <Show when={repoBackedLoadBlocked()}>
        {/* Repo-backed modes can be edited without a repo, but the actual Load
        action is replaced by repo selection until a project id exists. */}
        <RepoPicker
          repos={props.repos}
          error={props.repoSelectionError}
          onSelect={props.onRepoSelect}
          onRemove={props.onRepoRemove}
        />
      </Show>
      <Show when={!repoBackedLoadBlocked()}>
        <button class="load-button" type="submit">
          Load
        </button>
      </Show>
    </form>
  );
}

/**
 * Edit only the source/remote half of a BranchSelection.
 *
 * The branch text remains in the adjacent AutocompleteField so branch-review
 * controls stay as four direct grid children.
 */
function BranchSourceField(props: {
  label: string;
  selection: BranchSelectionDraft;
  refChoices: RefChoices | null;
  onSelection: (selection: BranchSelection) => void;
  action?: JSX.Element;
}) {
  const [focused, setFocused] = createSignal(false);
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const remoteSelection = createMemo((): RemoteBranchSelection | null =>
    props.selection.state === "selected" &&
    props.selection.value.source === "remote"
      ? props.selection.value
      : null,
  );
  const groups = createMemo(() => {
    const selection = remoteSelection();
    if (!focused() || selection === null) {
      return [];
    }
    const refs = props.refChoices;
    if (refs === null) {
      return [];
    }
    const values = filterValues(refs.remotes, selection.remote);
    return values.length ? [["remotes", values] as AutocompleteGroup] : [];
  });

  onCleanup(() => {
    const timer = blurTimer();
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  });

  const closeSoon = () => {
    setBlurTimer(window.setTimeout(() => setFocused(false), 120));
  };

  const keepOpen = () => {
    const timer = blurTimer();
    if (timer !== undefined) {
      clearTimeout(timer);
      setBlurTimer(undefined);
    }
  };

  const toggleSource = () => {
    const nextSelection = selectionWithSource(
      props.selection,
      props.selection.state === "selected" &&
        props.selection.value.source === "remote"
        ? "local"
        : "remote",
      props.refChoices,
    );
    props.onSelection(nextSelection);
    setFocused(nextSelection.source === "remote");
  };

  return (
    <div class="field branch-source-field autocomplete-host">
      <span>{props.label}</span>
      <div
        classList={{
          "branch-source-control": true,
          "is-remote":
            props.selection.state === "selected" &&
            props.selection.value.source === "remote",
        }}
      >
        <button
          type="button"
          class="branch-source-toggle"
          aria-pressed={
            props.selection.state === "selected" &&
            props.selection.value.source === "remote"
          }
          onClick={toggleSource}
        >
          {props.selection.state === "selected" &&
          props.selection.value.source === "remote"
            ? "Remote"
            : "Local"}
        </button>
        <Show when={remoteSelection()}>
          {(selection) => (
            <input
              class="branch-source-remote"
              value={selection().remote}
              aria-label={props.label}
              placeholder="remote"
              spellcheck={false}
              autocomplete="off"
              onFocus={() => setFocused(true)}
              onBlur={closeSoon}
              onClick={() => setFocused(true)}
              onPointerDown={() => setFocused(true)}
              onInput={(event) => {
                props.onSelection(
                  selectionWithRemote(selection(), event.currentTarget.value),
                );
                setFocused(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setFocused(false);
                }
              }}
            />
          )}
        </Show>
      </div>
      <Show when={groups().length > 0}>
        <div class="autocomplete-panel" onMouseDown={keepOpen}>
          <For each={groups()}>
            {([section, values]) => (
              <div class="autocomplete-section">
                <div class="autocomplete-section-label">
                  {autocompleteSectionLabel(section)}
                </div>
                <For each={values}>
                  {(value) => (
                    <button
                      type="button"
                      class="autocomplete-option"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        const selection = remoteSelection();
                        if (selection === null) {
                          throw new Error(
                            "Remote autocomplete opened for a local branch selection",
                          );
                        }
                        props.onSelection(
                          selectionWithRemote(selection, value),
                        );
                        setFocused(false);
                      }}
                    >
                      {value}
                    </button>
                  )}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
      {props.action}
    </div>
  );
}

function AutocompleteField(props: {
  label: string;
  value: string;
  placeholder?: string;
  groups: (query: string) => AutocompleteGroup[];
  onValue: (value: string) => void;
  action?: JSX.Element;
}) {
  let input: HTMLInputElement | undefined;
  const [focused, setFocused] = createSignal(false);
  const [query, setQuery] = createSignal("");
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const groups = createMemo(() => (focused() ? props.groups(query()) : []));

  onMount(() => {
    if (input === undefined) {
      return;
    }
    const open = () => {
      setQuery("");
      setFocused(true);
    };
    input.addEventListener("focus", open);
    input.addEventListener("blur", closeSoon);
    onCleanup(() => {
      input?.removeEventListener("focus", open);
      input?.removeEventListener("blur", closeSoon);
    });
  });

  onCleanup(() => {
    const timer = blurTimer();
    if (timer !== undefined) {
      clearTimeout(timer);
    }
  });

  const closeSoon = () => {
    setBlurTimer(
      window.setTimeout(() => {
        setFocused(false);
        setQuery("");
      }, 120),
    );
  };

  const keepOpen = () => {
    const timer = blurTimer();
    if (timer !== undefined) {
      clearTimeout(timer);
      setBlurTimer(undefined);
    }
  };

  return (
    <label class="field autocomplete-host">
      <span>{props.label}</span>
      <input
        ref={input}
        value={props.value}
        placeholder={props.placeholder}
        spellcheck={false}
        autocomplete="off"
        onClick={() => {
          setQuery("");
          setFocused(true);
        }}
        onPointerDown={() => {
          setQuery("");
          setFocused(true);
        }}
        onInput={(event) => {
          props.onValue(event.currentTarget.value);
          setQuery(event.currentTarget.value);
          setFocused(true);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            setFocused(false);
            setQuery("");
          }
        }}
      />
      {props.action}
      <Show when={groups().length > 0}>
        <div class="autocomplete-panel" onMouseDown={keepOpen}>
          <For each={groups()}>
            {([section, values]) => (
              <div class="autocomplete-section">
                <div class="autocomplete-section-label">
                  {autocompleteSectionLabel(section)}
                </div>
                <For each={values}>
                  {(value) => {
                    const description = autocompleteOptionDescription(
                      section,
                      value,
                    );
                    return (
                      <button
                        type="button"
                        class="autocomplete-option"
                        onMouseDown={(event) => {
                          event.preventDefault();
                          props.onValue(value);
                          setFocused(false);
                          setQuery("");
                        }}
                      >
                        <span class="autocomplete-option-label">{value}</span>
                        <Show when={description !== ""}>
                          <span class="autocomplete-option-description">
                            {description}
                          </span>
                        </Show>
                      </button>
                    );
                  }}
                </For>
              </div>
            )}
          </For>
        </div>
      </Show>
    </label>
  );
}

function mainBranchSelectionSavable(selection: BranchSelectionDraft): boolean {
  if (selection.state === "missing") {
    return false;
  }
  const branch = selection.value.branch.trim();
  if (branch.length === 0) {
    return false;
  }
  return (
    selection.value.source === "local" ||
    selection.value.remote.trim().length > 0
  );
}

function filterValues(values: string[], query: string): string[] {
  const needle = query.trim().toLowerCase();
  return values.filter((value) => {
    if (!needle) {
      return true;
    }
    return value.toLowerCase().includes(needle);
  });
}

function filterRefChoices(
  refChoices: RefChoices | null,
  query: string,
  sections: (keyof RefChoices)[],
): AutocompleteGroup[] {
  if (refChoices === null) {
    return [];
  }
  const filtered: AutocompleteGroup[] = [];
  for (const section of sections) {
    const values =
      section === "remote_branches"
        ? filterValues(
            // Compare Refs is a freeform git-ref input, so this is the one UI
            // path allowed to surface the fully-qualified remote ref string.
            refChoices.remote_branches.map((branch) => branch.gitref),
            query,
          )
        : filterValues(refChoices[section], query);
    if (values.length > 0) {
      filtered.push([section, values]);
    }
  }
  return filtered;
}

function filterBranchChoices(
  refChoices: RefChoices | null,
  selection: BranchSelectionDraft,
  query: string,
): AutocompleteGroup[] {
  if (refChoices === null || selection.state === "missing") {
    return [];
  }
  // Branch autocomplete follows the current selection variant: local branches
  // for local selections, remote branch names within the selected remote.
  if (selection.value.source === "local") {
    return filterRefChoices(refChoices, query, ["local_branches"]);
  }
  const values = filterValues(
    listRemoteBranchChoices(refChoices, selection.value.remote),
    query,
  );
  return values.length > 0 ? [["remote_branches", values]] : [];
}

function listRemoteBranchChoices(
  refChoices: RefChoices,
  remoteName: string,
): string[] {
  const normalizedRemote = remoteName.trim();
  if (normalizedRemote.length === 0) {
    return [];
  }
  const branches = new Set<string>();
  for (const value of refChoices.remote_branches) {
    if (value.structured.remote !== normalizedRemote) {
      continue;
    }
    if (value.structured.branch.length > 0) {
      branches.add(value.structured.branch);
    }
  }
  return [...branches].sort();
}

function firstRemoteName(refChoices: RefChoices | null): string {
  const first = refChoices?.remotes[0];
  if (first === undefined) {
    return "";
  }
  return first;
}

function autocompleteOptionDescription(section: string, value: string): string {
  if (section !== "builtins") {
    return "";
  }
  if (!Object.hasOwn(builtinRefDescriptions, value)) {
    throw new Error(`Missing description for built-in ref ${value}.`);
  }
  return builtinRefDescriptions[value as keyof typeof builtinRefDescriptions];
}

function autocompleteSectionLabel(section: string): string {
  if (!Object.hasOwn(refSectionLabels, section)) {
    throw new Error(`Missing label for autocomplete section ${section}.`);
  }
  return refSectionLabels[section];
}
