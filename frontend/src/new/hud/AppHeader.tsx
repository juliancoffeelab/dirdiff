/**
 * Defines the persistent application header.
 *
 * The header owns the application brand and the top-level controls and status
 * projections placed beside it. It does not own workspace state, execute
 * backend operations, or decide what an injected control action means.
 */

/**
 * Renders the sticky application header and brand control.
 *
 * The caller supplies `onSwitchFrontend`, which is invoked only when the user
 * activates the `dirdiff` brand button. The component does not call the action
 * during mounting or reactive updates.
 */
export function AppHeader(props: { onSwitchFrontend: () => void }) {
  return (
    <header class="app-header">
      <div class="app-title-block">
        <div class="app-title-row">
          <div class="app-brand">
            <h1>
              <button
                type="button"
                class="app-brand-switch app-brand-switch-new"
                title="Switch to v_old"
                aria-label="Switch to v_old"
                onClick={props.onSwitchFrontend}
              >
                dirdiff
              </button>
            </h1>
          </div>
        </div>
      </div>
    </header>
  );
}
