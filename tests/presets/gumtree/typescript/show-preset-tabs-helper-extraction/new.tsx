function PresetControls(props) {
  const renderPresetTabs = () => {
    const catalogs = props.presetCatalogs;
    if (catalogs === null) {
      return null;
    }
    return (
      <fieldset class="mode-tabs preset-tabs">
        <legend>Presets</legend>
        <For each={catalogs[draft().presetType].groups}>
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
    );
  };

  return (
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
            <button type="button">{presetTypeLabels[presetType]}</button>
          )}
        </For>
      </fieldset>
      {renderPresetTabs()}
    </Show>
  );
}
