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
} from "./api";
import {
  type AutocompleteGroup,
  type ControlsState,
  modeLabels,
  presetTypeLabels,
  presetTypes,
  refSectionLabels,
  topLevelModes,
} from "./fileUtils";

const builtinRefDescriptions: Record<string, string> = {
  head: "Current commit on this branch.",
  index: "Staged snapshot, what the next commit would include.",
  worktree: "Files on disk, including unstaged changes.",
};
const defaultRefsDraft = {
  left: "head~1",
  right: "head",
} as const;

type RemoteBranchSelection = Extract<BranchSelection, { source: "remote" }>;

/** Switch a BranchSelection between local and remote while preserving branch text. */
function selectionWithSource(
  selection: BranchSelection,
  source: BranchSelection["source"],
  refChoices: RefChoices,
): BranchSelection {
  if (source === "local") {
    return { source, branch: selection.branch };
  }
  return {
    source,
    remote:
      selection.source === "remote" && selection.remote.length > 0
        ? selection.remote
        : firstRemoteName(refChoices),
    branch: selection.branch,
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
  selection: BranchSelection,
  branch: string,
): BranchSelection {
  if (selection.source === "local") {
    return { source: "local", branch };
  }
  return { ...selection, branch };
}

export function Controls(props: {
  controls: ControlsState;
  refChoices: RefChoices;
  presetCatalogs: PresetCatalogs | null;
  presetCatalogsPending: boolean;
  presetCatalogsError: unknown;
  onPresetMode: () => Promise<PresetCatalogs | null>;
  onAgainstHead: () => void;
  onPreset: (presetType: PresetType, preset: string) => void;
  onRefs: (left: string, right: string) => void;
  onBranchReview: (
    baseSelection: BranchSelection,
    reviewSelection: BranchSelection,
  ) => void;
  mainBranchSaving: boolean;
  onSaveMainBranch: (selection: BranchSelection) => void | Promise<void>;
}) {
  const [draft, setDraft] = createSignal<ControlsState>(props.controls);
  createEffect(() => setDraft(props.controls));

  const updateDraft = (patch: Partial<ControlsState>) => {
    setDraft((current) => ({ ...current, ...patch }));
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
      return;
    }
    const nextDraft = {
      ...currentDraft,
      presetType,
      preset: catalogs[presetType].default_preset,
    };
    setDraft(nextDraft);
    loadDraft(nextDraft);
  };

  const loadDraft = (value: ControlsState) => {
    if (value.mode === "refs") {
      props.onRefs(value.left, value.right);
      return;
    }
    if (value.mode === "branch-review") {
      props.onBranchReview(value.baseSelection, value.reviewSelection);
      return;
    }
    if (value.mode === "preset") {
      const catalogs = props.presetCatalogs;
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
        !mainBranchSelectionSavable(draft().baseSelection)
      }
      onClick={() => {
        void props.onSaveMainBranch(draft().baseSelection);
      }}
    >
      <Save class="field-icon" aria-hidden="true" />
    </button>
  );

  return (
    <form class="controls" onSubmit={submit}>
      <fieldset class="mode-tabs">
        <legend>View</legend>
        <For each={topLevelModes}>
          {(mode) => (
            <button
              type="button"
              classList={{ "is-active": draft().mode === mode }}
              aria-pressed={draft().mode === mode}
              onClick={() => {
                if (mode === "preset") {
                  const catalogs = props.presetCatalogs;
                  const nextDraft =
                    catalogs === null
                      ? { ...draft(), mode }
                      : {
                          ...draft(),
                          mode,
                          preset: catalogs[draft().presetType].default_preset,
                        };
                  setDraft(nextDraft);
                  if (catalogs === null) {
                    void loadDefaultPresetWhenCatalogArrives(
                      nextDraft.presetType,
                    );
                    return;
                  }
                  loadDraft(nextDraft);
                  return;
                }
                const nextDraft =
                  mode === "refs"
                    ? { ...draft(), mode, ...defaultRefsDraft }
                    : { ...draft(), mode };
                setDraft(nextDraft);
                loadDraft(nextDraft);
              }}
            >
              {modeLabels[mode]}
            </button>
          )}
        </For>
      </fieldset>

      <Show when={draft().mode === "refs"}>
        <AutocompleteField
          label="Old ref"
          value={draft().left}
          groups={(query) =>
            filterRefChoices(props.refChoices, query, [
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
            filterRefChoices(props.refChoices, query, [
              "builtins",
              "local_branches",
              "remote_branches",
            ])
          }
          onValue={(right) => updateDraft({ right })}
        />
      </Show>

      <Show when={draft().mode === "branch-review"}>
        <BranchSourceField
          label="Base remote"
          selection={draft().baseSelection}
          refChoices={props.refChoices}
          onSelection={(baseSelection) => updateDraft({ baseSelection })}
          action={saveMainBranchButton()}
        />
        <AutocompleteField
          label="Base branch"
          value={draft().baseSelection.branch}
          groups={(query) =>
            filterBranchChoices(props.refChoices, draft().baseSelection, query)
          }
          onValue={(branch) =>
            updateDraft({
              baseSelection: selectionWithBranch(draft().baseSelection, branch),
            })
          }
          action={saveMainBranchButton()}
        />
        <BranchSourceField
          label="Branch remote"
          selection={draft().reviewSelection}
          refChoices={props.refChoices}
          onSelection={(reviewSelection) => updateDraft({ reviewSelection })}
        />
        <AutocompleteField
          label="Branch to review"
          value={draft().reviewSelection.branch}
          groups={(query) =>
            filterBranchChoices(
              props.refChoices,
              draft().reviewSelection,
              query,
            )
          }
          onValue={(branch) =>
            updateDraft({
              reviewSelection: selectionWithBranch(
                draft().reviewSelection,
                branch,
              ),
            })
          }
        />
      </Show>

      <Show when={draft().mode === "preset"}>
        <Show when={props.presetCatalogsPending}>
          <p class="status">Loading presets...</p>
        </Show>
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
                  const catalogs = props.presetCatalogs;
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
                  setDraft(nextDraft);
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
        <Show when={props.presetCatalogs}>
          {(catalogs) => (
            <fieldset class="mode-tabs preset-tabs">
              <legend>Presets</legend>
              <For each={catalogs()[draft().presetType].groups}>
                {(group) => (
                  <button
                    type="button"
                    onClick={() => {
                      const nextDraft = { ...draft(), preset: group.name };
                      setDraft(nextDraft);
                      loadDraft(nextDraft);
                    }}
                    classList={{ "is-active": draft().preset === group.name }}
                    aria-pressed={draft().preset === group.name}
                  >
                    {group.display_name}
                  </button>
                )}
              </For>
            </fieldset>
          )}
        </Show>
      </Show>

      <button class="load-button" type="submit">
        Load
      </button>
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
  selection: BranchSelection;
  refChoices: RefChoices;
  onSelection: (selection: BranchSelection) => void;
  action?: JSX.Element;
}) {
  const [focused, setFocused] = createSignal(false);
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const remoteSelection = createMemo((): RemoteBranchSelection | null =>
    props.selection.source === "remote" ? props.selection : null,
  );
  const groups = createMemo(() => {
    const selection = remoteSelection();
    if (!focused() || selection === null) {
      return [];
    }
    const values = filterValues(props.refChoices.remotes, selection.remote);
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
      props.selection.source === "local" ? "remote" : "local",
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
          "is-remote": props.selection.source === "remote",
        }}
      >
        <button
          type="button"
          class="branch-source-toggle"
          aria-pressed={props.selection.source === "remote"}
          onClick={toggleSource}
        >
          {props.selection.source === "remote" ? "Remote" : "Local"}
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

function mainBranchSelectionSavable(selection: BranchSelection): boolean {
  const branch = selection.branch.trim();
  if (branch.length === 0) {
    return false;
  }
  return selection.source === "local" || selection.remote.trim().length > 0;
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
  refChoices: RefChoices,
  query: string,
  sections: (keyof RefChoices)[],
): AutocompleteGroup[] {
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
  refChoices: RefChoices,
  selection: BranchSelection,
  query: string,
): AutocompleteGroup[] {
  // Branch autocomplete follows the current selection variant: local branches
  // for local selections, remote branch names within the selected remote.
  if (selection.source === "local") {
    return filterRefChoices(refChoices, query, ["local_branches"]);
  }
  const values = filterValues(
    listRemoteBranchChoices(refChoices, selection.remote),
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

function firstRemoteName(refChoices: RefChoices): string {
  const first = refChoices.remotes[0];
  if (first === undefined) {
    return "";
  }
  return first;
}

function autocompleteOptionDescription(section: string, value: string): string {
  if (section !== "builtins") {
    return "";
  }
  const description = builtinRefDescriptions[value];
  if (description === undefined) {
    throw new Error(`Missing description for built-in ref ${value}.`);
  }
  return description;
}

function autocompleteSectionLabel(section: string): string {
  if (!Object.hasOwn(refSectionLabels, section)) {
    throw new Error(`Missing label for autocomplete section ${section}.`);
  }
  return refSectionLabels[section];
}
