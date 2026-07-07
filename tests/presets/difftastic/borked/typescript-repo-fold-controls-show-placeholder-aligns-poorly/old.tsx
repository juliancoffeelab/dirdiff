function ControlsSlot() {
  return (
    <Show when={preferences() !== null}>
      <Show when={ui.displayFiles().length > 0}>
        <div class="repo-fold-controls">
          <button
            type="button"
            onClick={() => ui.setAllFilesExpanded(false)}
          >
            Fold all
          </button>
          <button
            type="button"
            onClick={() => ui.setAllFilesExpanded(true)}
          >
            Show all
          </button>
        </div>
      </Show>
      <div class="diff-workspace" />
    </Show>
  );
}
