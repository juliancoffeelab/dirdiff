import {
  For,
  Show,
  createEffect,
  createMemo,
  createSignal,
  onCleanup,
  onMount,
} from "solid-js";
import type { PresetCatalogs, PresetType, RefChoices } from "./api";
import {
  type AutocompleteGroup,
  type BranchSource,
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
    baseSource: BranchSource,
    baseRemote: string,
    baseBranch: string,
    branchSource: BranchSource,
    branchRemote: string,
    reviewBranch: string,
  ) => void;
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
      props.onBranchReview(
        value.baseSource,
        value.baseRemote,
        value.baseBranch,
        value.branchSource,
        value.branchRemote,
        value.reviewBranch,
      );
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
              "locals",
              "remotes",
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
              "locals",
              "remotes",
            ])
          }
          onValue={(right) => updateDraft({ right })}
        />
      </Show>

      <Show when={draft().mode === "branch-review"}>
        <BranchSourceField
          label="Base remote"
          source={draft().baseSource}
          remote={draft().baseRemote}
          remoteChoices={props.refChoices.remote_names}
          onSource={(baseSource) =>
            updateDraft({
              baseSource,
              baseRemote:
                baseSource === "remote" && draft().baseRemote.length === 0
                  ? firstRemoteName(props.refChoices)
                  : draft().baseRemote,
            })
          }
          onRemote={(baseRemote) => updateDraft({ baseRemote })}
        />
        <AutocompleteField
          label="Base branch"
          value={draft().baseBranch}
          groups={(query) =>
            filterBranchChoices(
              props.refChoices,
              draft().baseSource,
              draft().baseRemote,
              query,
            )
          }
          onValue={(baseBranch) => updateDraft({ baseBranch })}
        />
        <BranchSourceField
          label="Branch remote"
          source={draft().branchSource}
          remote={draft().branchRemote}
          remoteChoices={props.refChoices.remote_names}
          onSource={(branchSource) =>
            updateDraft({
              branchSource,
              branchRemote:
                branchSource === "remote" && draft().branchRemote.length === 0
                  ? firstRemoteName(props.refChoices)
                  : draft().branchRemote,
            })
          }
          onRemote={(branchRemote) => updateDraft({ branchRemote })}
        />
        <AutocompleteField
          label="Branch to review"
          value={draft().reviewBranch}
          groups={(query) =>
            filterBranchChoices(
              props.refChoices,
              draft().branchSource,
              draft().branchRemote,
              query,
            )
          }
          onValue={(reviewBranch) => updateDraft({ reviewBranch })}
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

function BranchSourceField(props: {
  label: string;
  source: BranchSource;
  remote: string;
  remoteChoices: string[];
  onSource: (source: BranchSource) => void;
  onRemote: (remote: string) => void;
}) {
  let input: HTMLInputElement | undefined;
  const [focused, setFocused] = createSignal(false);
  const [blurTimer, setBlurTimer] = createSignal<number | undefined>();
  const groups = createMemo(() => {
    if (!focused() || props.source !== "remote") {
      return [];
    }
    const values = filterValues(props.remoteChoices, props.remote);
    return values.length ? [["remote_names", values] as AutocompleteGroup] : [];
  });

  onMount(() => {
    if (input === undefined) {
      return;
    }
    const open = () => setFocused(true);
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
    props.onSource(props.source === "local" ? "remote" : "local");
    setFocused(props.source === "local");
  };

  return (
    <div class="field branch-source-field autocomplete-host">
      <span>{props.label}</span>
      <div
        classList={{
          "branch-source-control": true,
          "is-remote": props.source === "remote",
        }}
      >
        <button
          type="button"
          class="branch-source-toggle"
          aria-pressed={props.source === "remote"}
          onClick={toggleSource}
        >
          {props.source === "remote" ? "Remote" : "Local"}
        </button>
        <Show when={props.source === "remote"}>
          <input
            ref={input}
            class="branch-source-remote"
            value={props.remote}
            aria-label={props.label}
            placeholder="remote"
            spellcheck={false}
            autocomplete="off"
            onClick={() => setFocused(true)}
            onPointerDown={() => setFocused(true)}
            onInput={(event) => {
              props.onRemote(event.currentTarget.value);
              setFocused(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                setFocused(false);
              }
            }}
          />
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
                        props.onRemote(value);
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
    </div>
  );
}

function AutocompleteField(props: {
  label: string;
  value: string;
  groups: (query: string) => AutocompleteGroup[];
  onValue: (value: string) => void;
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
    const values = filterValues(refChoices[section], query);
    if (values.length > 0) {
      filtered.push([section, values]);
    }
  }
  return filtered;
}

function filterBranchChoices(
  refChoices: RefChoices,
  source: BranchSource,
  remoteName: string,
  query: string,
): AutocompleteGroup[] {
  if (source === "local") {
    return filterRefChoices(refChoices, query, ["locals"]);
  }
  const values = filterValues(
    listRemoteBranchChoices(refChoices, remoteName),
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
  const prefix = `${normalizedRemote}/`;
  return [
    ...new Set(
      refChoices.remotes
        .filter((value) => value.startsWith(prefix))
        .map((value) => value.slice(prefix.length))
        .filter((value) => value.length > 0),
    ),
  ].sort();
}

function firstRemoteName(refChoices: RefChoices): string {
  const first = refChoices.remote_names[0];
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
