function ControlsSlot() {
  return (
    <Show when={preferences() !== null}>
      <div class="repo-fold-controls">
        <Show
          when={ui.displayFiles().length > 0}
          fallback={
            <>
              <button type="button" disabled>
                Fold all
              </button>
              <button type="button" disabled>
                Show all
              </button>
            </>
          }
        >
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
        </Show>
      </div>
      <div class="diff-workspace" />
    </Show>
  );
}
